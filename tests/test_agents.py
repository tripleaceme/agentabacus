"""Supported or not. There is no third state.

An agent either has an adapter -- in which case its logs are collected -- or it
does not, in which case agentabacus only reports that the logs exist and points
at CONTRIBUTING. Nothing half-parsed ever reaches the archive or a report.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentabacus import agents, store  # noqa: E402
from agentabacus.adapters import REGISTRY  # noqa: E402
from agentabacus.discovery import discover  # noqa: E402
from agentabacus.schema import Batch, Turn  # noqa: E402


def test_only_agents_with_adapters_are_marked_supported():
    """agents.py and the adapter registry must not drift apart: claiming
    support without an adapter would make `collect --x` silently do nothing."""
    for agent in agents.AGENTS:
        if agent.supported:
            assert agent.name in REGISTRY, f"{agent.name} claims support, no adapter"
        else:
            assert agent.name not in REGISTRY, f"{agent.name} has an adapter, mark it supported"


def test_claude_code_is_the_supported_one_today():
    assert [a.name for a in agents.SUPPORTED] == ["claude_code"]


def test_every_agent_has_a_unique_flag():
    flags = [a.flag for a in agents.AGENTS]
    assert len(flags) == len(set(flags))


def test_unsupported_agents_are_never_discovered(tmp_path, monkeypatch):
    """Even with logs sitting right there, an agent without an adapter
    contributes no files -- so nothing of theirs can reach the archive."""
    codex = tmp_path / ".codex" / "sessions"
    codex.mkdir(parents=True)
    (codex / "rollout.jsonl").write_text('{"usage":{"input_tokens":5}}\n')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))

    assert discover() == []


def test_unsupported_agent_is_still_detected(tmp_path, monkeypatch):
    """Detected, so the user is told -- just not parsed."""
    root = tmp_path / ".codex"
    (root / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(root))

    detected = {a.name for a, _ in agents.installed(supported=False)}
    assert "codex" in detected


def test_detection_reports_no_usage_numbers():
    """`installed()` deliberately returns only the agent and its path.

    If this ever grows a count, ask whether the user can act on it. Half a
    number about logs we cannot read is noise, not insight.
    """
    for entry in agents.installed():
        assert len(entry) == 2
        agent, root = entry
        assert isinstance(agent, agents.Agent)
        assert isinstance(root, Path)


def test_missing_agent_root_is_not_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "definitely-absent"))
    assert agents.BY_NAME["codex"].root() is None


def test_env_var_overrides_default_root(tmp_path, monkeypatch):
    root = tmp_path / "elsewhere"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    assert agents.BY_NAME["claude_code"].root() == root


def test_opening_the_archive_drops_rows_from_unsupported_agents(tmp_path):
    """Upgrade path.

    Earlier versions shipped an unfinished Codex adapter that reported
    billions of tokens per session. Anyone who ran one has that data stored,
    and it would skew their totals forever. Opening the archive removes it.
    """
    db = tmp_path / "t.duckdb"
    conn = store.connect(db)
    store.write_batch(conn, Batch(turns=[
        Turn(request_id="ok", session_id="s", thread_id="main",
             source="claude_code", model_id="claude-opus-5",
             ts=datetime(2026, 8, 20, 10, 0), output_tokens=10),
    ]))
    # simulate what an older version left behind
    conn.execute(
        "INSERT INTO turns (request_id, session_id, thread_id, source, "
        "input_tokens) VALUES ('codex:1', 'c', 'main', 'codex', 67000000)"
    )
    assert conn.execute("SELECT count(*) FROM turns").fetchone()[0] == 2
    conn.close()

    conn = store.connect(db)          # reopening runs the cleanup
    rows = conn.execute("SELECT source FROM turns").fetchall()
    assert rows == [("claude_code",)], f"stale rows survived: {rows}"


def test_supported_agent_rows_are_untouched_by_the_cleanup(tmp_path):
    db = tmp_path / "t.duckdb"
    conn = store.connect(db)
    store.write_batch(conn, Batch(turns=[
        Turn(request_id=f"r{i}", session_id="s", thread_id="main",
             source="claude_code", model_id="claude-opus-5",
             ts=datetime(2026, 8, 20, 10, 0), output_tokens=10)
        for i in range(25)
    ]))
    conn.close()
    conn = store.connect(db)
    assert conn.execute("SELECT count(*) FROM turns").fetchone()[0] == 25
