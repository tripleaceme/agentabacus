"""Finding the transcripts.

The non-obvious part: subagent transcripts live in their OWN files, one level
deeper than the main transcript --

    ~/.claude/projects/<slug>/<session-uuid>.jsonl              <- main
    ~/.claude/projects/<slug>/<session-uuid>/subagents/*.jsonl  <- subagents

A `projects/*/*.jsonl` glob (the obvious one, and the one most tools use) misses
every subagent file. On a machine that uses agent fan-out those outnumber the
main transcripts several to one.

The slug is the working directory with '/' replaced by '-'. That encoding is
lossy and NOT reversible: `-Users-mac-Documents-BrainStorm-Projects-atlas` could
decode to '.../BrainStorm Projects/atlas' or '.../BrainStorm-Projects-atlas'.
Never reverse it -- read `cwd` from inside the JSONL, where it is verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import config_root


@dataclass(frozen=True)
class Found:
    path: Path
    source: str
    kind: str  # transcript | subagent | history


def _claude_code(root: Path):
    projects = root / "projects"
    if projects.is_dir():
        for slug_dir in sorted(projects.iterdir()):
            if not slug_dir.is_dir():
                continue
            for f in sorted(slug_dir.glob("*.jsonl")):
                yield Found(f, "claude_code", "transcript")
            # Subagents live under the session's sidecar dir -- but at TWO
            # depths, so this must recurse rather than glob a fixed shape:
            #   <uuid>/subagents/agent-*.jsonl                      plain subagent
            #   <uuid>/subagents/workflows/wf_*/agent-*.jsonl       workflow subagent
            # The nested form is the majority on a machine that runs workflows;
            # a `*/subagents/*.jsonl` glob silently drops all of them.
            for f in sorted(slug_dir.glob("*/subagents/**/*.jsonl")):
                yield Found(f, "claude_code", "subagent")
    history = root / "history.jsonl"
    if history.is_file():
        yield Found(history, "claude_code", "history")


def _codex(root: Path):
    """Only `sessions/`. No fallback to the config root.

    Falling back to rglob over the whole root swept up caches, temp dirs and
    plugin test fixtures -- on the machine this was written, the single
    "Codex transcript" found that way was
    `~/.codex/.tmp/plugins/.../fixtures/observed-usage/responses.jsonl`,
    a test fixture whose numbers are invented. Finding nothing is the correct
    answer when the sessions directory is absent.
    """
    sessions = root / "sessions"
    if not sessions.is_dir():
        return
    for f in sorted(sessions.rglob("*.jsonl")):
        if any(part.startswith(".") for part in f.relative_to(sessions).parts):
            continue
        yield Found(f, "codex", "transcript")


_WALKERS = {"claude_code": _claude_code, "codex": _codex}


def discover(sources: list[str] | None = None) -> list[Found]:
    """Every log file agentabacus knows how to read, on this machine, right now."""
    out: list[Found] = []
    for source, walker in _WALKERS.items():
        if sources and source not in sources:
            continue
        root = config_root(source)
        if root is None:
            continue
        out.extend(walker(root))
    return out
