"""Codex CLI adapter -- STRUCTURE UNVERIFIED.

No Codex rollout files were available on the machine this was written against,
so this adapter is deliberately shape-agnostic: it walks each record looking for
anything that resembles a usage object and a request identifier, rather than
asserting a layout it cannot confirm.

That makes it useful as a starting point and as the template for a third
adapter, but treat its output as provisional until someone verifies it against
real files. Rows land with `source='codex'`, so they are trivial to exclude.

Contributing a verified version is the single highest-value PR here: paste two
or three real records into a test fixture and tighten the parsing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schema import Batch, Session, Turn
from .base import as_int, iter_lines, parse_ts

SOURCE = "codex"

# Both Anthropic-style and OpenAI-style names, since Codex may use either.
_INPUT_KEYS = ("input_tokens", "prompt_tokens")
_OUTPUT_KEYS = ("output_tokens", "completion_tokens")


def _find_usage(node: Any, depth: int = 0):
    """Depth-limited hunt for a dict that looks like a usage object."""
    if depth > 6 or not isinstance(node, dict):
        return None
    looks_like_usage = any(k in node for k in _INPUT_KEYS) and any(
        k in node for k in _OUTPUT_KEYS
    )
    if looks_like_usage:
        return node
    for value in node.values():
        if isinstance(value, dict):
            found = _find_usage(value, depth + 1)
            if found is not None:
                return found
    return None


def _first(node: dict, keys) -> Any:
    for key in keys:
        value = node.get(key)
        if value:
            return value
    return None


def parse(
    path: Path, kind: str = "transcript", start_offset: int = 0, on_bytes=None
) -> Batch:
    batch = Batch(byte_offset=start_offset)
    session_id = path.stem
    turns: dict[str, Turn] = {}
    model_id = None
    first_ts = last_ts = None

    for record, offset in iter_lines(path, start_offset, on_bytes):
        batch.byte_offset = offset
        if record.get("__unparsed__"):
            batch.skipped_lines += 1
            continue

        ts = parse_ts(_first(record, ("timestamp", "created_at", "time")))
        if ts:
            first_ts = ts if first_ts is None or ts < first_ts else first_ts
            last_ts = ts if last_ts is None or ts > last_ts else last_ts
        model_id = model_id or _first(record, ("model", "model_id"))

        usage = _find_usage(record)
        if not usage:
            continue

        request_id = (
            _first(record, ("request_id", "response_id", "id"))
            or f"{session_id}:{offset}"
        )
        fields = {
            "input_tokens": as_int(_first(usage, _INPUT_KEYS)),
            "output_tokens": as_int(_first(usage, _OUTPUT_KEYS)),
            "cache_read_tokens": as_int(
                _first(usage, ("cache_read_input_tokens", "cached_tokens"))
            ),
        }
        existing = turns.get(request_id)
        if existing is None:
            turns[request_id] = Turn(
                request_id=f"codex:{request_id}",
                session_id=session_id,
                thread_id="main",
                source=SOURCE,
                model_id=_first(record, ("model", "model_id")) or model_id,
                ts=ts,
                speed="standard",
                block_lines=1,
                **fields,
            )
        else:
            # Same dedupe discipline as the Claude Code adapter: if a runtime
            # repeats usage across records, MAX keeps the total honest.
            for key, value in fields.items():
                setattr(existing, key, max(getattr(existing, key), value))
            existing.block_lines += 1

    if turns:
        batch.sessions.append(
            Session(
                session_id=session_id,
                thread_id="main",
                source=SOURCE,
                started_at=first_ts,
                ended_at=last_ts,
                transcript_path=str(path),
            )
        )
        batch.turns = list(turns.values())
    return batch
