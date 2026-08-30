# Tower Bot Core (Phases 0-2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot screen-aware, continuously observable, and safe — so it only taps live upgrades on the in-run screen, reports what it is doing, and can loop runs unattended.

**Architecture:** The scan loop moves into a worker thread and publishes typed events to a thread-safe `EventBus`. Sinks (structured log lines, opt-in `rich` TUI) consume from bounded queues, so no sink can ever stall the loop. A debounced screen FSM classifies every frame into `MAIN_MENU` / `IN_RUN` / `GAME_OVER` / `UNKNOWN` and gates all actions on the result.

**Tech Stack:** Python 3.12, uv, adbutils, opencv-python, numpy, rich (new), pytest (new, dev)

**Spec:** `docs/superpowers/specs/2026-08-30-tower-bot-observability-design.md`

## Global Constraints

- Python >= 3.12. Every function gets type hints. `from __future__ import annotations` at the top of every module.
- Dependencies are managed with `uv add` / `uv add --dev` — never hand-edit `pyproject.toml`. Commit `pyproject.toml` and `uv.lock` together.
- Emulator resolution is **1080x2400**. Templates are not scale-invariant; a different resolution invalidates every one of them.
- `ANCHOR_THRESHOLD = 0.8`, `SCREEN_CONFIRMATIONS = 2`, `DEFAULT_BRIGHTNESS_RATIO = 0.75`.
- **Bus invariant:** `EventBus.publish()` must never block. Sinks offer into bounded queues and drop on overflow.
- **No absolute coordinates for anything inside the death modal** — it shifts ~46px vertically. Locate by template match.
- The full test suite must pass with **no emulator attached**. Device access is mocked.
- Never commit without the user asking. Each task ends with a prepared commit; run it only when told.

---

### Task 1: Phase 0 — lock in corrected templates and fixtures

The templates in `templates/` were re-cut from a live run frame during investigation (the originals were cropped from the death modal, which is the bug this plan fixes). `tests/fixtures/` holds the four captured frames. This task pins that work down with a test that fails loudly if a template is ever re-cut badly.

**Files:**
- Create: `tests/test_fixtures.py`
- Modify: `pyproject.toml` (dev dependency)

**Interfaces:**
- Consumes: nothing
- Produces: `tests/fixtures/{main_menu,in_run_lit,game_over,game_over_fade}.png`, all 1080x2400 BGR frames readable by `cv2.imread`

- [ ] **Step 1: Add pytest as a dev dependency**

```bash
uv add --dev pytest
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_fixtures.py`:

```python
"""Fixtures are golden data. If these fail, a template or capture drifted."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"
EXPECTED_RESOLUTION = (1080, 2400)  # width, height


@pytest.mark.parametrize(
    "name", ["main_menu", "in_run_lit", "game_over", "game_over_fade"]
)
def test_fixture_resolution(name: str) -> None:
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    height, width = img.shape[:2]
    assert (width, height) == EXPECTED_RESOLUTION


def test_upgrade_templates_are_lit_not_dimmed() -> None:
    """Regression guard for the originating bug.

    The original templates were cropped from the death modal, where every
    label sits in a dimmed backdrop. A lit crop of these labels measures
    ~50-58 mean grey; the dimmed equivalent measures ~14. If someone re-cuts
    a template from a game-over frame, this catches it.
    """
    for path in sorted(TEMPLATES.glob("upgrade_*.png")):
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        assert img is not None, f"unreadable template: {path}"
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean()
        assert grey > 40.0, (
            f"{path.name} has mean grey {grey:.2f} - it looks like it was "
            "cropped from a dimmed frame. Re-cut it from a live run."
        )
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_fixtures.py -v`
Expected: all PASS (fixtures and templates are already in place)

- [ ] **Step 4: Prepare the commit (run only when the user approves)**

```bash
git add templates/ tests/ docs/ pyproject.toml uv.lock
git commit -m "fix: re-cut upgrade templates from a lit in-run frame

The originals were cropped from the GAME STATS death modal, where every
label sits in the dimmed backdrop. Because TM_CCOEFF_NORMED normalises out
brightness, they scored 1.000 against the death modal itself, and the
brightness gate's baseline was the dimmed value - so the bot would tap
straight through the modal.

Adds screen anchors, golden fixtures, and a regression test."
```

---

### Task 2: Event types and the EventBus

**Files:**
- Create: `events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Event` (frozen dataclass base, fields `seq: int`, `ts: float`)
  - Subclasses: `ScreenChanged`, `ScanCompleted`, `Tapped`, `Skipped`, `RunStarted`, `RunEnded`, `Navigated`, `UnknownScreen`, `BotError`
  - `Event.type -> str` (the class name)
  - `EventBus(start_seq: int = 0)`, `.subscribe(sink) -> None`, `.publish(event: Event) -> Event`, `.dropped: int`
  - `Sink` protocol with `offer(event: Event) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_events.py`:

```python
from __future__ import annotations

import events


class RecordingSink:
    def __init__(self) -> None:
        self.seen: list[events.Event] = []

    def offer(self, event: events.Event) -> bool:
        self.seen.append(event)
        return True


def test_publish_stamps_monotonic_seq_and_wall_clock() -> None:
    bus = events.EventBus()
    sink = RecordingSink()
    bus.subscribe(sink)

    bus.publish(events.Navigated(target="RETRY"))
    bus.publish(events.Navigated(target="BATTLE"))

    assert [e.seq for e in sink.seen] == [1, 2]
    assert all(e.ts > 0 for e in sink.seen)


def test_publish_seeds_seq_from_start() -> None:
    """seq is seeded from storage so a restart cannot collide on the PK."""
    bus = events.EventBus(start_seq=41)
    sink = RecordingSink()
    bus.subscribe(sink)

    stamped = bus.publish(events.Navigated(target="RETRY"))

    assert stamped.seq == 42


def test_event_type_is_the_class_name() -> None:
    assert events.Navigated(target="RETRY").type == "Navigated"


def test_publish_fans_out_to_every_sink() -> None:
    bus = events.EventBus()
    a, b = RecordingSink(), RecordingSink()
    bus.subscribe(a)
    bus.subscribe(b)

    bus.publish(events.Navigated(target="RETRY"))

    assert len(a.seen) == 1
    assert len(b.seen) == 1


def test_refused_offers_increment_the_dropped_counter() -> None:
    class RefusingSink:
        def offer(self, event: events.Event) -> bool:
            return False

    bus = events.EventBus()
    bus.subscribe(RefusingSink())

    bus.publish(events.Navigated(target="RETRY"))
    bus.publish(events.Navigated(target="BATTLE"))

    assert bus.dropped == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'events'`

- [ ] **Step 3: Write minimal implementation**

Create `events.py`:

```python
"""Typed events and the bus that fans them out to sinks.

The bus is the seam between the scan loop and everything that observes it.
Its one hard invariant: publish() never blocks. Sinks offer into bounded
queues and drop on overflow, so a slow browser or a locked database can
never stall the bot.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base event. The bus stamps seq and ts on publish."""

    seq: int = 0
    ts: float = 0.0

    @property
    def type(self) -> str:
        return type(self).__name__


@dataclass(frozen=True, kw_only=True)
class ScreenChanged(Event):
    prev: str
    curr: str
    confidence: float
    scores: dict[str, float]


@dataclass(frozen=True, kw_only=True)
class ScanCompleted(Event):
    screen: str
    duration_ms: float
    wallet: int | None = None


@dataclass(frozen=True, kw_only=True)
class Tapped(Event):
    action: str
    x: int
    y: int
    score: float
    price: int | None = None
    wallet: int | None = None


@dataclass(frozen=True, kw_only=True)
class Skipped(Event):
    action: str
    reason: str  # screen_gated | dimmed | unaffordable | cooldown
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class RunStarted(Event):
    run_id: int


@dataclass(frozen=True, kw_only=True)
class RunEnded(Event):
    run_id: int
    duration: float
    wave: int | None = None
    coins: int | None = None
    abandoned: bool = False


@dataclass(frozen=True, kw_only=True)
class Navigated(Event):
    target: str  # RETRY | BATTLE


@dataclass(frozen=True, kw_only=True)
class UnknownScreen(Event):
    snapshot_path: str
    best_anchor: str
    best_score: float


@dataclass(frozen=True, kw_only=True)
class BotError(Event):
    message: str
    traceback: str = ""


class Sink(Protocol):
    """A consumer of events. offer() MUST NOT block."""

    def offer(self, event: Event) -> bool:
        """Accept the event, or return False if it was dropped."""
        ...


class EventBus:
    """Thread-safe fan-out. publish() never blocks."""

    def __init__(self, start_seq: int = 0) -> None:
        self._counter = itertools.count(start_seq + 1)
        self._lock = threading.Lock()
        self._sinks: list[Sink] = []
        self.dropped = 0

    def subscribe(self, sink: Sink) -> None:
        with self._lock:
            self._sinks.append(sink)

    def publish(self, event: Event) -> Event:
        with self._lock:
            stamped = replace(event, seq=next(self._counter), ts=time.time())
            sinks = list(self._sinks)

        for sink in sinks:
            if not sink.offer(stamped):
                with self._lock:
                    self.dropped += 1
        return stamped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_events.py -v`
