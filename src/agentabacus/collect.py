"""The incremental collector.

Transcripts are garbage-collected by the agent CLIs themselves, so collection is
an ELT job with a deadline, not a read-on-demand convenience. Two consequences
shape this module:

* **Resume by byte offset.** Re-running collect re-reads nothing; a 350 MB
  corpus costs one stat() per file when nothing changed.
* **Never fail the run on one bad file.** A file that raises is reported and
  skipped, because the alternative is losing every later file to an exception
  raised by the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import store
from .adapters import REGISTRY
from .discovery import Found, discover


@dataclass
class CollectResult:
    files_seen: int = 0
    files_read: int = 0
    files_skipped_unchanged: int = 0
    bytes_read: int = 0
    counts: dict = field(default_factory=lambda: {
        "sessions": 0, "turns": 0, "tool_calls": 0, "prompts": 0
    })
    malformed_lines: int = 0
    errors: list = field(default_factory=list)


def _should_read(conn, found: Found, full: bool) -> tuple[bool, int]:
    """Decide whether to open the file, and from which byte."""
    offset, mtime, size = store.watermark(conn, found.path)
    if full:
        return True, 0
    try:
        stat = found.path.stat()
    except OSError:
        return False, 0
    if offset and stat.st_mtime == mtime and stat.st_size == size:
        return False, offset          # untouched since last run
    if stat.st_size < offset:
        return True, 0                # truncated or rotated; start over
    return True, offset


def collect(
    conn,
    sources: list[str] | None = None,
    full: bool = False,
    session_id: str | None = None,
    on_file=None,
) -> CollectResult:
    result = CollectResult()

    for found in discover(sources):
        # SessionEnd hook path: collect exactly the session that just closed.
        if session_id and session_id not in found.path.name and session_id not in str(found.path):
            continue
        result.files_seen += 1

        read, offset = _should_read(conn, found, full)
        if not read:
            result.files_skipped_unchanged += 1
            continue

        parse = REGISTRY.get(found.source)
        if parse is None:
            continue

        try:
            batch = parse(found.path, found.kind, offset)
        except Exception as exc:  # a vendor format change must not kill the run
            result.errors.append(f"{found.path}: {type(exc).__name__}: {exc}")
            continue

        counts = store.write_batch(conn, batch)
        store.set_watermark(conn, found.path, found.source, batch.byte_offset)

        result.files_read += 1
        result.bytes_read += max(0, batch.byte_offset - offset)
        result.malformed_lines += batch.skipped_lines
        for key, value in counts.items():
            result.counts[key] += value
        if on_file:
            on_file(found, counts)

    return result
