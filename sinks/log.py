"""Structured, greppable log lines - the default sink.

One line per event, timestamp first, event kind in a fixed-width column so
`grep TAP` and `grep SKIP` both work without a parser.
"""

from __future__ import annotations

import sys
import time
from typing import TextIO

import events
from sinks.base import QueueSink


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def render(event: events.Event) -> str:
    ts = _clock(event.ts)
    match event:
        case events.ScanCompleted():
            wallet = "" if event.wallet is None else f" wallet=${event.wallet}"
            return (
                f"{ts} SCAN   screen={event.screen} "
                f"{event.duration_ms:.0f}ms{wallet}"
            )
        case events.ScreenChanged():
            return (
                f"{ts} SCREEN {event.prev} -> {event.curr} "
                f"(conf {event.confidence:.3f})"
            )
        case events.Tapped():
            price = "" if event.price is None else f" price=${event.price}"
            return (
                f"{ts} TAP    {event.action} at ({event.x},{event.y}) "
                f"score={event.score:.3f}{price}"
            )
        case events.Skipped():
            detail = f" {event.detail}" if event.detail else ""
            return f"{ts} SKIP   {event.action} reason={event.reason}{detail}"
        case events.RunStarted():
            return f"{ts} RUN    #{event.run_id} started"
        case events.RunEnded():
            how = "abandoned" if event.abandoned else "ended"
            wave = "" if event.wave is None else f" wave={event.wave}"
            coins = "" if event.coins is None else f" coins={event.coins}"
            return (
                f"{ts} RUN    #{event.run_id} {how} "
                f"after {event.duration:.0f}s{wave}{coins}"
            )
        case events.Navigated():
            return f"{ts} NAV    {event.target}"
        case events.UnknownScreen():
            return (
                f"{ts} UNKNWN best={event.best_anchor} "
                f"{event.best_score:.3f} saved={event.snapshot_path}"
            )
        case events.BotError():
            return f"{ts} ERROR  {event.message}"
        case _:
            return f"{ts} {event.type}"


class LogSink(QueueSink):
    def __init__(self, stream: TextIO | None = None, maxsize: int = 1000) -> None:
        super().__init__(maxsize=maxsize)
        self._stream = stream if stream is not None else sys.stdout

    def handle(self, event: events.Event) -> None:
        print(render(event), file=self._stream, flush=True)
