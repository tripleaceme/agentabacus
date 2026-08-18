"""Filesystem locations. Nothing here is hardcoded to one machine or one vendor.

Every agent CLI stores its logs under a *config root* that the user can relocate
via an environment variable. Adapters take the root as a parameter so a test can
point them at a fixture directory, and so a user with a relocated config dir is
not silently skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

# Where agentledger keeps its own state. Overridable for the same reason.
AGENTLEDGER_HOME = Path(os.environ.get("AGENTLEDGER_HOME", Path.home() / ".agentledger"))
DB_PATH = AGENTLEDGER_HOME / "agentledger.duckdb"

# source name -> (env var that may relocate the root, default root)
CONFIG_ROOTS: dict[str, tuple[str, Path]] = {
    "claude_code": ("CLAUDE_CONFIG_DIR", Path.home() / ".claude"),
    "codex": ("CODEX_HOME", Path.home() / ".codex"),
    "gemini": ("GEMINI_CONFIG_DIR", Path.home() / ".gemini"),
}


def config_root(source: str) -> Path | None:
    """Resolve one source's config root, or None if it isn't present on disk."""
    if source not in CONFIG_ROOTS:
        return None
    env_var, default = CONFIG_ROOTS[source]
    root = Path(os.environ[env_var]).expanduser() if os.environ.get(env_var) else default
    return root if root.is_dir() else None


def ensure_home() -> Path:
    AGENTLEDGER_HOME.mkdir(parents=True, exist_ok=True)
    return AGENTLEDGER_HOME