Expected: 5 passed

- [ ] **Step 5: Prepare the commit**

```bash
git add events.py tests/test_events.py
git commit -m "feat: add typed events and non-blocking EventBus"
```

---

### Task 3: QueueSink and the back-pressure invariant

The bus invariant is only real if sinks actually honour it. This task provides the base class every sink inherits, and tests the invariant directly.

**Files:**
- Create: `sinks/__init__.py`, `sinks/base.py`
- Test: `tests/test_queue_sink.py`

**Interfaces:**
- Consumes: `events.Event` from Task 2
- Produces: `sinks.base.QueueSink(maxsize: int = 1000)` with `.offer(event) -> bool`, `.start() -> None`, `.close() -> None`, and an abstract `.handle(event: Event) -> None` subclasses override

- [ ] **Step 1: Write the failing test**

Create `tests/test_queue_sink.py`:

```python
from __future__ import annotations

import threading
import time

import events
from sinks.base import QueueSink


class SlowSink(QueueSink):
    """Blocks in handle() until released, to simulate a stalled consumer."""

    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize=maxsize)
        self.release = threading.Event()
        self.handled: list[events.Event] = []

    def handle(self, event: events.Event) -> None:
        self.release.wait(timeout=5)
        self.handled.append(event)


def test_offer_never_blocks_when_the_queue_is_full() -> None:
    """The invariant that keeps a stalled sink from freezing the bot."""
    sink = SlowSink(maxsize=2)
    sink.start()
    try:
        started = time.monotonic()
        results = [sink.offer(events.Navigated(target="RETRY")) for _ in range(50)]
        elapsed = time.monotonic() - started

        assert elapsed < 0.5, "offer() blocked - the bus invariant is broken"
        assert results.count(False) > 0, "expected overflow to be refused"
    finally:
        sink.release.set()
        sink.close()


def test_handled_events_reach_the_consumer() -> None:
    class Collector(QueueSink):
        def __init__(self) -> None:
            super().__init__(maxsize=10)
            self.handled: list[events.Event] = []
            self.done = threading.Event()

        def handle(self, event: events.Event) -> None:
            self.handled.append(event)
            self.done.set()

    sink = Collector()
    sink.start()
    try:
        sink.offer(events.Navigated(target="BATTLE"))
        assert sink.done.wait(timeout=2)
        assert sink.handled[0].target == "BATTLE"
    finally:
        sink.close()


def test_a_raising_handler_does_not_kill_the_consumer_thread() -> None:
    class Exploding(QueueSink):
        def __init__(self) -> None:
            super().__init__(maxsize=10)
            self.survived = threading.Event()
            self.calls = 0

        def handle(self, event: events.Event) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            self.survived.set()

    sink = Exploding()
    sink.start()
    try:
        sink.offer(events.Navigated(target="RETRY"))
        sink.offer(events.Navigated(target="BATTLE"))
        assert sink.survived.wait(timeout=2)
    finally:
        sink.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_queue_sink.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sinks'`

- [ ] **Step 3: Write minimal implementation**

Create `sinks/__init__.py` (empty file), then `sinks/base.py`:

```python
"""Base class for every event sink.

Sinks own a bounded queue and a consumer thread. offer() only ever does
put_nowait, so the publishing thread - the scan loop - is never blocked by
a slow consumer. Overflow is dropped and counted by the bus.
"""

from __future__ import annotations

import logging
import queue
import threading

import events

logger = logging.getLogger("tower_bot.sinks")

_SHUTDOWN = object()


class QueueSink:
    def __init__(self, maxsize: int = 1000) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._consume, name=type(self).__name__, daemon=True
        )
        self._thread.start()

    def offer(self, event: events.Event) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            return False

    def close(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._queue.put(_SHUTDOWN)
        self._thread.join(timeout=timeout)
        self._thread = None

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SHUTDOWN:
                return
            try:
                self.handle(item)
            except Exception:  # noqa: BLE001 - one bad event must not kill the sink
                logger.exception("sink %s failed handling an event", type(self).__name__)

    def handle(self, event: events.Event) -> None:
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_queue_sink.py -v`
Expected: 3 passed

- [ ] **Step 5: Prepare the commit**

```bash
git add sinks/ tests/test_queue_sink.py
git commit -m "feat: add QueueSink base with non-blocking offer"
```

---

### Task 4: Extract the device layer

Pure extraction from `tower_bot.py`. No behaviour change.

**Files:**
- Create: `device.py`
- Modify: `tower_bot.py` (remove the moved functions, import from `device`)
- Test: `tests/test_device.py`

**Interfaces:**
- Consumes: `config.DEVICE_HOST`, `config.DEVICE_PORT`, `config.ADB_HOST`, `config.ADB_PORT`
- Produces:
  - `device.EmulatorError(RuntimeError)`
  - `device.Image` = `NDArray[np.uint8]`
  - `device.connect_device(host, port, adb_host, adb_port) -> AdbDevice`
  - `device.capture_screen(dev) -> Image`
  - `device.tap(dev, x: int, y: int) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_device.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image as PILImage

import device


def test_capture_screen_converts_rgb_to_bgr() -> None:
    """adbutils returns PIL RGB; OpenCV needs BGR. A red pixel proves the swap."""
    fake = MagicMock()
    fake.screenshot.return_value = PILImage.new("RGB", (4, 4), (255, 0, 0))

    frame = device.capture_screen(fake)

    assert frame.shape == (4, 4, 3)
    # BGR: blue=0, green=0, red=255
    assert tuple(frame[0, 0]) == (0, 0, 255)


def test_capture_screen_demands_errors_not_black_frames() -> None:
    """error_ok=False matters: the default returns a black image on failure,
    which would leave the bot scanning blank frames forever."""
    fake = MagicMock()
    fake.screenshot.return_value = PILImage.new("RGB", (2, 2), (0, 0, 0))

    device.capture_screen(fake)

    fake.screenshot.assert_called_once_with(error_ok=False)


def test_tap_delegates_to_the_device() -> None:
    fake = MagicMock()
    device.tap(fake, 100, 200)
    fake.click.assert_called_once_with(100, 200)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_device.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'device'`

- [ ] **Step 3: Move the code**

Create `device.py` by moving `EmulatorError`, `Image`, `connect_device`, `capture_screen`, and `tap` verbatim out of `tower_bot.py` (they are currently under the "Device layer" comment block). Keep the docstrings and the `error_ok=False` comment — that comment records a real trap.

Add at the top of `device.py`:

```python
"""ADB device access: connect, capture, tap.

Everything here goes through ADB, so the emulator window never needs focus.
Verified: with the emulator app fully hidden (not merely unfocused),
screencap returns live frames and input tap registers.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
from adbutils import AdbClient, AdbDevice, AdbError
from numpy.typing import NDArray

import config

logger = logging.getLogger("tower_bot.device")

Image = NDArray[np.uint8]
```

Then in `tower_bot.py`, delete those definitions and add:

