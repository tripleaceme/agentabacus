"""Where agentabacus keeps its own data.

Agent log locations live in agents.py, not here -- that file is the single
list of what this tool knows about.
"""

from __future__ import annotations

import os
from pathlib import Path

AGENTABACUS_HOME = Path(
    os.environ.get("AGENTABACUS_HOME", Path.home() / ".agentabacus")
)
DB_PATH = AGENTABACUS_HOME / "agentabacus.duckdb"


def ensure_home() -> Path:
    AGENTABACUS_HOME.mkdir(parents=True, exist_ok=True)
    return AGENTABACUS_HOME
