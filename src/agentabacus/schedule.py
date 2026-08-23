"""Scheduled collection, using whatever the operating system already provides.

No daemon of our own. A background process that has to stay alive is a support
burden and something users are right to be suspicious of; launchd, systemd and
Task Scheduler are already installed, already supervised, and already visible
to the user in tools they know.

  macOS    ~/Library/LaunchAgents/tech.agentabacus.collect.plist
  Linux    ~/.config/systemd/user/agentabacus-collect.{service,timer}
  Windows  Task Scheduler task "agentabacus collect"

The scheduled command is `<python> -m agentabacus collect --quiet`, never the
`agentabacus` console script: schedulers run with a minimal PATH, and the
script may live in a virtualenv or pipx shim that is not on it. Pinning the
interpreter that installed the package is the one path guaranteed to resolve.

This complements the Claude Code SessionEnd hook rather than replacing it. The
hook fires the moment a session closes, which is ideal; a timer also catches
sessions that never exited cleanly, and every agent that has no hook at all.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import AGENTABACUS_HOME, ensure_home

LABEL = "tech.agentabacus.collect"
UNIT = "agentabacus-collect"
TASK = "agentabacus collect"

MIN_MINUTES = 5           # below this you are just burning battery
MAX_MINUTES = 7 * 24 * 60

_INTERVAL = re.compile(r"^\s*(\d+)\s*([mhd])\s*$", re.I)
_UNITS = {"m": 1, "h": 60, "d": 1440}


class ScheduleError(RuntimeError):
    pass


def parse_interval(text: str) -> int:
    """'30m' / '6h' / '1d' -> minutes."""
    match = _INTERVAL.match(text or "")
    if not match:
        raise ScheduleError(
            f"could not read interval {text!r}. Use e.g. 30m, 2h, 6h, or 1d."
        )
    minutes = int(match.group(1)) * _UNITS[match.group(2).lower()]
    if minutes < MIN_MINUTES:
        raise ScheduleError(
            f"{text} is too frequent; {MIN_MINUTES} minutes is the minimum. "
            f"Collection is incremental, so a long interval loses nothing."
        )
    if minutes > MAX_MINUTES:
        raise ScheduleError(f"{text} is longer than the 7 day maximum.")
    return minutes


def humanise(minutes: int) -> str:
    if minutes % 1440 == 0:
        n = minutes // 1440
        return f"every {n} day{'s' if n > 1 else ''}"
    if minutes % 60 == 0:
        n = minutes // 60
        return f"every {n} hour{'s' if n > 1 else ''}"
    return f"every {minutes} minutes"


def _command() -> list[str]:
    return [sys.executable, "-m", "agentabacus", "collect", "--quiet"]


def log_path() -> Path:
    ensure_home()
    return AGENTABACUS_HOME / "collect.log"


@dataclass
class Status:
    installed: bool
    minutes: int | None = None
    detail: str = ""
    path: str = ""


# --------------------------------------------------------------------------
# macOS -- launchd
# --------------------------------------------------------------------------

def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(minutes: int, command: list[str], log: Path) -> str:
    args = "".join(f"        <string>{c}</string>\n" for c in command)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}    </array>
    <key>StartInterval</key><integer>{minutes * 60}</integer>
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
    <key>ProcessType</key><string>Background</string>
</dict>
</plist>
"""


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _install_launchd(minutes: int) -> str:
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_plist(minutes, _command(), log_path()))

    target = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{target}/{LABEL}")      # ignore "not loaded"
    result = _launchctl("bootstrap", target, str(path))
    if result.returncode != 0:
        # older macOS
        result = _launchctl("load", "-w", str(path))
        if result.returncode != 0:
            raise ScheduleError(result.stderr.strip() or "launchctl refused the job")
    return str(path)


def _status_launchd() -> Status:
    path = _plist_path()
    if not path.exists():
        return Status(False)
    text = path.read_text()
    match = re.search(r"<key>StartInterval</key><integer>(\d+)</integer>", text)
    minutes = int(match.group(1)) // 60 if match else None
    loaded = _launchctl("list", LABEL).returncode == 0
    return Status(True, minutes, "loaded" if loaded else "not loaded", str(path))


def _uninstall_launchd() -> bool:
    path = _plist_path()
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    _launchctl("unload", str(path))
    if path.exists():
        path.unlink()
        return True
    return False


# --------------------------------------------------------------------------
# Linux -- systemd user timer
# --------------------------------------------------------------------------