```python
from device import EmulatorError, Image, capture_screen, connect_device, tap
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass. Also confirm the entry point still imports: `uv run python -c "import tower_bot"`

- [ ] **Step 5: Prepare the commit**

```bash
git add device.py tower_bot.py tests/test_device.py
git commit -m "refactor: extract device layer into device.py"
```

---

### Task 5: Extract the vision layer

Pure extraction. No behaviour change.

**Files:**
- Create: `vision.py`
- Modify: `tower_bot.py`
- Test: `tests/test_vision.py`

**Interfaces:**
- Consumes: `device.Image`
- Produces:
  - `vision.Match` (NamedTuple: `center: tuple[int,int]`, `score: float`, `top_left: tuple[int,int]`)
  - `vision.TemplateCache(template_dir: Path)` with `.get(name: str | Path) -> Image`
  - `vision.locate_template(screen, template, threshold) -> Match | None`
  - `vision.best_score(screen, template) -> tuple[float, tuple[int, int]]`
  - `vision.mean_brightness(image) -> float`
  - `vision.brightness_ratio(screen, match, template) -> float`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vision.py`:

```python
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import vision

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


@pytest.fixture
def cache() -> vision.TemplateCache:
    return vision.TemplateCache(TEMPLATES)


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


def test_locate_finds_a_lit_upgrade_in_a_live_run(cache: vision.TemplateCache) -> None:
    match = vision.locate_template(
        frame("in_run_lit"), cache.get("upgrade_damage.png"), threshold=0.9
    )
    assert match is not None
    assert match.score > 0.99


def test_locate_returns_none_below_threshold(cache: vision.TemplateCache) -> None:
    """The upgrade panel is absent from the main menu."""
    match = vision.locate_template(
        frame("main_menu"), cache.get("upgrade_damage.png"), threshold=0.9
    )
    assert match is None


def test_score_alone_cannot_separate_in_run_from_game_over(
    cache: vision.TemplateCache,
) -> None:
    """The core reason the brightness gate exists.

    The upgrade panel is still rendered behind the death modal, and
    TM_CCOEFF_NORMED normalises out brightness - so the template scores ~1.0
    on BOTH screens. Only brightness separates them.
    """
    template = cache.get("upgrade_damage.png")
    in_run = vision.locate_template(frame("in_run_lit"), template, threshold=0.9)
    game_over = vision.locate_template(frame("game_over"), template, threshold=0.9)

    assert in_run is not None and game_over is not None
    assert in_run.score > 0.99
    assert game_over.score > 0.99  # identical score, different screen


def test_brightness_ratio_separates_lit_from_dimmed(
    cache: vision.TemplateCache,
) -> None:
    """Measured: 1.00 lit, 0.28 dimmed. The 0.75 threshold sits between."""
    template = cache.get("upgrade_damage.png")

    lit_frame = frame("in_run_lit")
    lit_match = vision.locate_template(lit_frame, template, threshold=0.9)
    lit = vision.brightness_ratio(lit_frame, lit_match, template)

    dim_frame = frame("game_over")
    dim_match = vision.locate_template(dim_frame, template, threshold=0.9)
    dim = vision.brightness_ratio(dim_frame, dim_match, template)

    assert lit == pytest.approx(1.0, abs=0.05)
    assert dim < 0.4
    assert dim < 0.75 < lit


def test_cache_returns_the_same_object_twice(cache: vision.TemplateCache) -> None:
    assert cache.get("upgrade_damage.png") is cache.get("upgrade_damage.png")


def test_missing_template_raises(cache: vision.TemplateCache) -> None:
    with pytest.raises(FileNotFoundError):
        cache.get("does_not_exist.png")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vision.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vision'`

- [ ] **Step 3: Write the implementation**

Create `vision.py`. Move `Match`, `TemplateCache`, `locate_template`, and `mean_brightness` out of `tower_bot.py`, then add two new functions that lift logic currently buried inside `TowerBot`:

```python
def best_score(screen: Image, template: Image) -> tuple[float, tuple[int, int]]:
    """Best match score and its top-left position, ignoring any threshold."""
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return float(max_val), max_loc


def brightness_ratio(screen: Image, match: Match, template: Image) -> float:
    """Brightness of the matched region relative to the template's own.

    TM_CCOEFF_NORMED is blind to brightness, so a dimmed button scores the
    same as a lit one. This is what tells them apart.
    """
    tpl_h, tpl_w = template.shape[:2]
    x, y = match.top_left
    region = screen[y : y + tpl_h, x : x + tpl_w]
    template_level = mean_brightness(template)
    if template_level <= 0.0:  # all-black template: nothing to compare against
        return 1.0
    return mean_brightness(region) / template_level
```

Rewrite `locate_template` to use `best_score` so there is one matching path:

```python
def locate_template(screen: Image, template: Image, threshold: float) -> Match | None:
    """Return the best match above threshold, or None."""
    screen_h, screen_w = screen.shape[:2]
    tpl_h, tpl_w = template.shape[:2]
    if tpl_h > screen_h or tpl_w > screen_w:
        logger.warning(
            "Template (%dx%d) is larger than the screen (%dx%d) - was it "
            "captured at a different emulator resolution?",
            tpl_w, tpl_h, screen_w, screen_h,
        )
        return None

    score, top_left = best_score(screen, template)
    if score < threshold:
        return None

    center = (top_left[0] + tpl_w // 2, top_left[1] + tpl_h // 2)
    return Match(center=center, score=score, top_left=top_left)
```

Remove the now-duplicated `_best_score` and `_brightness_ratio` methods from `TowerBot` and have it call the module functions.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass

- [ ] **Step 5: Prepare the commit**

```bash
git add vision.py tower_bot.py tests/test_vision.py
git commit -m "refactor: extract vision layer into vision.py"
```

---

### Task 6: Screen classification

**Files:**
- Create: `screens.py`
- Modify: `config.py` (add anchor config)
- Test: `tests/test_screens.py`

**Interfaces:**
- Consumes: `vision.TemplateCache`, `vision.best_score`, `device.Image`
- Produces:
  - `screens.ScreenState` (str Enum: `MAIN_MENU`, `IN_RUN`, `GAME_OVER`, `UNKNOWN`)
  - `screens.ScreenReading` (frozen dataclass: `state: ScreenState`, `confidence: float`, `scores: dict[str, float]`, `top_left: tuple[int,int] | None`)
  - `screens.classify(screen, cache, threshold=config.ANCHOR_THRESHOLD) -> ScreenReading`
- Config added: `ANCHOR_THRESHOLD: float = 0.8`, `SCREEN_CONFIRMATIONS: int = 2`, `SCREEN_ANCHORS: dict[str, str]`

- [ ] **Step 1: Add the anchor configuration**

Append to `config.py`:

```python
# --- Screen recognition -----------------------------------------------------
# Anchors are small crops unique to one screen. Measured separation on the
# golden fixtures: 1.000 on the correct screen, <=0.462 on every wrong one,
# so 0.8 has a wide margin in both directions.
ANCHOR_THRESHOLD: float = 0.8

# A transition is only declared after this many consecutive identical
# readings. Capture lands inside the death modal's fade animation (the same
# region measures 0.74 mid-fade and 0.28 fully dimmed), and without debounce
# those frames produce phantom transitions that corrupt run boundaries.
SCREEN_CONFIRMATIONS: int = 2

SCREEN_ANCHORS: dict[str, str] = {
    "MAIN_MENU": "screens/main_menu.png",
    "IN_RUN": "screens/in_run.png",
    "GAME_OVER": "screens/game_over.png",
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_screens.py`:

```python
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import screens
import vision

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


@pytest.fixture
def cache() -> vision.TemplateCache:
    return vision.TemplateCache(TEMPLATES)


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("main_menu", screens.ScreenState.MAIN_MENU),
        ("in_run_lit", screens.ScreenState.IN_RUN),
        ("game_over", screens.ScreenState.GAME_OVER),
    ],
)
def test_classifies_each_fixture(
    cache: vision.TemplateCache, fixture: str, expected: screens.ScreenState
) -> None:
    assert screens.classify(frame(fixture), cache).state is expected


@pytest.mark.parametrize("fixture", ["main_menu", "in_run_lit", "game_over"])
def test_winning_anchor_beats_the_runner_up_by_a_wide_margin(
    cache: vision.TemplateCache, fixture: str
) -> None:
    """A correctness-only test would still pass with a badly re-cut anchor.
    Asserting the margin is what actually guards the thresholds."""
    reading = screens.classify(frame(fixture), cache)
    ranked = sorted(reading.scores.values(), reverse=True)

    assert ranked[0] >= 0.8, f"winner {ranked[0]:.3f} below threshold"
    assert ranked[1] <= 0.5, f"runner-up {ranked[1]:.3f} too close"


def test_unrecognised_frame_is_unknown(cache: vision.TemplateCache) -> None:
    blank = frame("main_menu").copy()
    blank[:, :] = 0

    reading = screens.classify(blank, cache)

    assert reading.state is screens.ScreenState.UNKNOWN


def test_reading_reports_every_anchor_score(cache: vision.TemplateCache) -> None:
    reading = screens.classify(frame("in_run_lit"), cache)
    assert set(reading.scores) == {"MAIN_MENU", "IN_RUN", "GAME_OVER"}


def test_game_over_reading_carries_the_anchor_position(
    cache: vision.TemplateCache,
) -> None:
    """Death-modal regions are anchor-relative because the modal moves ~46px."""
    reading = screens.classify(frame("game_over"), cache)
    assert reading.top_left is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_screens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screens'`

