"""Adapter helpers.

Design rule for every adapter: **be a tolerant parser**. These formats are
undocumented, version-dependent, and change without notice. An adapter that
raises on an unknown `type` turns a vendor's routine release into a crash for
every user. Route on known shapes, count what you skipped, never throw.

The strictness lives in schema.py instead -- messy at the edges, contract-
enforced in the core.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterator


def parse_ts(value: Any) -> datetime | None:
    """ISO-8601 (with or without 'Z') or epoch ms/seconds. Never raises."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Heuristic: anything past ~2001 in seconds is milliseconds here.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    return None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def as_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


PROGRESS_CHUNK = 1 << 20  # report every 1 MB consumed


def iter_lines(
    path, start_offset: int = 0, on_bytes=None
) -> Iterator[tuple[dict, int]]:
    """Yield (record, offset_after_this_line) for every COMPLETE line.

    A trailing partial line -- the file was mid-write when we read it -- is not
    yielded and does not advance the offset, so the next run re-reads it whole.
    Malformed complete lines are skipped with a sentinel rather than raising.

    `on_bytes(n)` is called roughly every megabyte with the bytes consumed
    since the last call. Transcripts range from a few KB to ~18 MB, and a
    single large one can take half a minute; without intra-file reporting a
    progress bar sits frozen on it and looks hung. Optional, so an adapter
    that ignores it still works -- its files just advance the bar on completion.
    """
    with open(path, "rb") as fh:
        fh.seek(start_offset)
        offset = start_offset
        pending = 0
        for raw in fh:
            if not raw.endswith(b"\n"):
                break  # torn write; leave the offset where it was
            offset += len(raw)
            if on_bytes:
                pending += len(raw)
                if pending >= PROGRESS_CHUNK:
                    on_bytes(pending)
                    pending = 0
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except (ValueError, TypeError):
                yield ({"__unparsed__": True}, offset)
                continue
            if isinstance(record, dict):
                yield (record, offset)
            else:
                yield ({"__unparsed__": True}, offset)

        if on_bytes and pending:
            on_bytes(pending)   # flush the tail, so the bar reaches 100%


# Tool inputs vary per tool; these are the keys that carry "what did it act on".
_TARGET_KEYS = ("file_path", "path", "notebook_path", "command", "url", "pattern", "query")


def tool_target(tool_input: Any, limit: int = 400) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in _TARGET_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value[:limit]
    return None


def content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                total += len(block.get("text") or "")
            elif isinstance(block, str):
                total += len(block)
        return total
    return 0
