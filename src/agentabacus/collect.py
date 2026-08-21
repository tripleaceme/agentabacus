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


@dataclass
class Job:
    """One file to parse, and how much of it is left to read."""

    found: Found
    offset: int
    nbytes: int


def build_plan(
    conn,
    sources: list[str] | None = None,
    full: bool = False,
    session_id: str | None = None,
) -> tuple[list[Job], int, int]:
    """Decide all the work before doing any of it.

    Planning up front is what makes an honest percentage possible: the total
    bytes to parse are known before the first byte is read. It costs one
    stat() per discovered file, which is nothing next to reading them, and it
    means the progress bar can never lurch or overshoot.

    Returns (jobs, files_seen, files_skipped_unchanged).
    """
    jobs: list[Job] = []
    seen = skipped = 0

    for found in discover(sources):
        # SessionEnd hook path: collect exactly the session that just closed.
        if session_id and session_id not in found.path.name and session_id not in str(found.path):
            continue
        seen += 1

        read, offset = _should_read(conn, found, full)
        if not read:
            skipped += 1
            continue
        if found.source not in REGISTRY:
            continue

        try:
            size = found.path.stat().st_size
        except OSError:
            continue
        jobs.append(Job(found=found, offset=offset, nbytes=max(0, size - offset)))

    return jobs, seen, skipped


def collect(
    conn,
    sources: list[str] | None = None,
    full: bool = False,
    session_id: str | None = None,
    on_plan=None,
    on_file_start=None,
    on_progress=None,
) -> CollectResult:
    """Parse everything new into the archive.

    on_plan(jobs, total_bytes)  -- once, before any parsing
    on_file_start(index, job)   -- as each file is opened, NOT when it finishes
    on_progress(nbytes)         -- bytes consumed, roughly every megabyte

    on_file_start fires on open rather than on completion so a caller shows the
    file it is actually working on. Reporting the last-finished file instead is
    doubly misleading: during a slow file the display names the wrong one, and
    it names it for exactly as long as the user is wondering what is stuck.
    """
    result = CollectResult()
    jobs, seen, skipped = build_plan(conn, sources, full, session_id)
    result.files_seen = seen
    result.files_skipped_unchanged = skipped

    total_bytes = sum(j.nbytes for j in jobs)
    if on_plan:
        on_plan(jobs, total_bytes)

    for index, job in enumerate(jobs, start=1):
        found, offset = job.found, job.offset
        parse = REGISTRY[found.source]
        if on_file_start:
            on_file_start(index, job)

        # Track what the adapter reported so the tail can be topped up exactly.
        # An adapter that ignores on_bytes reports nothing and its whole size
        # lands in the top-up -- correct either way, just coarser.
        reported = 0

        def on_bytes(n, _state=None):
            nonlocal reported
            reported += n
            if on_progress:
                on_progress(n)

        try:
            batch = parse(found.path, found.kind, offset, on_bytes)
        except TypeError:
            # Adapter predates the on_bytes parameter. Still supported.
            try:
                batch = parse(found.path, found.kind, offset)
            except Exception as exc:
                result.errors.append(f"{found.path}: {type(exc).__name__}: {exc}")
                batch = None
        except Exception as exc:  # a vendor format change must not kill the run
            result.errors.append(f"{found.path}: {type(exc).__name__}: {exc}")
            batch = None

        if batch is not None:
            counts = store.write_batch(conn, batch)
            store.set_watermark(conn, found.path, found.source, batch.byte_offset)

            result.files_read += 1
            result.bytes_read += max(0, batch.byte_offset - offset)
            result.malformed_lines += batch.skipped_lines
            for key, value in counts.items():
                result.counts[key] += value

        # Top up whatever the adapter did not report, so the bar lands on 100%
        # whether the file succeeded, failed, or reported nothing at all.
        leftover = job.nbytes - reported
        if on_progress and leftover > 0:
            on_progress(leftover)

    return result
