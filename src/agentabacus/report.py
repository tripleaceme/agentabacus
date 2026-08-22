"""Reports.

All of these read `turns_costed`, the view that joins tokens to effective-dated
pricing. Nothing here recomputes cost by hand -- there is one definition of
"cost", it lives in schema.py, and both the CLI and any downstream export share it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


_WINDOW = re.compile(r"^(\d+)\s*([dhwm])$", re.I)
_UNITS = {"h": "hours", "d": "days", "w": "weeks", "m": "days"}


def cutoff(since: str | None) -> datetime | None:
    """'30d' / '12h' / '4w' / 'all' -> a UTC-naive datetime, or None."""
    if not since or since.lower() in {"all", "0"}:
        return None
    match = _WINDOW.match(since.strip())
    if not match:
        raise ValueError(f"bad --since value {since!r}; use e.g. 7d, 24h, 4w, or all")
    amount, unit = int(match.group(1)), match.group(2).lower()
    amount = amount * 30 if unit == "m" else amount
    delta = timedelta(**{_UNITS[unit]: amount})
    return (datetime.now(timezone.utc) - delta).replace(tzinfo=None)


def _where(since: str | None, source: str | None, include_subagents: bool):
    clauses, params = ["1=1"], []
    at = cutoff(since)
    if at is not None:
        clauses.append("t.ts >= ?")
        params.append(at)
    if source:
        clauses.append("t.source = ?")
        params.append(source)
    if not include_subagents:
        clauses.append("t.thread_id = 'main'")
    return " AND ".join(clauses), params



def summary(conn, since=None, source=None, include_subagents=True):
    where, params = _where(since, source, include_subagents)
    return conn.execute(
        f"""
        SELECT
            COUNT(*)                                   AS requests,
            COUNT(DISTINCT t.session_id)               AS sessions,
            SUM(t.block_lines)                         AS jsonl_lines,
            SUM(t.input_tokens)                        AS input_tokens,
            SUM(t.output_tokens)                       AS output_tokens,
            SUM(t.thinking_tokens)                     AS thinking_tokens,
            SUM(t.cache_read_tokens)                   AS cache_read,
            SUM(t.cache_write_5m_tokens)               AS cache_write_5m,
            SUM(t.cache_write_1h_tokens)               AS cache_write_1h,
            SUM(t.cost_usd)                            AS cost_usd,
            SUM(CASE WHEN NOT t.priced THEN 1 ELSE 0 END) AS unpriced_requests
        FROM turns_costed t WHERE {where}
        """,
        params,
    ).fetchone()


_DIMENSIONS = {
    "model": "COALESCE(t.model_id, '(unknown)')",
    "source": "t.source",
    "project": "COALESCE(s.cwd, s.project_slug, '(unknown)')",
    "branch": "COALESCE(s.git_branch, '(none)')",
    "day": "CAST(t.ts AS DATE)",
    "effort": "COALESCE(t.effort, '(none)')",
    "speed": "COALESCE(t.speed, 'standard')",
    "thread": "CASE WHEN t.thread_id = 'main' THEN 'main loop' ELSE 'subagents' END",
}


def breakdown(conn, dimension="model", since=None, source=None,
              include_subagents=True, limit=25):
    if dimension not in _DIMENSIONS:
        raise ValueError(
            f"unknown --by {dimension!r}; choose from {', '.join(sorted(_DIMENSIONS))}"
        )
    where, params = _where(since, source, include_subagents)
    expr = _DIMENSIONS[dimension]
    rows = conn.execute(
        f"""
        SELECT {expr} AS dim,
               COUNT(*)                 AS requests,
               SUM(t.input_tokens)      AS input_tokens,
               SUM(t.output_tokens)     AS output_tokens,
               SUM(t.cache_read_tokens) AS cache_read,
               SUM(t.cost_usd)          AS cost_usd
        FROM turns_costed t
        LEFT JOIN sessions s
               ON s.session_id = t.session_id AND s.thread_id = t.thread_id
        WHERE {where}
        GROUP BY 1 ORDER BY cost_usd DESC NULLS LAST LIMIT {int(limit)}
        """,
        params,
    ).fetchall()
    return rows


def top_sessions(conn, since=None, source=None, include_subagents=True, limit=10):
    where, params = _where(since, source, include_subagents)
    return conn.execute(
        f"""
        SELECT t.session_id, t.thread_id,
               COALESCE(s.cwd, s.project_slug, '(unknown)') AS project,
               MIN(t.ts) AS started,
               COUNT(*)  AS requests,
               SUM(t.cost_usd) AS cost_usd
        FROM turns_costed t
        LEFT JOIN sessions s
               ON s.session_id = t.session_id AND s.thread_id = t.thread_id
        WHERE {where}
        GROUP BY 1,2,3 ORDER BY cost_usd DESC NULLS LAST LIMIT {int(limit)}
        """,
        params,
    ).fetchall()


def cache_report(conn, since=None, source=None, include_subagents=True):
    """Cache efficiency, with the 1h/5m split kept visible.

    A 1-hour write costs 2x base input; a 5-minute write 1.25x. Tools that
    collapse them into one number can't show you which one you're paying for.
    """
    where, params = _where(since, source, include_subagents)
    return conn.execute(
        f"""
        SELECT COALESCE(t.model_id, '(unknown)') AS model,
               SUM(t.cache_read_tokens)      AS read_tokens,
               SUM(t.cache_write_5m_tokens)  AS write_5m,
               SUM(t.cache_write_1h_tokens)  AS write_1h,
               CASE WHEN SUM(t.cache_read_tokens + t.cache_write_5m_tokens
                           + t.cache_write_1h_tokens) = 0 THEN NULL
                    ELSE SUM(t.cache_read_tokens) * 1.0
                       / SUM(t.cache_read_tokens + t.cache_write_5m_tokens
                           + t.cache_write_1h_tokens) END AS read_share,
               SUM(t.cost_cache_read)     AS cost_read,
               SUM(t.cost_cache_write_5m) AS cost_write_5m,
               SUM(t.cost_cache_write_1h) AS cost_write_1h
        FROM turns_costed t WHERE {where}
        GROUP BY 1 ORDER BY (cost_read + cost_write_5m + cost_write_1h) DESC NULLS LAST
        """,
        params,
    ).fetchall()


def tool_report(conn, since=None, source=None, include_subagents=True, limit=20):
    """Tool-call outcomes -- the effectiveness side of the ledger.

    Cost answers 'how much did I spend'. This starts on 'did the agent's
    actions actually work', which is the question with no good open answer today.
    """
    clauses, params = ["1=1"], []
    at = cutoff(since)
    if at is not None:
        clauses.append("c.ts >= ?")
        params.append(at)
    if source:
        clauses.append("c.source = ?")
        params.append(source)
    if not include_subagents:
        clauses.append("c.thread_id = 'main'")
    where = " AND ".join(clauses)
    return conn.execute(
        f"""
        SELECT COALESCE(c.tool_name, '(unknown)') AS tool,
               COUNT(*)                                        AS calls,
               SUM(CASE WHEN c.is_error THEN 1 ELSE 0 END)      AS errors,
               SUM(CASE WHEN c.is_error IS NULL THEN 1 ELSE 0 END) AS unresolved,
               CASE WHEN COUNT(c.is_error) = 0 THEN NULL
                    ELSE SUM(CASE WHEN c.is_error THEN 1 ELSE 0 END) * 1.0
                       / COUNT(c.is_error) END                  AS error_rate,
               CAST(AVG(c.result_chars) AS BIGINT)              AS avg_result_chars
        FROM tool_calls c WHERE {where}
        GROUP BY 1 ORDER BY calls DESC LIMIT {int(limit)}
        """,
        params,
    ).fetchall()


def unpriced_models(conn):
    """Models observed in the data with no pricing row.

    This is the alarm for "a new model shipped and the price table is stale" --
    the failure that otherwise makes cost reports quietly wrong.
    """
    return conn.execute(
        """
        SELECT t.model_id, COALESCE(t.speed,'standard') AS speed,
               COUNT(*) AS requests, MIN(t.ts) AS first_seen, MAX(t.ts) AS last_seen
        FROM turns_costed t
        WHERE NOT t.priced AND NOT t.non_billable
        GROUP BY 1,2 ORDER BY requests DESC
        """
    ).fetchall()
