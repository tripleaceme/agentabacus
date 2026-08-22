#!/usr/bin/env python3
"""Work out the next version from the latest tag and the merge commit subject.

Conventional-commit prefixes, read from the squash-merge subject (which GitHub
takes from the PR title):

    feat!: ... / BREAKING CHANGE   -> major
    feat: ...                      -> minor
    anything else                  -> patch

Nobody edits a version number by hand, and nothing is committed back to main:
the tag is the version, so releasing is one `git tag`. That keeps the release
job clear of branch-protection rules, which reject bot pushes to main.

Usage:  python scripts/next_version.py "<commit subject>"
Prints: the bare version, e.g. 0.3.0
"""

from __future__ import annotations

import re
import subprocess
import sys

TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def latest_tag() -> tuple[int, int, int]:
    try:
        out = subprocess.run(
            ["git", "tag", "--list", "v*", "--sort=-v:refname"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return (0, 0, 0)
    for tag in out:
        m = TAG.match(tag.strip())
        if m:
            return tuple(int(g) for g in m.groups())  # type: ignore[return-value]
    return (0, 0, 0)


def bump_kind(subject: str) -> str:
    text = subject.strip()
    head = text.splitlines()[0] if text else ""
    if "BREAKING CHANGE" in text or re.match(r"^\w+(\([^)]*\))?!:", head):
        return "major"
    if re.match(r"^feat(\([^)]*\))?:", head, re.I):
        return "minor"
    return "patch"


def next_version(subject: str) -> str:
    major, minor, patch = latest_tag()
    kind = bump_kind(subject)
    if kind == "major":
        # 0.x is still pre-1.0: a breaking change moves the minor, not the
        # major, until the project deliberately ships 1.0.
        return f"{major}.{minor + 1}.0" if major == 0 else f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


if __name__ == "__main__":
    print(next_version(sys.argv[1] if len(sys.argv) > 1 else ""))