def _unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def build_unit(minutes: int, command: list[str]) -> tuple[str, str]:
    quoted = " ".join(f'"{c}"' for c in command)
    service = f"""[Unit]
Description=agentabacus incremental collection

[Service]
Type=oneshot
ExecStart={quoted}
"""
    # OnUnitActiveSec alone never fires until the first manual run, so
    # OnBootSec seeds it. Persistent catches up after the machine was asleep.
    timer = f"""[Unit]
Description=Run agentabacus collect {humanise(minutes)}

[Timer]
OnBootSec=5min
OnUnitActiveSec={minutes}min
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
"""
    return service, timer


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _install_systemd(minutes: int) -> str:
    if shutil.which("systemctl") is None:
        raise ScheduleError(
            "systemctl not found. Add a cron entry instead:\n"
            f"  */{minutes} * * * * {' '.join(_command())}"
        )
    directory = _unit_dir()
    directory.mkdir(parents=True, exist_ok=True)
    service, timer = build_unit(minutes, _command())
    (directory / f"{UNIT}.service").write_text(service)
    (directory / f"{UNIT}.timer").write_text(timer)

    _systemctl("daemon-reload")
    result = _systemctl("enable", "--now", f"{UNIT}.timer")
    if result.returncode != 0:
        raise ScheduleError(result.stderr.strip() or "systemctl refused the timer")
    return str(directory / f"{UNIT}.timer")


def _status_systemd() -> Status:
    timer = _unit_dir() / f"{UNIT}.timer"
    if not timer.exists():
        return Status(False)
    match = re.search(r"OnUnitActiveSec=(\d+)min", timer.read_text())
    minutes = int(match.group(1)) if match else None
    active = _systemctl("is-active", f"{UNIT}.timer").stdout.strip()
    return Status(True, minutes, active or "unknown", str(timer))


def _uninstall_systemd() -> bool:
    directory = _unit_dir()
    timer = directory / f"{UNIT}.timer"
    _systemctl("disable", "--now", f"{UNIT}.timer")
    removed = False
    for name in (f"{UNIT}.timer", f"{UNIT}.service"):
        path = directory / name
        if path.exists():
            path.unlink()
            removed = True
    _systemctl("daemon-reload")
    return removed


# --------------------------------------------------------------------------
# Windows -- Task Scheduler
# --------------------------------------------------------------------------

def build_schtasks(minutes: int, command: list[str]) -> list[str]:
    """schtasks caps /mo at 1439 for MINUTE, so switch units past a day."""
    if minutes % 1440 == 0 and minutes >= 1440:
        cadence = ["/sc", "DAILY", "/mo", str(minutes // 1440)]
    elif minutes % 60 == 0 and minutes >= 60:
        cadence = ["/sc", "HOURLY", "/mo", str(minutes // 60)]
    else:
        cadence = ["/sc", "MINUTE", "/mo", str(minutes)]
    runner = " ".join(f'\\"{c}\\"' for c in command)
    return ["schtasks", "/create", "/f", "/tn", TASK, "/tr", runner, *cadence]


def _install_schtasks(minutes: int) -> str:
    result = subprocess.run(
        build_schtasks(minutes, _command()), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ScheduleError(result.stderr.strip() or result.stdout.strip())
    return TASK


def _status_schtasks() -> Status:
    result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK, "/fo", "LIST"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return Status(False)
    state = ""
    for line in result.stdout.splitlines():
        if line.lower().startswith("status:"):
            state = line.split(":", 1)[1].strip()
    return Status(True, None, state or "scheduled", TASK)


def _uninstall_schtasks() -> bool:
    result = subprocess.run(
        ["schtasks", "/delete", "/f", "/tn", TASK], capture_output=True, text=True
    )
    return result.returncode == 0


# --------------------------------------------------------------------------

def backend() -> str:
    system = platform.system()
    if system == "Darwin":
        return "launchd"
    if system == "Windows":
        return "schtasks"
    if system == "Linux":
        return "systemd"
    raise ScheduleError(f"no scheduler support for {system} yet")


def install(minutes: int) -> str:
    return {
        "launchd": _install_launchd,
        "systemd": _install_systemd,
        "schtasks": _install_schtasks,
    }[backend()](minutes)


def status() -> Status:
    try:
        which = backend()
    except ScheduleError:
        return Status(False)
    return {
        "launchd": _status_launchd,
        "systemd": _status_systemd,
        "schtasks": _status_schtasks,
    }[which]()


def uninstall() -> bool:
    return {
        "launchd": _uninstall_launchd,
        "systemd": _uninstall_systemd,
        "schtasks": _uninstall_schtasks,
    }[backend()]()
