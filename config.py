"""Runtime configuration for the Tower bot.

Keep every tunable value here so the bot logic stays free of magic numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

# --- ADB connection -------------------------------------------------------
ADB_HOST: str = "127.0.0.1"
ADB_PORT: int = 5037  # adb *server* port (not the device port)

# The emulator's ADB endpoint. Android Studio AVDs listen on <console_port + 1>,
# so the first running emulator (emulator-5554) is reachable at 127.0.0.1:5555.
DEVICE_HOST: str = "127.0.0.1"
DEVICE_PORT: int = 5555

# --- Loop timing ----------------------------------------------------------
SCAN_INTERVAL_SECONDS: float = 2.0
# Minimum delay between two clicks on the *same* template, so a slow UI
# animation does not cause a burst of taps on a button that is already pressed.
CLICK_COOLDOWN_SECONDS: float = 1.0

# --- Vision ---------------------------------------------------------------
TEMPLATE_DIR: Path = Path(__file__).parent / "templates"
DEFAULT_THRESHOLD: float = 0.8


class Action(NamedTuple):
    """One template the bot looks for, in priority order."""

    name: str
    template: str  # file name inside TEMPLATE_DIR
    threshold: float = DEFAULT_THRESHOLD


# Evaluated top to bottom on every scan. Add rows as you capture more buttons.
ACTIONS: tuple[Action, ...] = (
    Action(name="Upgrade Health", template="upgrade_health.png", threshold=0.85),
)