- [ ] **Step 4: Write the implementation**

Create `screens.py`:

```python
"""Which screen is the game on?

Anchors are small crops unique to one screen. Every scan matches all of them
full-frame and takes the argmax; below ANCHOR_THRESHOLD the answer is UNKNOWN
and the bot holds.

Full-frame rather than region-of-interest is deliberate: ~100ms for three
anchors against a 2s scan interval is not worth optimising, and the death
modal moves vertically, so ROI padding would need tuning for no gain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import config
import vision
from device import Image


class ScreenState(str, Enum):
    MAIN_MENU = "MAIN_MENU"
    IN_RUN = "IN_RUN"
    GAME_OVER = "GAME_OVER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ScreenReading:
    """One frame's classification, with every anchor's score for debugging."""

    state: ScreenState
    confidence: float
    scores: dict[str, float]
    top_left: tuple[int, int] | None = None


def classify(
    screen: Image,
    cache: vision.TemplateCache,
    threshold: float = config.ANCHOR_THRESHOLD,
) -> ScreenReading:
    scores: dict[str, float] = {}
    positions: dict[str, tuple[int, int]] = {}

    for name, template_path in config.SCREEN_ANCHORS.items():
        score, top_left = vision.best_score(screen, cache.get(template_path))
        scores[name] = score
        positions[name] = top_left

    winner = max(scores, key=lambda name: scores[name])
    confidence = scores[winner]

    if confidence < threshold:
        return ScreenReading(ScreenState.UNKNOWN, confidence, scores)

    return ScreenReading(
        ScreenState(winner), confidence, scores, top_left=positions[winner]
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_screens.py -v`
Expected: 9 passed

- [ ] **Step 6: Prepare the commit**

```bash
git add screens.py config.py tests/test_screens.py
git commit -m "feat: classify the current screen from anchor templates"
```

---

### Task 7: Debounced screen tracker

**Files:**
- Modify: `screens.py`
- Test: `tests/test_screen_tracker.py`

