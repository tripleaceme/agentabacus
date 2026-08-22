"""Unverified adapters must not contribute to headline numbers.

A missing number prompts a question. A wrong number gets believed. The Codex
adapter reported 4,318 requests carrying 291 billion input tokens -- about
67 million tokens per request -- and those silently inflated a user's totals.

So: experimental sources are collected and stored, but excluded from reports
unless explicitly asked for.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentabacus import report, store  # noqa: E402
from agentabacus.adapters import EXPERIMENTAL  # noqa: E402
from agentabacus.discovery import _codex  # noqa: E402
from agentabacus.schema import Batch, Turn  # noqa: E402

TS = datetime(2026, 8, 20, 10, 0, 0)


def _seed(tmp_path):
    conn = store.connect(tmp_path / "t.duckdb")
    turns = [
        Turn(request_id="ok_1", session_id="s1", thread_id="main",
             source="claude_code", model_id="claude-opus-5", ts=TS,
             output_tokens=1_000),
        # the implausible shape actually observed from the codex adapter
        Turn(request_id="codex_1", session_id="c1", thread_id="main",
             source="codex", model_id=None, ts=TS, input_tokens=67_000_000),
    ]
    store.write_batch(conn, Batch(turns=turns))
    return conn


def test_experimental_source_is_excluded_by_default(tmp_path):
    conn = _seed(tmp_path)
    requests, *_ = report.summary(conn, since="all")
    assert requests == 1, "codex rows leaked into the default totals"


def test_experimental_tokens_do_not_inflate_totals(tmp_path):
    conn = _seed(tmp_path)
    row = report.summary(conn, since="all")
    input_tokens = row[3]
    assert input_tokens == 0, f"67M bogus input tokens leaked in: {input_tokens}"


def test_experimental_included_when_asked_for(tmp_path):
    conn = _seed(tmp_path)
    requests, *_ = report.summary(conn, since="all", include_experimental=True)
    assert requests == 2


def test_explicit_source_overrides_the_exclusion(tmp_path):
    """--source codex is a deliberate request for that data."""
    conn = _seed(tmp_path)
    requests, *_ = report.summary(conn, since="all", source="codex")
    assert requests == 1


def test_breakdown_also_excludes(tmp_path):
    conn = _seed(tmp_path)
    dims = [r[0] for r in report.breakdown(conn, "source", since="all")]
    assert dims == ["claude_code"]


def test_findings_report_what_was_skipped(tmp_path):
    """The user must be told these files were seen, not left guessing."""
    conn = _seed(tmp_path)
    rows = report.experimental_findings(conn)
    assert rows, "nothing reported; the user would think codex was ignored"
    source, _files, requests = rows[0]
    assert source == "codex"
    assert requests == 1


def test_codex_discovery_ignores_everything_outside_sessions(tmp_path):
    """The fallback rglob over the config root picked up plugin test fixtures
    whose token counts are invented. Absent sessions/ means find nothing."""
    root = tmp_path / ".codex"
    (root / ".tmp" / "plugins" / "fixtures").mkdir(parents=True)
    (root / ".tmp" / "plugins" / "fixtures" / "responses.jsonl").write_text("{}\n")
    assert list(_codex(root)) == [], "picked up files outside sessions/"

    sessions = root / "sessions" / "2026" / "08"
    sessions.mkdir(parents=True)
    (sessions / "real.jsonl").write_text("{}\n")
    (root / "sessions" / ".hidden").mkdir()
    (root / "sessions" / ".hidden" / "cache.jsonl").write_text("{}\n")

    found = list(_codex(root))
    assert [f.path.name for f in found] == ["real.jsonl"]


def test_codex_is_currently_marked_experimental():
    """Remove codex from EXPERIMENTAL only once its output has been checked
    against real Codex transcripts -- then delete this test."""
    assert "codex" in EXPERIMENTAL
    assert "claude_code" not in EXPERIMENTAL
