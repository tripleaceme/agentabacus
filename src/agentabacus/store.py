"""DuckDB store: one file, no server, no daemon.

Every write is an idempotent upsert, because the collector is incremental and a
request's content-block lines can straddle two collection runs. Token columns
merge with `greatest()` for the same reason the adapter aggregates with MAX:
a value only ever grows within a request, so re-reading a line is a no-op
rather than a double count.

This file is the archive. Claude Code garbage-collects transcripts (`.last-cleanup`
is stamped on every run, and slugs with a `memory/` dir but no `.jsonl` are what
that looks like afterwards), so once cleanup fires, this database is the only
remaining copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import pricing
from .config import DB_PATH, ensure_home
from .schema import DDL, SCHEMA_VERSION, VIEWS, Batch


def connect(db_path: Path | None = None, read_only: bool = False):
    path = db_path or DB_PATH
    if not read_only:
        ensure_home()
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        init(conn)
    return conn


def init(conn) -> None:
    conn.execute(DDL)
    conn.execute(VIEWS)
    pricing.sync(conn)
    conn.execute(
        "INSERT INTO _meta VALUES ('schema_version', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [str(SCHEMA_VERSION)],
    )


# --------------------------------------------------------------------------
# watermarks
# --------------------------------------------------------------------------


def watermark(conn, path: Path) -> tuple[int, float, int]:
    """(byte_offset, mtime, size) recorded for this file, or zeros."""
    row = conn.execute(
        "SELECT byte_offset, mtime, size_bytes FROM _files WHERE path = ?",
        [str(path)],
    ).fetchone()
    return (row[0] or 0, row[1] or 0.0, row[2] or 0) if row else (0, 0.0, 0)


def set_watermark(conn, path: Path, source: str, byte_offset: int) -> None:
    stat = path.stat()
    conn.execute(
        """
        INSERT INTO _files VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (path) DO UPDATE SET
            mtime        = excluded.mtime,
            size_bytes   = excluded.size_bytes,
            byte_offset  = excluded.byte_offset,
            collected_at = excluded.collected_at
        """,
        [
            str(path),
            source,
            stat.st_mtime,
            stat.st_size,
            byte_offset,
            datetime.now(timezone.utc).replace(tzinfo=None),
        ],
    )


# --------------------------------------------------------------------------
# upserts
# --------------------------------------------------------------------------

_TOKEN_COLS = (
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "web_search_requests",
    "web_fetch_requests",
)

_TURN_MERGE = ",\n            ".join(
    f"{c} = greatest({c}, excluded.{c})" for c in _TOKEN_COLS
)


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _stage(conn, table: str, rows: list, width: int) -> str:
    """Bulk-load rows into a temp table shaped like `table`.

    Rows go out as newline-delimited JSON and come back through `read_json`,
    which is DuckDB's vectorised path. This is not premature optimisation --
    it is the difference between a usable tool and one that looks hung:

        7,612 rows, measured                 duckdb 1.5.5
          conn.executemany(...)                56.5 s
          multi-row INSERT ... VALUES          41.0 s
          NDJSON + read_json                    0.13 s   <-- ~450x

    The cost is in DuckDB's *Python parameter binding*, roughly 0.9 ms per
    row, not in the insert. Batching statements barely helps because the
    binding still happens per row; the only real fix is to stop binding row
    parameters at all. A single 236 MB transcript took 82 s to write and
    1.8 s to parse before this change.

    Column names and types are read back from the target table, so this stays
    correct automatically when schema.py changes.
    """
    name = f"_stg_{table}"
    conn.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM {table} LIMIT 0")

    described = conn.execute(f"DESCRIBE {name}").fetchall()
    columns = [d[0] for d in described]
    types = {d[0]: d[1] for d in described}

    handle, path = tempfile.mkstemp(suffix=".jsonl", prefix="agentabacus_")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(
                    json.dumps(
                        {c: _jsonable(v) for c, v in zip(columns, row)},
                        default=str,
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        # as_posix(): DuckDB wants forward slashes in paths on Windows too.
        sql_path = Path(path).as_posix().replace("'", "''")
        conn.execute(
            f"INSERT INTO {name} SELECT {', '.join(columns)} "
            f"FROM read_json('{sql_path}', columns={types!r})"
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return name


def write_batch(conn, batch: Batch) -> dict[str, int]:
    counts = {"sessions": 0, "turns": 0, "tool_calls": 0, "prompts": 0}

    for s in batch.sessions:
        conn.execute(
            """
            INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (session_id, thread_id) DO UPDATE SET
                cwd               = COALESCE(excluded.cwd, cwd),
                project_slug      = COALESCE(excluded.project_slug, project_slug),
                git_branch        = COALESCE(excluded.git_branch, git_branch),
                cli_version       = COALESCE(excluded.cli_version, cli_version),
                entrypoint        = COALESCE(excluded.entrypoint, entrypoint),
                agent_type        = COALESCE(excluded.agent_type, agent_type),
                parent_session_id = COALESCE(excluded.parent_session_id, parent_session_id),
                is_subagent       = sessions.is_subagent OR excluded.is_subagent,
                started_at        = least(COALESCE(started_at, excluded.started_at),
                                          COALESCE(excluded.started_at, started_at)),
                ended_at          = greatest(COALESCE(ended_at, excluded.ended_at),
                                             COALESCE(excluded.ended_at, ended_at)),
                transcript_path   = COALESCE(excluded.transcript_path, transcript_path)
            """,
            [
                s.session_id, s.thread_id, s.source, s.cwd, s.project_slug,
                s.git_branch, s.cli_version, s.entrypoint, s.agent_type,
                s.parent_session_id, s.is_subagent, s.started_at, s.ended_at,
                s.transcript_path,
            ],
        )
        counts["sessions"] += 1

    if batch.turns:
        rows = [
            (
                t.request_id, t.session_id, t.thread_id, t.source, t.model_id,
                t.ts, t.effort, t.service_tier, t.speed, t.input_tokens,
                t.output_tokens, t.thinking_tokens, t.cache_read_tokens,
                t.cache_write_5m_tokens, t.cache_write_1h_tokens,
                t.web_search_requests, t.web_fetch_requests, t.block_lines,
            )
            for t in batch.turns
        ]
        stg = _stage(conn, "turns", rows, 18)
        conn.execute(
            f"""
            INSERT INTO turns SELECT * FROM {stg}
            ON CONFLICT (request_id) DO UPDATE SET
            {_TURN_MERGE},
            block_lines  = turns.block_lines + excluded.block_lines,
            model_id     = COALESCE(excluded.model_id, model_id),
            effort       = COALESCE(excluded.effort, effort),
            service_tier = COALESCE(excluded.service_tier, service_tier),
            speed        = COALESCE(excluded.speed, speed),
            ts           = least(COALESCE(ts, excluded.ts), COALESCE(excluded.ts, ts))
            """
        )
        counts["turns"] = len(rows)

    if batch.tool_calls:
        rows = [
            (
                c.tool_use_id, c.session_id, c.thread_id, c.source, c.request_id,
                c.ts, c.tool_name, c.target, c.is_error, c.result_chars,
            )
            for c in batch.tool_calls
        ]
        stg = _stage(conn, "tool_calls", rows, 10)
        conn.execute(
            f"""
            INSERT INTO tool_calls SELECT * FROM {stg}
            ON CONFLICT (tool_use_id) DO UPDATE SET
                is_error     = COALESCE(excluded.is_error, is_error),
                result_chars = COALESCE(excluded.result_chars, result_chars),
                tool_name    = COALESCE(excluded.tool_name, tool_name),
                target       = COALESCE(excluded.target, target),
                request_id   = COALESCE(excluded.request_id, request_id)
            """
        )
        counts["tool_calls"] = len(rows)

    if batch.prompts:
        seen: set[str] = set()
        rows = []
        for p in batch.prompts:
            key = f"{p.prompt_sha256}|{p.session_id}|{p.ts}"
            prompt_id = hashlib.sha256(key.encode()).hexdigest()[:32]
            if prompt_id in seen:
                continue  # ON CONFLICT cannot update the same row twice
            seen.add(prompt_id)
            rows.append(
                (
                    prompt_id, p.prompt_sha256, p.session_id, p.source, p.ts,
                    p.project, p.char_len,
                )
            )
        stg = _stage(conn, "prompts", rows, 7)
        conn.execute(
            f"INSERT INTO prompts SELECT * FROM {stg} ON CONFLICT (prompt_id) DO NOTHING"
        )
        counts["prompts"] = len(rows)

    return counts