**Interfaces:**
- Consumes: `screens.ScreenState`, `screens.ScreenReading`
- Produces: `screens.ScreenTracker(confirmations: int = config.SCREEN_CONFIRMATIONS)` with `.state: ScreenState` and `.observe(reading: ScreenReading) -> ScreenState | None` (returns the new state on a confirmed transition, else `None`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen_tracker.py`:

```python
from __future__ import annotations

import pytest

from screens import ScreenReading, ScreenState, ScreenTracker


def reading(state: ScreenState) -> ScreenReading:
    return ScreenReading(state, confidence=1.0, scores={})


def test_starts_unknown() -> None:
    assert ScreenTracker().state is ScreenState.UNKNOWN


def test_a_single_reading_does_not_transition() -> None:
    tracker = ScreenTracker(confirmations=2)
    assert tracker.observe(reading(ScreenState.MAIN_MENU)) is None
    assert tracker.state is ScreenState.UNKNOWN


def test_two_consecutive_readings_confirm_the_transition() -> None:
    tracker = ScreenTracker(confirmations=2)
    tracker.observe(reading(ScreenState.MAIN_MENU))

    assert tracker.observe(reading(ScreenState.MAIN_MENU)) is ScreenState.MAIN_MENU
    assert tracker.state is ScreenState.MAIN_MENU


def test_an_interrupted_sequence_does_not_transition() -> None:
    """A mid-fade frame between two stable ones must not slip through."""
    tracker = ScreenTracker(confirmations=2)
    tracker.observe(reading(ScreenState.MAIN_MENU))
    tracker.observe(reading(ScreenState.MAIN_MENU))

    tracker.observe(reading(ScreenState.IN_RUN))
    tracker.observe(reading(ScreenState.GAME_OVER))  # interrupts the run of IN_RUN

    assert tracker.state is ScreenState.MAIN_MENU


def test_staying_on_the_same_screen_reports_no_transition() -> None:
    tracker = ScreenTracker(confirmations=2)
    tracker.observe(reading(ScreenState.IN_RUN))
    tracker.observe(reading(ScreenState.IN_RUN))

    assert tracker.observe(reading(ScreenState.IN_RUN)) is None
    assert tracker.observe(reading(ScreenState.IN_RUN)) is None


def test_consecutive_transitions_both_fire() -> None:
    tracker = ScreenTracker(confirmations=2)
    for _ in range(2):
        tracker.observe(reading(ScreenState.MAIN_MENU))
    for _ in range(2):
        result = tracker.observe(reading(ScreenState.IN_RUN))

    assert result is ScreenState.IN_RUN
    assert tracker.state is ScreenState.IN_RUN


def test_unknown_is_a_state_like_any_other() -> None:
    """UNKNOWN still needs confirming, so one bad frame cannot halt the bot."""
    tracker = ScreenTracker(confirmations=2)
    for _ in range(2):
        tracker.observe(reading(ScreenState.IN_RUN))

    assert tracker.observe(reading(ScreenState.UNKNOWN)) is None
    assert tracker.state is ScreenState.IN_RUN

    assert tracker.observe(reading(ScreenState.UNKNOWN)) is ScreenState.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_screen_tracker.py -v`
Expected: FAIL with `ImportError: cannot import name 'ScreenTracker'`

- [ ] **Step 3: Write the implementation**

Append to `screens.py`:

```python
class ScreenTracker:
    """Debounces raw readings into confirmed screen transitions.

    Capture lands inside the death modal's fade animation, so a single frame
    is not trustworthy. A transition is declared only after `confirmations`
    consecutive identical readings; an interrupted run resets the count.
    """

    def __init__(self, confirmations: int = config.SCREEN_CONFIRMATIONS) -> None:
        self._confirmations = confirmations
        self.state = ScreenState.UNKNOWN
        self._pending: ScreenState | None = None
        self._streak = 0

    def observe(self, reading: ScreenReading) -> ScreenState | None:
        """Feed one reading. Returns the new state on a confirmed transition."""
        if reading.state is self.state:
            self._pending = None
            self._streak = 0
            return None

        if reading.state is self._pending:
            self._streak += 1
        else:
            self._pending = reading.state
            self._streak = 1

        if self._streak < self._confirmations:
            return None

        self.state = reading.state
        self._pending = None
        self._streak = 0
        return self.state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_screen_tracker.py -v`
Expected: 7 passed

- [ ] **Step 5: Prepare the commit**

```bash
git add screens.py tests/test_screen_tracker.py
git commit -m "feat: debounce screen readings into confirmed transitions"
```

---

### Task 8: Structured log sink

**Files:**
- Create: `sinks/log.py`
- Test: `tests/test_log_sink.py`

**Interfaces:**
- Consumes: `events.Event` subclasses, `sinks.base.QueueSink`
- Produces: `sinks.log.render(event: events.Event) -> str` and `sinks.log.LogSink(stream=sys.stdout)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_log_sink.py`:

```python
from __future__ import annotations

import events
from sinks.log import render


def stamped(event: events.Event) -> events.Event:
    bus = events.EventBus()
    return bus.publish(event)


def test_renders_a_tap_with_action_and_position() -> None:
    line = render(stamped(events.Tapped(action="Damage", x=840, y=1520, score=0.94)))
    assert "TAP" in line
    assert "Damage" in line
    assert "840" in line and "1520" in line


def test_renders_a_skip_with_its_reason() -> None:
    line = render(
        stamped(events.Skipped(action="Damage", reason="unaffordable", detail="$10 > $3"))
    )
    assert "SKIP" in line
    assert "unaffordable" in line


def test_renders_a_screen_transition_with_both_states() -> None:
    line = render(
        stamped(
            events.ScreenChanged(
                prev="IN_RUN", curr="GAME_OVER", confidence=0.998, scores={}
            )
        )
    )
    assert "IN_RUN" in line and "GAME_OVER" in line


def test_renders_unknown_events_without_raising() -> None:
    """A new event type must degrade gracefully, never crash the sink."""

    class Surprise(events.Event):
        pass

    assert isinstance(render(stamped(Surprise())), str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_log_sink.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sinks.log'`

- [ ] **Step 3: Write the implementation**

Create `sinks/log.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_log_sink.py -v`
Expected: 4 passed

- [ ] **Step 5: Prepare the commit**

```bash
git add sinks/log.py tests/test_log_sink.py
git commit -m "feat: add structured log sink"
```

---

### Task 9: Wire phase 1 — the bot reports its screen

At the end of this task `--once` reports the detected screen. Tapping behaviour is deliberately unchanged; gating arrives in Task 10.

**Files:**
- Modify: `tower_bot.py`
- Test: `tests/test_bot_reporting.py`

**Interfaces:**
- Consumes: everything from Tasks 2-8
- Produces: `TowerBot(device, templates, bus, ...)` publishing `ScanCompleted` every scan and `ScreenChanged` on confirmed transitions; `TowerBot.screen_state -> ScreenState`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_reporting.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
import screens
import vision
from tower_bot import TowerBot

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


class Recorder:
    def __init__(self) -> None:
        self.seen: list[events.Event] = []

    def offer(self, event: events.Event) -> bool:
        self.seen.append(event)
        return True

    def of(self, kind: type) -> list[events.Event]:
        return [e for e in self.seen if isinstance(e, kind)]


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


def make_bot(fixture: str) -> tuple[TowerBot, Recorder, MagicMock]:
    dev = MagicMock()
    bus = events.EventBus()
    rec = Recorder()
    bus.subscribe(rec)
    bot = TowerBot(
        device=dev, templates=vision.TemplateCache(TEMPLATES), bus=bus
    )
    bot._screen = frame(fixture)  # inject the frame; no emulator needed
    return bot, rec, dev


def test_every_scan_publishes_scan_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, _ = make_bot("in_run_lit")
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)

    bot.run_once()

    assert len(rec.of(events.ScanCompleted)) == 1


def test_confirmed_transition_publishes_screen_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot, rec, _ = make_bot("in_run_lit")
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)

    bot.run_once()
    assert rec.of(events.ScreenChanged) == []  # one reading is not enough

    bot.run_once()
    changed = rec.of(events.ScreenChanged)
    assert len(changed) == 1
    assert changed[0].curr == "IN_RUN"
    assert bot.screen_state is screens.ScreenState.IN_RUN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot_reporting.py -v`
Expected: FAIL — `TowerBot.__init__()` does not accept `bus`

- [ ] **Step 3: Write the implementation**

In `tower_bot.py`, change `TowerBot.__init__` to accept the bus and a tracker, and rewrite `run_once`:

```python
    def __init__(
        self,
        device: AdbDevice,
        templates: vision.TemplateCache,
        bus: events.EventBus,
        click_cooldown: float = config.CLICK_COOLDOWN_SECONDS,
    ) -> None:
        self.device = device
        self.templates = templates
        self.bus = bus
        self.click_cooldown = click_cooldown
        self.tracker = screens.ScreenTracker()
        self._screen: Image | None = None
        self._last_click: dict[str, float] = {}
        self._running = True

    @property
    def screen_state(self) -> screens.ScreenState:
        return self.tracker.state

    def run_once(self) -> bool:
        started = time.monotonic()
        self.refresh_screen()

        reading = screens.classify(self.screen, self.templates)
        previous = self.tracker.state
        if self.tracker.observe(reading) is not None:
            self.bus.publish(
                events.ScreenChanged(
                    prev=previous.value,
                    curr=self.tracker.state.value,
                    confidence=reading.confidence,
                    scores=reading.scores,
                )
            )

        clicked = False
        for action in config.ACTIONS:
            if self.find_and_click_image(
                action.template, action.threshold, action.brightness_ratio
            ):
                clicked = True

        self.bus.publish(
            events.ScanCompleted(
                screen=self.tracker.state.value,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        )
        return clicked
```

Replace the `logger.info("Clicked ...")` call inside `find_and_click_image` with a `Tapped` publish:

```python
        tap(self.device, x, y)
        self._last_click[key] = now
        self.bus.publish(
            events.Tapped(action=key, x=x, y=y, score=score)
        )
        return True
```

In `main()`, build the bus and sink before the bot:

```python
    bus = events.EventBus()
    log_sink = LogSink()
    log_sink.start()
    bus.subscribe(log_sink)

    bot = TowerBot(device=device, templates=vision.TemplateCache(config.TEMPLATE_DIR), bus=bus)
```

and call `log_sink.close()` before returning.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass

- [ ] **Step 5: Smoke-test against the emulator**

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"
uv run tower_bot.py --once
```
Expected: a `SCAN   screen=...` line naming the screen the game is actually on.

- [ ] **Step 6: Prepare the commit**

```bash
git add tower_bot.py tests/test_bot_reporting.py
git commit -m "feat: publish scan and screen-transition events"
```

---

### Task 10: Gate every action on the screen state

This is the task that fixes the originating bug.

**Files:**
- Modify: `tower_bot.py`, `config.py`
- Create: `affordability.py`
- Test: `tests/test_gating.py`

**Interfaces:**
- Consumes: `screens.ScreenState`, `vision.brightness_ratio`
- Produces:
  - `affordability.AffordabilityCheck` protocol: `.affordable(screen, match, template) -> tuple[bool, str]` returning `(ok, detail)`
  - `affordability.BrightnessAffordability(ratio: float)`
  - `TowerBot.find_and_click_image` publishing `Skipped` with a reason instead of returning silently

- [ ] **Step 1: Write the failing test**

Create `tests/test_gating.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
import vision
from tower_bot import TowerBot

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


class Recorder:
    def __init__(self) -> None:
        self.seen: list[events.Event] = []

    def offer(self, event: events.Event) -> bool:
        self.seen.append(event)
        return True

    def of(self, kind: type) -> list[events.Event]:
        return [e for e in self.seen if isinstance(e, kind)]


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


def settled_bot(fixture: str, monkeypatch: pytest.MonkeyPatch):
    """A bot whose tracker has already confirmed the fixture's screen."""
    dev = MagicMock()
    bus = events.EventBus()
    rec = Recorder()
    bus.subscribe(rec)
    bot = TowerBot(device=dev, templates=vision.TemplateCache(TEMPLATES), bus=bus)
    bot._screen = frame(fixture)
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    bot.run_once()  # first reading
    bot.run_once()  # confirms the transition
    rec.seen.clear()
    return bot, rec, dev


def test_game_over_does_not_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the originating bug.

    The upgrade panel is still rendered behind the death modal and the
    templates score 1.000 against it. Asserting the ABSENCE of a tap is the
    point. The reason is screen_gated, because the screen gate fires before
    the brightness check ever runs - see test_brightness_gate_rejects_dimmed
    for direct coverage of the gate itself.
    """
    bot, rec, dev = settled_bot("game_over", monkeypatch)

    bot.run_once()

    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()
    assert {e.reason for e in rec.of(events.Skipped)} == {"screen_gated"}


def test_main_menu_does_not_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, dev = settled_bot("main_menu", monkeypatch)

    bot.run_once()

    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()


def test_in_run_taps_every_affordable_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()  # ignore cooldown from the settling scans

    bot.run_once()

    tapped = {e.action for e in rec.of(events.Tapped)}
    assert len(tapped) == 4


def test_brightness_gate_rejects_dimmed_regions() -> None:
    """Direct coverage of BrightnessAffordability, which the full-loop
    game-over test never reaches because screen gating fires first."""
    from affordability import BrightnessAffordability

    check = BrightnessAffordability(ratio=0.75)
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get("upgrade_damage.png")

    dim = frame("game_over")
    match = vision.locate_template(dim, template, threshold=0.9)
    ok, _ = check.affordable(dim, match, template)
    assert ok is False

    lit = frame("in_run_lit")
    match = vision.locate_template(lit, template, threshold=0.9)
    ok, _ = check.affordable(lit, match, template)
    assert ok is True


def test_cooldown_suppresses_a_repeat_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()

    bot.run_once()
    rec.seen.clear()
    bot.run_once()  # immediately again - all four are still cooling down

    assert rec.of(events.Tapped) == []
    assert {e.reason for e in rec.of(events.Skipped)} == {"cooldown"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gating.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'affordability'`

- [ ] **Step 3: Write the affordability strategy**

Create `affordability.py`:

```python
"""Is this upgrade actually buyable right now?

Phase 2 answers with brightness. Phase 3 replaces it with reading the wallet
and the price, which is exact. The protocol is the seam that makes the swap a
one-line change in the bot.
"""

from __future__ import annotations

from typing import Protocol

import config
import vision
from device import Image


class AffordabilityCheck(Protocol):
    def affordable(
        self, screen: Image, match: vision.Match, template: Image
    ) -> tuple[bool, str]:
        """Return (ok, human-readable detail)."""
        ...


class BrightnessAffordability:
    """Reject regions dimmer than the template they matched.

    TM_CCOEFF_NORMED normalises out brightness, so a dimmed button scores the
    same as a lit one. Measured on the fixtures: 1.00 lit, 0.28 dimmed.

    NOTE: this is verified against modal-dimming only. The greyed-out
    "cannot afford" state was never captured, so its separation is unproven.
    Phase 3 removes the dependency by reading the numbers instead.
    """

    def __init__(self, ratio: float = config.DEFAULT_BRIGHTNESS_RATIO) -> None:
        self.ratio = ratio

    def affordable(
        self, screen: Image, match: vision.Match, template: Image
    ) -> tuple[bool, str]:
        if self.ratio <= 0.0:
            return True, ""
        measured = vision.brightness_ratio(screen, match, template)
        if measured < self.ratio:
            return False, f"brightness {measured:.2f} < {self.ratio:.2f}"
        return True, ""
```

- [ ] **Step 4: Add the gate to the bot**

In `tower_bot.py`, accept the check and gate on screen state. Replace `find_and_click_image` with:

```python
    def find_and_click_image(
        self,
        template_path: str | Path,
        threshold: float = config.DEFAULT_THRESHOLD,
    ) -> bool:
        """Find the template on the current screen and tap it.

        Rejects are ordered cheapest-first, so a non-IN_RUN scan costs almost
        nothing.
        """
        key = str(template_path)

        if self.tracker.state is not screens.ScreenState.IN_RUN:
            self.bus.publish(
                events.Skipped(
                    action=key,
                    reason="screen_gated",
                    detail=f"screen is {self.tracker.state.value}",
                )
            )
            return False

        template = self.templates.get(template_path)
        match = vision.locate_template(self.screen, template, threshold)
        if match is None:
            return False

        ok, detail = self.affordability.affordable(self.screen, match, template)
        if not ok:
            self.bus.publish(
                events.Skipped(action=key, reason="dimmed", detail=detail)
            )
            return False

        now = time.monotonic()
        if now - self._last_click.get(key, 0.0) < self.click_cooldown:
            self.bus.publish(events.Skipped(action=key, reason="cooldown"))
            return False

        x, y = match.center
        tap(self.device, x, y)
        self._last_click[key] = now
        self.bus.publish(events.Tapped(action=key, x=x, y=y, score=match.score))
        return True
```

Add to `__init__`:

```python
        self.affordability: AffordabilityCheck = affordability_check or BrightnessAffordability()
```

with the parameter `affordability_check: AffordabilityCheck | None = None`.

Update the `run_once` action loop to drop the removed `brightness_ratio` argument:

```python
        for action in config.ACTIONS:
            if self.find_and_click_image(action.template, action.threshold):
                clicked = True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_gating.py -v`
Expected: 5 passed

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass

- [ ] **Step 7: Prepare the commit**

```bash
git add tower_bot.py affordability.py config.py tests/test_gating.py
git commit -m "fix: gate every action on the confirmed screen state

The bot no longer taps through the death modal. Actions fire only on
IN_RUN; every rejection publishes a Skipped event carrying its reason."
```

---

### Task 11: Snapshot unrecognised screens

**Files:**
- Modify: `tower_bot.py`, `config.py`
- Create: `snapshots.py`
- Test: `tests/test_snapshots.py`

**Interfaces:**
- Consumes: `device.Image`, `events.UnknownScreen`
- Produces: `snapshots.SnapshotWriter(directory: Path, min_interval: float = 30.0, keep: int = 50)` with `.maybe_write(image, now=None) -> Path | None`
- Config added: `UNKNOWN_DIR: Path`, `UNKNOWN_MIN_INTERVAL: float = 30.0`, `UNKNOWN_KEEP: int = 50`

- [ ] **Step 1: Write the failing test**

Create `tests/test_snapshots.py`:

```python
from __future__ import annotations

from pathlib import Path

import numpy as np

from snapshots import SnapshotWriter


def blank() -> np.ndarray:
    return np.zeros((40, 30, 3), dtype=np.uint8)


def test_writes_a_snapshot(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path)
    path = writer.maybe_write(blank(), now=1000.0)

    assert path is not None
    assert Path(path).exists()


def test_rate_limits_within_the_interval(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path, min_interval=30.0)

    assert writer.maybe_write(blank(), now=1000.0) is not None
    assert writer.maybe_write(blank(), now=1010.0) is None
    assert writer.maybe_write(blank(), now=1031.0) is not None


def test_prunes_to_the_keep_limit(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path, min_interval=0.0, keep=3)

    for i in range(6):
        writer.maybe_write(blank(), now=1000.0 + i)

    assert len(list(tmp_path.glob("*.png"))) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_snapshots.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'snapshots'`

- [ ] **Step 3: Write the implementation**

Create `snapshots.py`:

```python
"""Save frames the bot could not classify.

These are the diagnostic trail for UNKNOWN, and the raw material for adding
new screens later. Rate-limited and capped so a long unattended session
cannot fill the disk.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2

from device import Image

logger = logging.getLogger("tower_bot.snapshots")


class SnapshotWriter:
    def __init__(
        self, directory: Path, min_interval: float = 30.0, keep: int = 50
    ) -> None:
        self._dir = Path(directory)
        self._min_interval = min_interval
        self._keep = keep
        self._last = float("-inf")

    def maybe_write(self, image: Image, now: float | None = None) -> Path | None:
        moment = time.monotonic() if now is None else now
        if moment - self._last < self._min_interval:
            return None

        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{int(moment * 1000)}.png"
        cv2.imwrite(str(path), image)
        self._last = moment
        self._prune()
        return path

    def _prune(self) -> None:
        existing = sorted(self._dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
        for stale in existing[: max(0, len(existing) - self._keep)]:
            stale.unlink(missing_ok=True)
```

Add to `config.py`:

```python
# --- Unknown-screen snapshots ---------------------------------------------
UNKNOWN_DIR: Path = Path(__file__).parent / "unknown"
UNKNOWN_MIN_INTERVAL: float = 30.0
UNKNOWN_KEEP: int = 50
```

In `tower_bot.py`, publish an `UnknownScreen` event when the confirmed state becomes `UNKNOWN`, inside `run_once` just after the transition block:

```python
        if self.tracker.state is screens.ScreenState.UNKNOWN:
            path = self.snapshots.maybe_write(self.screen)
            if path is not None:
                best = max(reading.scores, key=lambda name: reading.scores[name])
                self.bus.publish(
                    events.UnknownScreen(
                        snapshot_path=str(path),
                        best_anchor=best,
                        best_score=reading.scores[best],
                    )
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_snapshots.py -v`
Expected: 3 passed

- [ ] **Step 5: Add `unknown/` to .gitignore and prepare the commit**

```bash
echo "unknown/" >> .gitignore
git add snapshots.py config.py tower_bot.py .gitignore tests/test_snapshots.py
git commit -m "feat: snapshot unrecognised screens for later triage"
```

---

### Task 12: Auto-navigate between runs

**Files:**
- Create: `templates/buttons/retry.png`, `templates/buttons/battle.png`, `navigate.py`
- Modify: `tower_bot.py`, `config.py`
- Test: `tests/test_navigate.py`

**Interfaces:**
- Consumes: `screens.ScreenState`, `vision.locate_template`
- Produces: `navigate.Navigator(templates, bus, cooldown=3.0)` with `.maybe_navigate(screen_image, state, device, now=None) -> str | None`
- Config added: `NAVIGATION_COOLDOWN_SECONDS: float = 3.0`, `NAV_BUTTONS: dict[str, str]`

- [ ] **Step 1: Cut the button templates**

The buttons must be located by template match, never by fixed coordinates — the death modal shifts ~46px vertically depending on whether the "New Highest Wave!" line is present.

```bash
uv run python - <<'PY'
import cv2
over = cv2.imread("tests/fixtures/game_over.png")
menu = cv2.imread("tests/fixtures/main_menu.png")
import os; os.makedirs("templates/buttons", exist_ok=True)
cv2.imwrite("templates/buttons/retry.png",  over[1598:1718,  82:518])
cv2.imwrite("templates/buttons/battle.png", menu[1896:2030, 286:792])
for p in ("templates/buttons/retry.png", "templates/buttons/battle.png"):
    i = cv2.imread(p); print(p, i.shape[1], "x", i.shape[0])
PY
```
Expected: `retry.png 436 x 120` and `battle.png 506 x 134`

- [ ] **Step 2: Write the failing test**

Create `tests/test_navigate.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
import vision
from navigate import Navigator
from screens import ScreenState

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


@pytest.fixture
def nav() -> Navigator:
    return Navigator(vision.TemplateCache(TEMPLATES), events.EventBus())


def test_taps_retry_on_game_over(nav: Navigator) -> None:
    dev = MagicMock()
    target = nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev, now=0.0)

    assert target == "RETRY"
    dev.click.assert_called_once()


def test_taps_battle_on_the_main_menu(nav: Navigator) -> None:
    dev = MagicMock()
    target = nav.maybe_navigate(frame("main_menu"), ScreenState.MAIN_MENU, dev, now=0.0)

    assert target == "BATTLE"
    dev.click.assert_called_once()


def test_does_nothing_in_run(nav: Navigator) -> None:
    dev = MagicMock()
    assert nav.maybe_navigate(frame("in_run_lit"), ScreenState.IN_RUN, dev, now=0.0) is None
    dev.click.assert_not_called()


def test_cooldown_prevents_a_double_tap(nav: Navigator) -> None:
    dev = MagicMock()
    assert nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev, now=0.0)
    assert nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev, now=1.0) is None
    assert nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev, now=4.0)


def test_retry_is_located_not_hardcoded(nav: Navigator) -> None:
    """The modal moves ~46px between the two game-over fixtures. Locating the
    button by match must find it in both, at different y positions."""
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get("buttons/retry.png")

    a = vision.locate_template(frame("game_over"), template, 0.8)
    b = vision.locate_template(frame("game_over_fade"), template, 0.8)

    assert a is not None and b is not None
    assert a.center[1] != b.center[1]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_navigate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'navigate'`

- [ ] **Step 4: Write the implementation**

Add to `config.py`:

```python
# --- Auto-navigation ------------------------------------------------------
# Buttons are located by template match, never by fixed coordinates: the
# death modal shifts ~46px vertically depending on whether the
# "New Highest Wave!" line is present.
NAVIGATION_COOLDOWN_SECONDS: float = 3.0
NAV_BUTTONS: dict[str, tuple[str, str]] = {
    "GAME_OVER": ("RETRY", "buttons/retry.png"),
    "MAIN_MENU": ("BATTLE", "buttons/battle.png"),
}
```

Create `navigate.py`:

```python
"""Move between runs unattended.

Nothing here spends permanent resources: in-run upgrades are bought with
per-run cash that resets, and coins are only ever earned.
"""

from __future__ import annotations

import logging

import config
import events
import vision
from device import Image, tap
from screens import ScreenState

logger = logging.getLogger("tower_bot.navigate")


class Navigator:
    def __init__(
        self,
        templates: vision.TemplateCache,
        bus: events.EventBus,
        cooldown: float = config.NAVIGATION_COOLDOWN_SECONDS,
        threshold: float = 0.8,
    ) -> None:
        self._templates = templates
        self._bus = bus
        self._cooldown = cooldown
        self._threshold = threshold
        self._last = float("-inf")

    def maybe_navigate(
        self, screen: Image, state: ScreenState, device, now: float | None = None
    ) -> str | None:
        entry = config.NAV_BUTTONS.get(state.value)
        if entry is None:
            return None

        moment = 0.0 if now is None else now
        if moment - self._last < self._cooldown:
            return None

        target, template_path = entry
        match = vision.locate_template(
            screen, self._templates.get(template_path), self._threshold
        )
        if match is None:
            return None

        x, y = match.center
        tap(device, x, y)
        self._last = moment
        self._bus.publish(events.Navigated(target=target))
        return target
```

Add both to `TowerBot.__init__` (new parameter `auto_navigate: bool = False`):

```python
        self.auto_navigate = auto_navigate
        self.navigator = Navigator(templates, bus)
```

Then wire it into `run_once`, after the action loop:

```python
        if self.auto_navigate:
            self.navigator.maybe_navigate(
                self.screen, self.tracker.state, self.device, now=time.monotonic()
            )
```

Note: `config.Action.brightness_ratio` became unused in Task 10 when the check
moved behind `AffordabilityCheck`. Leave the field in place — Plan 2 reuses the
per-action override for `DigitAffordability`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_navigate.py -v`
Expected: 5 passed

- [ ] **Step 6: Prepare the commit**

```bash
git add navigate.py templates/buttons/ config.py tower_bot.py tests/test_navigate.py
git commit -m "feat: auto-navigate between runs via RETRY and BATTLE"
```

---

### Task 13: Run correlation

Spec section 5 requires the FSM to produce run boundaries. Without this the
dashboard in Plan 3 has no runs to show, and `--max-runs` in Task 15 has
nothing to count.

**Files:**
- Create: `runs.py`
- Modify: `tower_bot.py`
- Test: `tests/test_runs.py`

**Interfaces:**
- Consumes: `screens.ScreenState`, `events.RunStarted`, `events.RunEnded`
- Produces: `runs.RunTracker()` with `.current_id: int | None`, `.completed: int`,
  and `.transition(prev: ScreenState, curr: ScreenState, now: float) -> events.Event | None`
  returning an unstamped `RunStarted` / `RunEnded` for the bot to publish, or `None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_runs.py`:

```python
from __future__ import annotations

import events
from runs import RunTracker
from screens import ScreenState


def test_entering_a_run_from_the_menu_starts_one() -> None:
    tracker = RunTracker()

    event = tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)

    assert isinstance(event, events.RunStarted)
    assert tracker.current_id == 1


