"""Every registered adapter must pass the contract, and the contract must
actually catch the mistakes it claims to catch.

The second half matters more than the first. A validator that never rejects
anything is worse than none, because it makes a green tick mean nothing. So
each check is fed the exact failure it exists to prevent.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from agentabacus.adapters import REGISTRY  # noqa: E402
from agentabacus.schema import Turn  # noqa: E402
from validate_adapters import Failure, check, validate_turns  # noqa: E402

TS = datetime(2026, 8, 20, 10, 0, 0)


def _turn(**kw):
    base = dict(
        request_id="req_1", session_id="s", thread_id="main",
        source="x", model_id="claude-opus-5", ts=TS,
        input_tokens=100, output_tokens=200, block_lines=1,
    )
    base.update(kw)
    return Turn(**base)


@pytest.mark.parametrize("source", sorted(REGISTRY))
def test_registered_adapter_passes_the_contract(source):
    """Runs on every adapter, including any a contributor adds."""
    check(source)


def test_healthy_input_passes():
    validate_turns([_turn(), _turn(request_id="req_2")])


# --- each check, fed the failure it exists to prevent ---------------------


def test_rejects_cumulative_counters_read_as_per_request_usage():
    """The Codex bug: 67 million input tokens on a single request."""
    with pytest.raises(Failure, match="context window"):
        validate_turns([_turn(input_tokens=67_000_000)])


def test_rejects_request_ids_built_from_byte_offsets():
    """Unique per record, so dedupe silently never fires."""
    with pytest.raises(Failure, match="byte offsets"):
        validate_turns([
            _turn(request_id="sess-abc:41231"),
            _turn(request_id="sess-abc:98765"),
        ])


def test_rejects_duplicate_request_ids():
    with pytest.raises(Failure, match="duplicate request_ids"):
        validate_turns([_turn(), _turn()])


def test_rejects_missing_model_ids():
    """Rows without a model price at zero and vanish from cost reports."""
    with pytest.raises(Failure, match="model_id"):
        validate_turns([
            _turn(request_id=f"r{i}", model_id=None if i else "claude-opus-5")
            for i in range(10)
        ])


def test_rejects_unparsed_timestamps():
    with pytest.raises(Failure, match="timestamp"):
        validate_turns([_turn(ts=None)])


def test_rejects_implausible_dates():
    with pytest.raises(Failure, match="implausible timestamp"):
        validate_turns([_turn(ts=datetime(1970, 1, 1))])


def test_rejects_negative_tokens():
    with pytest.raises(Failure, match="negative"):
        validate_turns([_turn(output_tokens=-5)])


def test_rejects_an_adapter_that_parses_nothing():
    with pytest.raises(Failure, match="zero requests"):
        validate_turns([])


def test_missing_fixtures_are_a_failure():
    """A new adapter with no sample logs cannot be verified by anyone."""
    with pytest.raises(Failure, match="no fixtures"):
        check("an-agent-that-does-not-exist")
