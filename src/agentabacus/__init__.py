"""agentabacus - local-first analytics for AI coding agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentabacus")
except PackageNotFoundError:      # running from a source tree, not installed
    __version__ = "0.0.0.dev0"