def test_retry_starts_a_new_run_with_a_new_id() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)
    tracker.transition(ScreenState.IN_RUN, ScreenState.GAME_OVER, now=200.0)

    event = tracker.transition(ScreenState.GAME_OVER, ScreenState.IN_RUN, now=210.0)

    assert isinstance(event, events.RunStarted)
    assert event.run_id == 2


def test_death_ends_the_run_and_records_its_duration() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)

    event = tracker.transition(ScreenState.IN_RUN, ScreenState.GAME_OVER, now=250.0)

    assert isinstance(event, events.RunEnded)
    assert event.duration == 150.0
    assert event.abandoned is False
    assert tracker.completed == 1


def test_leaving_to_the_menu_abandons_the_run() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)

    event = tracker.transition(ScreenState.IN_RUN, ScreenState.MAIN_MENU, now=120.0)

    assert isinstance(event, events.RunEnded)
    assert event.abandoned is True


def test_unknown_does_not_end_an_open_run() -> None:
    """A stray popup mid-battle must not fabricate a run boundary."""
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)

    assert tracker.transition(ScreenState.IN_RUN, ScreenState.UNKNOWN, now=110.0) is None
    assert tracker.current_id == 1

    assert tracker.transition(ScreenState.UNKNOWN, ScreenState.IN_RUN, now=120.0) is None
    assert tracker.current_id == 1


