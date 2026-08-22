"""The agents agentabacus knows about.

One list, two states: an agent either has an adapter or it does not.

  supported = True   -> its logs are discovered, parsed and collected
  supported = False  -> we only detect that its logs exist, and say so

Nothing in between. A half-working adapter that contributes uncertain numbers
to someone's spend is worse than no adapter at all, so unsupported agents are
never parsed and never stored -- `doctor` simply reports that their logs are
present and links to CONTRIBUTING.

Adding an agent to this list with `supported=False` costs nothing and makes it
visible to every user who has it installed. Flipping it to True means writing
an adapter (see CONTRIBUTING.md) and adding a flag in cli.py.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()


def _app_support(name: str) -> tuple[Path, ...]:
    """Where an Electron/VS Code-family app keeps its per-user data."""
    if sys.platform == "darwin":
        return (HOME / "Library" / "Application Support" / name,)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) / name,) if appdata else ()
    return (HOME / ".config" / name,)


@dataclass(frozen=True)
class Agent:
    name: str                 # canonical id, also the `source` column value
    flag: str                 # CLI flag, e.g. "claude" -> `collect --claude`
    label: str                # human name
    env_var: str | None       # env var that relocates the root, if any
    candidates: tuple[Path, ...]   # default locations, first match wins
    supported: bool

    def root(self) -> Path | None:
        """Where this agent's data actually is, or None if not installed."""
        if self.env_var and os.environ.get(self.env_var):
            candidate = Path(os.environ[self.env_var]).expanduser()
            return candidate if candidate.is_dir() else None
        for candidate in self.candidates:
            if candidate.is_dir():
                return candidate
        return None


AGENTS: tuple[Agent, ...] = (
    Agent(
        name="claude_code", flag="claude", label="Claude Code",
        env_var="CLAUDE_CONFIG_DIR", candidates=(HOME / ".claude",),
        supported=True,
    ),
    Agent(
        name="codex", flag="codex", label="Codex CLI",
        env_var="CODEX_HOME", candidates=(HOME / ".codex",),
        supported=False,
    ),
    Agent(
        name="gemini", flag="gemini", label="Gemini CLI",
        env_var="GEMINI_CONFIG_DIR", candidates=(HOME / ".gemini",),
        supported=False,
    ),
    Agent(
        name="opencode", flag="opencode", label="OpenCode",
        env_var="OPENCODE_CONFIG_DIR",
        candidates=(
            HOME / ".local" / "share" / "opencode",
            HOME / ".opencode",
        ),
        supported=False,
    ),
    # Detect the extension's own storage, not the editor's. Matching
    # `Application Support/Code` would report Cline for anyone who has ever
    # installed VS Code, which is a false positive dressed as a finding.
    Agent(
        name="cursor", flag="cursor", label="Cursor",
        env_var=None,
        candidates=tuple(p / "User" / "globalStorage" for p in _app_support("Cursor")),
        supported=False,
    ),
    Agent(
        name="cline", flag="cline", label="Cline",
        env_var=None,
        candidates=tuple(
            base / "User" / "globalStorage" / "saoudrizwan.claude-dev"
            for name in ("Code", "VSCodium")
            for base in _app_support(name)
        ),
        supported=False,
    ),
    Agent(
        name="aider", flag="aider", label="Aider",
        env_var=None, candidates=(HOME / ".aider",),
        supported=False,
    ),
)

BY_NAME = {a.name: a for a in AGENTS}
BY_FLAG = {a.flag: a for a in AGENTS}

SUPPORTED = tuple(a for a in AGENTS if a.supported)
SUPPORTED_NAMES = frozenset(a.name for a in SUPPORTED)

CONTRIBUTING_URL = (
    "https://github.com/tripleaceme/agentabacus/blob/main/CONTRIBUTING.md"
    "#contributing-an-adapter"
)


def installed(supported: bool | None = None) -> list[tuple[Agent, Path]]:
    """Agents whose data directory exists on this machine.

    supported=None  -> all of them
    supported=True  -> only the ones we can actually collect
    supported=False -> only the ones we can see but cannot read yet
    """
    out = []
    for agent in AGENTS:
        if supported is not None and agent.supported is not supported:
            continue
        root = agent.root()
        if root is not None:
            out.append((agent, root))
    return out
