"""Round-trip tests for the bulk-load path.

Rows reach DuckDB as newline-delimited JSON rather than bound parameters,
because parameter binding costs ~0.9 ms/row and made a 236 MB transcript take
82 s to write instead of 0.4 s. That speed is worth having, but serialising
through JSON is exactly the kind of change that can quietly mangle a NULL, a
timestamp, or a quote and not show up until someone's numbers look odd.

So: pin the awkward values.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentabacus import store  # noqa: E402
from agentabacus.schema import Batch, Session, ToolCall, Turn  # noqa: E402


def _conn(tmp_path):
    return store.connect(tmp_path / "t.duckdb")


def _turn(**kw):
    base = dict(
        request_id="req_1", session_id="s1", thread_id="main",
        source="claude_code", model_id="claude-opus-5",
        ts=datetime(2026, 8, 18, 12, 30, 45), input_tokens=1, output_tokens=2,
    )
    base.update(kw)
    return Turn(**base)


def test_timestamps_survive_as_timestamps(tmp_path):
    conn = _conn(tmp_path)
    store.write_batch(conn, Batch(turns=[_turn()]))
    kind, value = conn.execute("SELECT typeof(ts), ts FROM turns").fetchone()
    assert kind == "TIMESTAMP", f"ts degraded to {kind}"
    assert value == datetime(2026, 8, 18, 12, 30, 45)


def test_nulls_stay_null_and_do_not_become_strings(tmp_path):
    """The trap: a None serialised badly comes back as the text 'None'."""
    conn = _conn(tmp_path)
    store.write_batch(conn, Batch(turns=[_turn(model_id=None, ts=None, effort=None)]))
    model, ts, effort = conn.execute(
        "SELECT model_id, ts, effort FROM turns"
    ).fetchone()
    assert model is None and ts is None and effort is None
    assert conn.execute(
        "SELECT count(*) FROM turns WHERE model_id = 'None'"
    ).fetchone()[0] == 0


def test_booleans_keep_three_states(tmp_path):
    """is_error is True / False / NULL, and NULL means 'no result seen yet'.
    Collapsing NULL into False would silently invent successes."""
    conn = _conn(tmp_path)
    calls = [
        ToolCall(tool_use_id=f"t{i}", session_id="s1", thread_id="main",
                 source="claude_code", is_error=v)
        for i, v in enumerate((True, False, None))
    ]
    store.write_batch(conn, Batch(tool_calls=calls))
    got = dict(conn.execute(
        "SELECT tool_use_id, is_error FROM tool_calls ORDER BY tool_use_id"
    ).fetchall())
    assert got == {"t0": True, "t1": False, "t2": None}


def test_awkward_strings_round_trip(tmp_path):
    """Tool targets are raw shell commands: quotes, heredocs, newlines,
    backslashes, unicode. JSON must carry all of it verbatim."""
    nasty = [
        """git commit -F - <<'EOF'\nmulti\nline\nEOF""",
        'echo "double" and \'single\' quotes',
        "path\\with\\backslashes",
        "unicode: café — 日本語 — 🧮",
        "sql injection'); DROP TABLE turns;--",
    ]
    conn = _conn(tmp_path)
    calls = [
        ToolCall(tool_use_id=f"t{i}", session_id="s1", thread_id="main",
                 source="claude_code", tool_name="Bash", target=text)
        for i, text in enumerate(nasty)
    ]
    store.write_batch(conn, Batch(tool_calls=calls))

    got = [r[0] for r in conn.execute(
        "SELECT target FROM tool_calls ORDER BY tool_use_id"
    ).fetchall()]
    assert got == nasty
    # the injection string must be data, not executed
    assert conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 5


def test_upsert_merges_with_max_not_sum(tmp_path):
    """Re-collecting a file must not double the tokens. Token columns merge
    with greatest(), so writing the same request twice is a no-op."""
    conn = _conn(tmp_path)
    store.write_batch(conn, Batch(turns=[_turn(output_tokens=500)]))
    store.write_batch(conn, Batch(turns=[_turn(output_tokens=500)]))
    total, rows = conn.execute(
        "SELECT sum(output_tokens), count(*) FROM turns"
    ).fetchone()
    assert rows == 1
    assert total == 500, f"re-collection double-counted: {total}"


def test_larger_batch_lands_completely(tmp_path):
    conn = _conn(tmp_path)
    turns = [_turn(request_id=f"req_{i}", output_tokens=i) for i in range(5000)]
    store.write_batch(conn, Batch(turns=turns))
    rows, total = conn.execute(
        "SELECT count(*), sum(output_tokens) FROM turns"
    ).fetchone()
    assert rows == 5000
    assert total == sum(range(5000))


def test_session_and_empty_batch(tmp_path):
    conn = _conn(tmp_path)
    store.write_batch(conn, Batch())  # must not raise
    store.write_batch(conn, Batch(sessions=[
        Session(session_id="s1", thread_id="main", source="claude_code",
                cwd="/tmp/proj", is_subagent=False,
                started_at=datetime(2026, 8, 18, 9, 0, 0)),
    ]))
    cwd, sub = conn.execute(
        "SELECT cwd, is_subagent FROM sessions"
    ).fetchone()
    assert cwd == "/tmp/proj"
    assert sub is False
