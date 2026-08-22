"""Finding the transcripts.

Only agents with an adapter are walked here. An agent we cannot parse is
detected in `agents.py` and reported by `doctor`; it is never read, parsed or
stored, so there is no way for a half-understood log format to end up
contributing numbers to anyone's spend.

The non-obvious part of the Claude Code layout: subagent transcripts live in
their OWN files, one and sometimes two levels deeper than the main transcript --

    ~/.claude/projects/<slug>/<uuid>.jsonl                            main
    ~/.claude/projects/<slug>/<uuid>/subagents/agent-*.jsonl          subagent
    ~/.claude/projects/<slug>/<uuid>/subagents/workflows/wf_*/...     workflow subagent

A `projects/*/*.jsonl` glob misses every subagent file, and a
`*/subagents/*.jsonl` glob still misses the workflow ones a level deeper --
which on a machine that runs workflows are the majority (measured: 80 of 127).
Discovery has to recurse.

The slug is the working directory with '/' replaced by '-'. That encoding is
lossy and NOT reversible: `-Users-mac-Documents-BrainStorm-Projects-atlas`
could decode to '.../BrainStorm Projects/atlas' or '.../BrainStorm-Projects-atlas'.
Never reverse it -- read `cwd` from inside the JSONL, where it is verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agents import BY_NAME, SUPPORTED


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
            for f in sorted(slug_dir.glob("*/subagents/**/*.jsonl")):
                yield Found(f, "claude_code", "subagent")
    history = root / "history.jsonl"
    if history.is_file():
        yield Found(history, "claude_code", "history")


# One walker per supported agent. Adding an agent means adding an adapter,
# a walker here, and flipping `supported` in agents.py.
_WALKERS = {"claude_code": _claude_code}


def discover(sources: list[str] | None = None) -> list[Found]:
    """Every log file agentabacus can actually read, on this machine, now."""
    out: list[Found] = []
    for agent in SUPPORTED:
        if sources and agent.name not in sources:
            continue
        walker = _WALKERS.get(agent.name)
        root = agent.root()
        if walker is None or root is None:
            continue
        out.extend(walker(root))
    return out


def config_root(name: str) -> Path | None:
    """Kept for callers that just want one agent's root by name."""
    agent = BY_NAME.get(name)
    return agent.root() if agent else None
