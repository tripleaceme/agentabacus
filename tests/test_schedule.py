"""Scheduling.

Installing a real launchd job or systemd timer cannot run in CI, so what is
tested here is everything that can be: interval parsing and the exact text of
the files handed to each scheduler. Those are the parts that break silently --
a malformed plist or unit does not error, it just never runs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentabacus import schedule  # noqa: E402

CMD = ["/usr/bin/python3", "-m", "agentabacus", "collect", "--quiet"]


# --- intervals ------------------------------------------------------------


@pytest.mark.parametrize(
    "text,minutes",
    [("5m", 5), ("30m", 30), ("1h", 60), ("6h", 360), ("1d", 1440), ("7d", 10080),
     (" 6H ", 360)],
)
def test_intervals_parse(text, minutes):
    assert schedule.parse_interval(text) == minutes


@pytest.mark.parametrize("text", ["1m", "4m", "0h"])
def test_too_frequent_is_refused(text):
    """Sub-5-minute polling burns battery for nothing: collection is
    incremental, so a long interval loses no data."""
    with pytest.raises(schedule.ScheduleError, match="too frequent"):
        schedule.parse_interval(text)


def test_too_long_is_refused():
    with pytest.raises(schedule.ScheduleError, match="7 day"):
        schedule.parse_interval("8d")


@pytest.mark.parametrize("text", ["", "soon", "6", "h", "6 hours", "-1h"])
def test_nonsense_is_refused(text):
    with pytest.raises(schedule.ScheduleError, match="could not read"):
        schedule.parse_interval(text)


@pytest.mark.parametrize(
    "minutes,expected",
    [(30, "every 30 minutes"), (60, "every 1 hour"), (360, "every 6 hours"),
     (1440, "every 1 day"), (2880, "every 2 days"), (90, "every 90 minutes")],
)
def test_humanise(minutes, expected):
    assert schedule.humanise(minutes) == expected


# --- the scheduled command ------------------------------------------------


def test_command_uses_the_interpreter_not_the_console_script():
    """Schedulers run with a minimal PATH. The `agentabacus` script may live in
    a virtualenv or pipx shim that is not on it; the interpreter that installed
    the package always resolves."""
    command = schedule._command()
    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "agentabacus"]
    assert "--quiet" in command, "a scheduled run must not print to a log forever"


# --- macOS ----------------------------------------------------------------


def test_plist_is_well_formed(tmp_path):
    text = schedule.build_plist(360, CMD, tmp_path / "collect.log")
    import plistlib

    parsed = plistlib.loads(text.encode())
    assert parsed["Label"] == schedule.LABEL
    assert parsed["ProgramArguments"] == CMD
    assert parsed["StartInterval"] == 360 * 60, "launchd wants seconds, not minutes"
    assert parsed["RunAtLoad"] is False
    assert parsed["StandardErrorPath"].endswith("collect.log")


def test_plist_interval_round_trips_through_status_parsing(tmp_path):
    """The status command reads the interval back out of the file it wrote."""
    text = schedule.build_plist(45, CMD, tmp_path / "l.log")
    found = re.search(r"<key>StartInterval</key><integer>(\d+)</integer>", text)
    assert found and int(found.group(1)) // 60 == 45


# --- Linux ----------------------------------------------------------------


def test_systemd_units_have_what_systemd_needs():
    service, timer = schedule.build_unit(120, CMD)

    assert "Type=oneshot" in service
    assert "-m" in service and "agentabacus" in service

    assert "OnUnitActiveSec=120min" in timer
    # Without OnBootSec the timer never fires until someone runs the service
    # by hand, which looks exactly like the feature being broken.
    assert "OnBootSec=" in timer
    # Without Persistent a laptop that was asleep silently skips the run.
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


# --- Windows --------------------------------------------------------------


@pytest.mark.parametrize(
    "minutes,expected",
    [(30, ["/sc", "MINUTE", "/mo", "30"]),
     (90, ["/sc", "MINUTE", "/mo", "90"]),
     (60, ["/sc", "HOURLY", "/mo", "1"]),
     (360, ["/sc", "HOURLY", "/mo", "6"]),
     (1440, ["/sc", "DAILY", "/mo", "1"]),
     (4320, ["/sc", "DAILY", "/mo", "3"])],
)
def test_schtasks_picks_a_unit_the_scheduler_accepts(minutes, expected):
    """schtasks caps /mo at 1439 for MINUTE, so long intervals must switch
    units rather than be rejected at the command line."""
    args = schedule.build_schtasks(minutes, CMD)
    assert args[-4:] == expected


def test_schtasks_never_exceeds_the_minute_cap():
    for minutes in (schedule.MIN_MINUTES, 720, 1439, 1440, schedule.MAX_MINUTES):
        args = schedule.build_schtasks(minutes, CMD)
        unit, count = args[-3], int(args[-1])
        if unit == "MINUTE":
            assert count <= 1439, f"{minutes}min produced /mo {count}"


def test_status_is_safe_to_call_anywhere():
    """Never raises, whatever the platform or state -- `schedule` with no
    arguments is the first thing a curious user runs."""
    state = schedule.status()
    assert isinstance(state.installed, bool)
