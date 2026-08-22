"""Adapter registry.

One entry per agent we can actually parse. Adding support for a new agent CLI
is: write a module exposing `parse(path, kind, start_offset, on_bytes=None)
-> Batch`, add a walker in discovery.py, register it here, and flip
`supported=True` in agents.py.

There is deliberately no half-way state. An adapter is either finished and
trusted, or it is not here at all -- `doctor` will still tell users their logs
were spotted and point them at CONTRIBUTING.
"""

from __future__ import annotations

from . import claude_code

REGISTRY = {
    claude_code.SOURCE: claude_code.parse,
}

__all__ = ["REGISTRY", "claude_code"]
