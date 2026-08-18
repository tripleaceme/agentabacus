"""The dedupe contract, pinned.

If this test ever fails, every cost number the tool produces is wrong. It is the
one invariant worth a test before anything else exists: Claude Code emits one
JSONL line per content block, each repeating the parent response's FULL usage,
so a per-line sum overcounts by however many blocks that response happened to
emit.

Run: python tests/test_dedupe.py    (or pytest)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentledger.adapters import claude_code  # noqa: E402

USAGE = {
    "input_tokens": 2,
    "output_tokens": 469,
    "cache_read_input_tokens": 42498,
    "cache_creation_input_tokens": 3799,
    "output_tokens_details": {"thinking_tokens": 211},
    "cache_creation": {
        "ephemeral_1h_input_tokens": 3000,
        "ephemeral_5m_input_tokens": 799,
    },
    "service_tier": "standard",
    "speed": "standard",
}


def _line(block_type: str, request_id: str, extra=None):
    block = {"type": block_type}
    if block_type == "tool_use":
        block |= {"id": f"toolu_{request_id}", "name": "Bash", "input": {"command": "ls"}}
    else:
        block["text"] = "x"
    record = {
        "type": "assistant",
        "requestId": request_id,
        "timestamp": "2026-08-18T00:24:12.000Z",
        "sessionId": "sess-1",
        "cwd": "/tmp/proj",
        "version": "2.1.0",
        "effort": "high",
        "message": {"model": "claude-opus-5", "usage": USAGE, "content": [block]},
    }
    return json.dumps(record | (extra or {}))


def test_three_blocks_collapse_to_one_request():
    # One API response that emitted thinking + text + tool_use = three lines.
    lines = [_line(t, "req_A") for t in ("thinking", "text", "tool_use")]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.jsonl"
        path.write_text("\n".join(lines) + "\n")
        batch = claude_code.parse_transcript(path)

    assert len(batch.turns) == 1, f"expected 1 request, got {len(batch.turns)}"
    turn = batch.turns[0]
    assert turn.block_lines == 3, "should record that 3 lines collapsed"

    # The numbers must equal ONE response's usage, not three.
    assert turn.output_tokens == 469, turn.output_tokens
    assert turn.cache_read_tokens == 42498, turn.cache_read_tokens
    assert turn.thinking_tokens == 211, turn.thinking_tokens

    naive = 469 * 3
    assert turn.output_tokens * 3 == naive, "sanity: naive sum would be 3x"


def test_cache_ttl_split_is_preserved():
    """1h writes bill at 2x base input, 5m at 1.25x. Collapsing them into a
    single cache_creation_input_tokens figure misprices every long session."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.jsonl"
        path.write_text(_line("text", "req_B") + "\n")
        turn = claude_code.parse_transcript(path).turns[0]

    assert turn.cache_write_1h_tokens == 3000
    assert turn.cache_write_5m_tokens == 799
    total = turn.cache_write_1h_tokens + turn.cache_write_5m_tokens
    assert total == USAGE["cache_creation_input_tokens"], "split must reconcile"


def test_distinct_requests_are_not_merged():
    lines = [_line("text", "req_A"), _line("text", "req_B")]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.jsonl"
        path.write_text("\n".join(lines) + "\n")
        batch = claude_code.parse_transcript(path)
    assert len(batch.turns) == 2
    assert sum(t.output_tokens for t in batch.turns) == 938


def test_torn_trailing_line_does_not_advance_offset():
    """A file caught mid-write must be re-read whole next run, not half-parsed."""
    complete = _line("text", "req_A") + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.jsonl"
        path.write_text(complete + '{"type":"assist')  # no newline: torn
        batch = claude_code.parse_transcript(path)
    assert batch.byte_offset == len(complete.encode()), (
        "offset must stop at the last COMPLETE line"
    )
    assert len(batch.turns) == 1


def test_unknown_record_types_do_not_raise():
    """Vendor formats change without notice; an adapter must never crash."""
    lines = [
        json.dumps({"type": "some-future-event", "payload": {"nested": [1, 2]}}),
        json.dumps({"type": "assistant"}),                # no message at all
        "{not json at all",
        _line("text", "req_A"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.jsonl"
        path.write_text("\n".join(lines) + "\n")
        batch = claude_code.parse_transcript(path)
    assert len(batch.turns) == 1
    assert batch.skipped_lines == 1  # only the malformed line counts as skipped


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}")
    print("\nall green" if not failures else f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