def test_death_without_a_started_run_is_ignored() -> None:
    """Starting the bot on the death screen must not invent a run."""
    tracker = RunTracker()

    assert tracker.transition(ScreenState.UNKNOWN, ScreenState.GAME_OVER, now=100.0) is None
    assert tracker.completed == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'runs'`

- [ ] **Step 3: Write the implementation**

Create `runs.py`:

```python
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
```

- [ ] **Step 4: Publish run events from the bot**

In `TowerBot.__init__` add `self.runs = RunTracker()`. In `run_once`, inside the
confirmed-transition block, right after publishing `ScreenChanged`:

```python
            run_event = self.runs.transition(
                previous, self.tracker.state, time.monotonic()
            )
            if run_event is not None:
                self.bus.publish(run_event)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_runs.py -v`
Expected: 6 passed

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass

- [ ] **Step 7: Prepare the commit**

```bash
git add runs.py tower_bot.py tests/test_runs.py
git commit -m "feat: derive run boundaries from screen transitions"
```

---

### Task 14: Live TUI sink

**Files:**
- Create: `sinks/tui.py`
- Test: `tests/test_tui_sink.py`

**Interfaces:**
- Consumes: `events.*`, `sinks.base.QueueSink`
- Produces: `sinks.tui.TuiState` (accumulates events into renderable state) and `sinks.tui.TuiSink`

The renderable state is separated from `rich` rendering so it can be tested without a terminal.

- [ ] **Step 1: Add the dependency**

```bash
uv add rich
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_tui_sink.py`:

```python
from __future__ import annotations

