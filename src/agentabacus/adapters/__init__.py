"""Adapter registry.

Adding support for a new agent CLI is: write a module exposing
`parse(path, kind, start_offset) -> Batch`, add a walker in discovery.py, and
register it here. Nothing else in the codebase needs to change.
"""

from __future__ import annotations

from . import claude_code, codex

REGISTRY = {
    claude_code.SOURCE: claude_code.parse,
    codex.SOURCE: codex.parse,
}

# Adapters that have never been verified against real files from that tool.
#
# Their rows are still collected and stored -- so that fixing the adapter and
# re-running `collect --full` recovers the history -- but they are EXCLUDED
# from headline totals. An unverified adapter that quietly contributes to a
# cost figure is worse than one that reports nothing: a missing number prompts
# a question, a wrong number gets believed.
#
# This is not hypothetical. The Codex adapter reported 4,318 requests carrying
# 291 billion input tokens, roughly 67 million tokens per request. It appears
# to be summing cumulative token counters rather than per-request usage --
# the same class of bug as the per-line overcount the Claude Code adapter
# exists to avoid.
#
# Remove a source from this set only once someone has checked its output
# against real transcripts. See CONTRIBUTING.md.
EXPERIMENTAL = {"codex"}

CONTRIBUTING_URL = (
    "https://github.com/tripleaceme/agentabacus/blob/main/CONTRIBUTING.md"
    "#contributing-an-adapter"
)

__all__ = ["REGISTRY", "EXPERIMENTAL", "CONTRIBUTING_URL", "claude_code", "codex"]
