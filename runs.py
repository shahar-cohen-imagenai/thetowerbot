"""Turn confirmed screen transitions into run boundaries.

The FSM already knows when the game moves between the menu, a live run, and
the death modal - which is exactly enough to bracket a run without the bot
having to understand the game.

UNKNOWN deliberately does not close a run: a stray popup mid-battle must not
fabricate a boundary. The run stays open and resumes.
"""

from __future__ import annotations

import events
from screens import ScreenState


class RunTracker:
    def __init__(self) -> None:
        self._next_id = 1
        self.current_id: int | None = None
        self.completed = 0
        self._started_at: float | None = None

    def transition(
        self, prev: ScreenState, curr: ScreenState, now: float
    ) -> events.Event | None:
        """Return an unstamped RunStarted / RunEnded, or None."""
        if curr is ScreenState.UNKNOWN or prev is ScreenState.UNKNOWN:
            return None

        if curr is ScreenState.IN_RUN and self.current_id is None:
            self.current_id = self._next_id
            self._next_id += 1
            self._started_at = now
            return events.RunStarted(run_id=self.current_id)

        if prev is ScreenState.IN_RUN and self.current_id is not None:
            run_id = self.current_id
            started = self._started_at if self._started_at is not None else now
            self.current_id = None
            self._started_at = None
            self.completed += 1
            return events.RunEnded(
                run_id=run_id,
                duration=now - started,
                abandoned=curr is not ScreenState.GAME_OVER,
            )

        return None
