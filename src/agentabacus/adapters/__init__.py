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

__all__ = ["REGISTRY", "claude_code", "codex"]
