"""Live terminal panel.

The state it draws lives in sinks/state.py, shared with the dashboard's
/api/status: the panel and the web header answer the same question, and one
accumulator is the only way they cannot disagree. TuiSink only draws.
"""

from __future__ import annotations

import events
from sinks.base import QueueSink
from sinks.state import BotState


class TuiSink(QueueSink):
    def __init__(self, state: BotState | None = None, maxsize: int = 1000) -> None:
        super().__init__(maxsize=maxsize)
        self.state = state if state is not None else BotState()
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

    def _render(self) -> "Panel":
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
