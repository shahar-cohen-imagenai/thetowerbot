"""Live terminal panel.

TuiState is deliberately separate from rendering so it can be tested without
a terminal; TuiSink only draws.
"""

from __future__ import annotations

import time
from collections import Counter, deque

import events
from sinks.base import QueueSink
from sinks.log import render


class TuiState:
    """Everything the panel displays, accumulated from the event stream."""

    def __init__(self, tail: int = 12) -> None:
        self.screen = "UNKNOWN"
        self.scans = 0
        self.taps: Counter[str] = Counter()
        self.skips: Counter[str] = Counter()
        self.last_error: str | None = None
        self.started = time.monotonic()
        self.tail: deque[str] = deque(maxlen=tail)

    def apply(self, event: events.Event) -> None:
        match event:
            case events.ScreenChanged():
                self.screen = event.curr
                self.tail.append(render(event))
            case events.ScanCompleted():
                self.scans += 1
                self.screen = event.screen
            case events.Tapped():
                self.taps[event.action] += 1
                self.tail.append(render(event))
            case events.Skipped():
                self.skips[event.reason] += 1
            case events.BotError():
                self.last_error = event.message
                self.tail.append(render(event))
            case _:
                self.tail.append(render(event))

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.started


class TuiSink(QueueSink):
    def __init__(self, maxsize: int = 1000) -> None:
        super().__init__(maxsize=maxsize)
        self.state = TuiState()
        self._live = None

    def start(self) -> None:
        from rich.live import Live

        self._live = Live(self._render(), refresh_per_second=4)
        self._live.start()
        super().start()

    def close(self, timeout: float = 2.0) -> None:
        super().close(timeout=timeout)
        if self._live is not None:
            self._live.stop()
            self._live = None

    def handle(self, event: events.Event) -> None:
        self.state.apply(event)
        if self._live is not None:
            self._live.update(self._render())

    def _render(self):
        from rich.panel import Panel
        from rich.table import Table

        table = Table.grid(padding=(0, 2))
        table.add_row("Screen", self.state.screen)
        table.add_row("Uptime", f"{self.state.uptime:.0f}s")
        table.add_row("Scans", str(self.state.scans))
        for action, count in sorted(self.state.taps.items()):
            table.add_row(f"  {action}", f"x{count}")
        for reason, count in sorted(self.state.skips.items()):
            table.add_row(f"  skip:{reason}", str(count))
        if self.state.last_error:
            table.add_row("Error", self.state.last_error)
        table.add_row("", "\n".join(self.state.tail))
        return Panel(table, title="The Tower bot")