import events
from sinks.tui import TuiState


def stamped(bus: events.EventBus, event: events.Event) -> events.Event:
    return bus.publish(event)


def test_tracks_the_current_screen() -> None:
    bus, state = events.EventBus(), TuiState()

    state.apply(stamped(bus, events.ScreenChanged(
        prev="UNKNOWN", curr="IN_RUN", confidence=1.0, scores={})))

    assert state.screen == "IN_RUN"


def test_counts_taps_per_action() -> None:
    bus, state = events.EventBus(), TuiState()

    for _ in range(3):
        state.apply(stamped(bus, events.Tapped(action="Damage", x=1, y=2, score=0.9)))
    state.apply(stamped(bus, events.Tapped(action="Attack Speed", x=1, y=2, score=0.9)))

    assert state.taps == {"Damage": 3, "Attack Speed": 1}


def test_keeps_a_bounded_event_tail() -> None:
    bus, state = events.EventBus(), TuiState(tail=5)

    for _ in range(20):
        state.apply(stamped(bus, events.Navigated(target="RETRY")))

    assert len(state.tail) == 5


def test_counts_scans_and_records_the_last_error() -> None:
    bus, state = events.EventBus(), TuiState()

    state.apply(stamped(bus, events.ScanCompleted(screen="IN_RUN", duration_ms=12.0)))
    state.apply(stamped(bus, events.ScanCompleted(screen="IN_RUN", duration_ms=14.0)))
    state.apply(stamped(bus, events.BotError(message="screencap failed")))

    assert state.scans == 2
    assert state.last_error == "screencap failed"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_sink.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sinks.tui'`

- [ ] **Step 4: Write the implementation**

Create `sinks/tui.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_tui_sink.py -v`
Expected: 4 passed

- [ ] **Step 6: Prepare the commit**

```bash
git add sinks/tui.py pyproject.toml uv.lock tests/test_tui_sink.py
git commit -m "feat: add live TUI sink"
```

---

### Task 15: CLI wiring and the resolution guard

**Files:**
- Modify: `tower_bot.py`, `config.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above
- Produces: flags `--tui`, `--auto-navigate`, `--max-runs N`; `config.EXPECTED_RESOLUTION`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
from __future__ import annotations

from tower_bot import parse_args


def test_auto_navigate_defaults_off() -> None:
    """A bot that launches battles the moment it starts is surprising."""
    assert parse_args([]).auto_navigate is False


def test_auto_navigate_can_be_enabled() -> None:
    assert parse_args(["--auto-navigate"]).auto_navigate is True


def test_tui_defaults_off() -> None:
    assert parse_args([]).tui is False


def test_max_runs_defaults_to_unlimited() -> None:
    assert parse_args([]).max_runs is None


def test_max_runs_parses_an_integer() -> None:
    assert parse_args(["--max-runs", "5"]).max_runs == 5


def test_run_forever_stops_at_max_runs(monkeypatch) -> None:
    """--max-runs is meaningless unless the loop actually honours it."""
    from pathlib import Path
    from unittest.mock import MagicMock

    import cv2
    import events
    import vision
    from tower_bot import TowerBot

    fixtures = Path(__file__).parent / "fixtures"
    bot = TowerBot(
        device=MagicMock(),
        templates=vision.TemplateCache(Path(__file__).parent.parent / "templates"),
        bus=events.EventBus(),
    )
    bot._screen = cv2.imread(str(fixtures / "main_menu.png"), cv2.IMREAD_COLOR)
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    bot.runs.completed = 3

    bot.run_forever(interval=0.0, max_runs=3)  # must return immediately
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `AttributeError: 'Namespace' object has no attribute 'auto_navigate'`

- [ ] **Step 3: Add the flags**

In `parse_args`:

```python
    parser.add_argument(
        "--tui", action="store_true", help="live terminal panel instead of log lines"
    )
    parser.add_argument(
        "--auto-navigate", action="store_true",
        help="tap RETRY / BATTLE to loop runs unattended (default: off)",
    )
    parser.add_argument(
        "--max-runs", type=int, default=None,
        help="stop after this many runs (default: unlimited)",
    )
```

Enforce it in `run_forever`, using the counter from Task 13:

```python
            if max_runs is not None and self.runs.completed >= max_runs:
                logger.info("Reached --max-runs=%d, stopping.", max_runs)
                break
```

with `run_forever(self, interval: float = ..., max_runs: int | None = None)`,
called as `bot.run_forever(interval=args.interval, max_runs=args.max_runs)`.

In `main`, select the sink and add the resolution guard:

```python
    sink = TuiSink() if args.tui else LogSink()
    sink.start()
    bus.subscribe(sink)

    frame = capture_screen(device)
    height, width = frame.shape[:2]
    if (width, height) != config.EXPECTED_RESOLUTION:
        logger.warning(
            "Emulator is %dx%d but templates were captured at %dx%d. "
            "Template matching is not scale-invariant - re-capture them.",
            width, height, *config.EXPECTED_RESOLUTION,
        )
```

Add to `config.py`:

```python
# Templates are not scale-invariant. A different emulator resolution
# invalidates every one of them.
EXPECTED_RESOLUTION: tuple[int, int] = (1080, 2400)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass

- [ ] **Step 5: End-to-end smoke test**

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"
uv run tower_bot.py --once                 # one scan, structured line
uv run tower_bot.py --tui --auto-navigate  # watch it loop; Ctrl+C to stop
```
Expected: the panel reports the current screen, taps only on `IN_RUN`, and taps RETRY / BATTLE to move between runs.

- [ ] **Step 6: Capture the greyed-out state (spec open item)**

While a run is live and the wallet is nearly empty, capture a frame where at
least one upgrade is unaffordable and save it as
`tests/fixtures/in_run_broke.png`. Measure its brightness ratio:

```bash
uv run python - <<'PY'
import cv2, vision
cache = vision.TemplateCache("templates")
t = cache.get("upgrade_damage.png")
f = cv2.imread("tests/fixtures/in_run_broke.png")
m = vision.locate_template(f, t, 0.9)
print("brightness ratio:", vision.brightness_ratio(f, m, t))
PY
```

Record the number in the spec's open items. If it is not comfortably below
0.75, `BrightnessAffordability` does **not** detect unaffordability and Plan 2
(digit reading) becomes required rather than merely preferable.

- [ ] **Step 7: Prepare the commit**

```bash
git add tower_bot.py config.py tests/test_cli.py
git commit -m "feat: add --tui, --auto-navigate, --max-runs and a resolution guard"
```

---

## Done when

- `uv run pytest -v` passes with no emulator attached.
- `uv run tower_bot.py --once` names the screen the game is actually on.
- The bot taps nothing on the main menu or the death modal.
- `--tui --auto-navigate` loops runs unattended and reports what it is doing.
- The greyed-out brightness ratio from Task 15 Step 6 is recorded in the spec.

## Follow-on plans

- **Plan 2** — phase 3: digit atlas, exact affordability, wave/coins from the death modal.
- **Plan 3** — phases 4-5: SQLite persistence, FastAPI dashboard, README rewrite.
