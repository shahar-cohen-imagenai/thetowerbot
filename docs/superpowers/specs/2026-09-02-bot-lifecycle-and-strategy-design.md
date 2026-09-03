# The Tower bot — browser-driven lifecycle and an editable strategy

Status: designed, not yet implemented
Date: 2026-09-02

Builds on `2026-09-01-tower-bot-dashboard-ui-design.md`, whose control plane
(section 4) this design extends and partly reshapes.

## 1. Context

The dashboard can watch a bot and tune it. It cannot start one, and what it
tunes is a thin slice of what the bot actually decides.

Two gaps, and they are not the same gap.

**The bot's lifecycle belongs to the terminal.** `serve_web()` runs the
server on the main thread and the scan loop on a worker beside it, and a
single `stop` Event brings both down together. That coupling is deliberate
and well documented — it is what keeps Ctrl+C, `--max-runs` and the browser's
Stop button on one shutdown path instead of three. But it also means the
dashboard can only ever exist while the bot does. There is no state in which
a browser could press Start, because by the time anything is serving, the
bot is already running.

**"Strategy" means one bit of what it should mean.** `Controls.strategy` is
`digits | brightness` — how prices are read, not what to buy. The real
strategy is `config.ACTIONS`: a frozen tuple in source, whose order is the
priority, whose thresholds are per-row, and which cannot be changed without
editing Python and restarting. `enabled_actions` lets the browser switch a
row off; it cannot reorder them, retune one, or save a set of choices as
something you can name and come back to.

So the control page today can pause a bot it did not start, and toggle rows
of a strategy it cannot see.

## 2. Goals

- Start, stop and restart the bot from the browser, with the dashboard
  outliving any individual bot.
- One page that shows the whole decision policy — purchases, loop timing and
  run policy — and lets every part of it be changed.
- Named strategies on disk, switchable live, diffable in git, editable by
  hand when nothing is running.
- Exactly one source of truth for policy. What the page shows is what the
  loop reads and what the file holds.
- Keep the invariants the current design earned: one settings snapshot per
  scan, `publish()` never blocks, all-or-nothing validation, and a single
  process-shutdown path.

### Non-goals

- **Abandon rules.** Bailing out of a live run on a time or wave-stall
  condition needs new detection and a new tap path. `RunEnded.abandoned`
  today is only set by `db.close_abandoned_runs()` for a killed process;
  there is no policy that decides to leave. Out of scope here.
- **Adding or removing action rows from the UI.** The page reorders,
  toggles and tunes the rows a strategy already has. Adding one means
  choosing a template file, which is a file-picker problem, not a strategy
  problem. Hand-editing the JSON does it, and validation covers that path.
- **Authentication.** Unchanged, and now mattering more — see section 13.
- **Multiple concurrent bots.** One runner, one bot, one device.

## 3. Architecture

Three parts, each replacing or wrapping something that exists.

```
strategy.py      Strategy + ActionRule value objects, StrategyStore over
                 strategies/*.json.  Knows nothing about threads or HTTP.

control.py       Controls, reshaped: session state (paused) plus one
                 Strategy, swapped whole under the lock it already has.

runner.py        BotRunner: owns the device, the TowerBot and the worker
                 thread.  start() / stop() / status().  The thing the
                 browser's Start button reaches.
```

`web/app.py` gains routes over all three. `tower_bot.py`'s `serve_web()`
stops owning a worker thread and owns a runner instead.

The dependency direction is the one the codebase already insists on: the
scan loop imports `control` and `strategy`, and neither imports the web
layer. `runner.py` sits above the loop and below the web layer, importing
both `tower_bot` and `control` — which is why it is a new module rather than
more of `control.py`.

## 4. The strategy model

```python
@dataclass(frozen=True)
class ActionRule:
    name: str
    template: str
    enabled: bool = True
    threshold: float = 0.9
    brightness_ratio: float = 0.75

@dataclass(frozen=True)
class Strategy:
    name: str
    actions: tuple[ActionRule, ...]     # order IS priority
    affordability: str = "digits"       # digits | brightness
    interval: float = 2.0
    click_cooldown: float = 1.0
    auto_navigate: bool = False
    max_runs: int | None = None
    navigation_cooldown: float = 3.0    # applies on next Start
    screen_confirmations: int = 2       # applies on next Start
```

Frozen all the way down, rows included. This is not decoration: today
`Controls.__post_init__` copies the `enabled_actions` set precisely so a
caller cannot mutate live state behind the lock, and `snapshot()` hands out
a detached copy for the same reason. A frozen dataclass holding a tuple of
frozen dataclasses cannot be mutated by anyone, so the copying stops being
necessary rather than being reimplemented one level deeper.

`order IS priority` deletes a field. A separate `priority: int` per row
would be a second way to express what the tuple's order already expresses,
and the two would drift the first time a row was inserted.

### Validation

`Strategy.validated()` raises the existing `ControlError(field, message)`.
Reusing that type rather than inventing one keeps `/api/control`'s 422
handling exactly as it is, and keeps one vocabulary for "the browser sent
something it may not have".

That reuse inverts where the type is defined. `control.py` imports
`Strategy`, so `strategy.py` cannot import `ControlError` back from
`control.py` without a cycle. `ControlError` therefore moves *into*
`strategy.py`, and `control.py` re-exports it — `from strategy import
ControlError` — so `web/app.py`'s existing `from control import
ControlError` keeps working untouched.

| Field | Rule | Why |
|---|---|---|
| `interval` | `MIN_INTERVAL..MAX_INTERVAL` | unchanged: a floor against busy-looping ADB, a ceiling against looking hung |
| `threshold` | `0 < t <= 1` | `cv2.matchTemplate` normalised score |
| `brightness_ratio` | `0 <= r <= 1` | 0 disables the check, as `config` documents |
| `click_cooldown` | `0..60` | 0 is legal; a minute between taps on one button is not a strategy |
| `navigation_cooldown` | `0..60` | same |
| `screen_confirmations` | `1..10` | 0 would defeat the debounce entirely |
| `max_runs` | `null` or `>= 1` | null is unlimited |
| `affordability` | in `("digits", "brightness")` | matches `STRATEGIES` today |
| `actions` | non-empty, names unique | a duplicate name makes `Tapped.action` ambiguous in the log |
| `actions[].template` | exists in `TEMPLATE_DIR` | see below |

The template check is the one worth arguing for. A mistyped filename does
not fail loudly — `TemplateCache.get()` raises when the loop first reaches
that row, deep inside a scan, and the bot reports an error per pass forever.
Rejecting it at save turns a recurring runtime failure into one 422 with a
field name attached.

### The store

`StrategyStore` over a `strategies/` directory beside `config.py`:

- `names()` — sorted stems of `*.json`.
- `load(name)` / `save(strategy)` / `delete(name)`.
- `active_name()` / `set_active(name)`.
- `ensure_seeded()` — writes `default.json` from `config.ACTIONS` and the
  `config` constants if the directory is empty.

`save()` writes a temp file in the same directory and `os.replace()`s it
into place. A crash mid-write must not leave a half-written profile that
fails to parse on next launch — and `os.replace` is atomic within a
filesystem, which a same-directory temp file guarantees.

`delete()` refuses the active profile and refuses the last remaining one.
Both leave the bot with no policy to load, which is a state with no good
recovery short of hand-editing.

The active profile's name lives in `strategies/.active`, a one-line file.
It is a dotfile so it cannot collide with a profile named `active`.

Profile names are validated against `[A-Za-z0-9_-]{1,64}` **before** they
are joined to a path. A name arrives off a URL, and `/api/unknown/{name}`
already learned this lesson the hard way: resolve-and-check is the fallback,
refusing the name outright is the fix.

`ensure_seeded()` keeps `config.ACTIONS` meaningful. It stays the origin of
the defaults — the thing a fresh clone starts from — without staying the
source of truth, which is what section 5 moves.

## 5. `Controls`, reshaped

```python
@dataclass
class Controls:
    strategy: Strategy          # required: there is no useful default
    paused: bool = False
```

`strategy` has no default. A `Controls()` that invented an empty policy
would be a second seeding path competing with `StrategyStore.ensure_seeded()`,
and the two would disagree the first time `config.ACTIONS` changed. Callers
load a profile and pass it.

Everything else it holds today — `interval`, `auto_navigate`,
`enabled_actions`, and the old `strategy` string — is policy, and policy now
lives in the `Strategy`. What is left is genuinely session-scoped: `paused`
is a fact about this bot right now, not a thing you would save under a name
and load next week.

The lock, `snapshot()`, `apply()` and the `changed`-dict-to-`ControlChanged`
path are kept as they are. They are the well-tested part, and none of the
reasons for them changed.

`apply()` keeps its all-or-nothing contract, and gets it more cheaply than
before: a patch is merged into the current `Strategy` with
`dataclasses.replace`, the result is validated whole, and only then is the
new object swapped in under the lock. A patch whose third field is invalid
cannot leave the first two applied, because the first two were never applied
to anything but a candidate object that is now discarded.

`snapshot()` changes its return type from a dict to a frozen
`Live(paused: bool, strategy: Strategy)`. The dict was JSON-shaped because
the web layer was its only other consumer; the loop paid for that with
`settings["enabled_actions"]`, and would now pay far more with
`settings["strategy"]["actions"][0]["threshold"]`. A separate `payload()`
serves the web layer its JSON, and the loop gets attributes.

### The wire rename

`strategy` on the control payload currently means `digits | brightness`. It
now means the whole policy object, and the affordability method becomes
`affordability`. `strategies_available` becomes `affordability_available`.

This breaks the existing control page and its tests. Both are being
rewritten by section 9 regardless, and carrying a field whose name means one
thing to the server and another to a reader is worse than one rename.

## 6. How the loop reads it

Three edits to `run_once()`, all small, because the existing shape already
fits.

```python
settings = self.controls.snapshot()          # still exactly one, per pass
...
for rule in settings.strategy.actions:
    if not rule.enabled:
        continue
    if self.find_and_click_image(rule.as_action(), boxes):
        clicked = True
```

- The action loop iterates the strategy's ordered rows instead of
  `config.ACTIONS` filtered against an enabled-set. Priority and per-row
  tuning need no further work: `find_and_click_image()` already takes a
  `config.Action` and reads `.threshold` from it, and
  `AffordabilityCheck.affordable()` already reads `.brightness_ratio` from
  it. `ActionRule.as_action()` is the whole adapter.
- `self.click_cooldown` becomes `settings.strategy.click_cooldown`.
- `max_runs` comes from `settings.strategy` when the existing parameter is
  `None`, and from the parameter otherwise. The parameter stays for the
  non-web paths, which have no strategy loaded to read; the precedence rule
  means the two can never both be meaningfully set in the web path, because
  section 10 persists the CLI flag into the strategy rather than carrying it
  alongside.

One snapshot per pass, unchanged. The reason in the current docstring —
that re-reading mid-scan would let the wallet be read with one strategy and
the price gate applied with another — is now more true, not less, because
there is more that could tear.

### What cannot be live, and why

`screen_confirmations` is `ScreenTracker`'s debounce depth and
`navigation_cooldown` is `Navigator`'s rate limit. Both objects are
constructed once and carry state across scans — a partially-confirmed
reading, a last-navigation timestamp. Changing the depth under a tracker
that is two observations into confirming a transition has no correct answer.

So both are read by `BotRunner` when it constructs the bot, and the page
labels them *applies on next Start*. This is an honest boundary rather than
a limitation: what the loop reads per-scan is live, what a stateful
collaborator is built with is not.

## 7. `BotRunner` and the lifecycle split

```python
class BotRunner:
    def start(self) -> dict   # connect, build, spawn
    def stop(self) -> dict    # bot.stop(), join with a bounded timeout
    def status(self) -> dict  # {running, since, error}
```

A lock around all three, so two concurrent `POST /api/bot/start` requests
cannot both spawn a thread. A second start while running is a 409, not a
silent no-op — the browser asked for something that did not happen.

`Controls` and the `StrategyStore` are owned by the *process*, not by the
runner, and outlive any bot. So the whole strategy surface stays editable
while nothing is running — which is the normal case for the Start button:
open the dashboard, set the policy, then start a bot that is constructed
from it. A `PATCH` with no bot running validates, persists and publishes
exactly as it does with one; there is simply no loop reading the result yet.

The affordability `checks` dict is process-level for a different reason:
`build_checks_and_controls()` builds both strategies once at startup because
`DigitAffordability` loads a glyph atlas, and rebuilding that per Start
would make the button slow for no gain. The runner receives the dict and
passes it to each bot it constructs.

Three things this must get right that the current code never had to:

**Device failure becomes recoverable.** Today `connect_device()` runs in
`main()` and an `EmulatorError` returns exit 1 before anything serves. In
idle mode the connection happens inside `start()`, where a dead emulator
must not take the dashboard down with it. It becomes a 503 carrying the
message, plus a `BotError` on the bus so it lands in the live feed and on
the errors page like every other failure.

**Run ids carry forward.** `prepare_store()` reads the last run id once, at
launch, and `TowerBot` is constructed with `first_run_id`. Across several
starts in one process the runner must carry the next id from the previous
bot's `RunTracker`, or the second session's run 2 collides with the first
session's run 2 in SQLite.

**`BotState` resets on start.** Uptime, scan count and tap tallies
accumulate for the life of the sink, which is now longer than the life of a
bot. Without a reset the status bar shows a stopped bot's scan count beside
a fresh bot's uptime.

The `EventBus` is deliberately *not* reset. It is process-lived, so `seq`
keeps climbing across bot lifetimes and a reconnecting browser's
`Last-Event-ID` stays meaningful — the SSE ring spans restarts, which is
exactly what you want when the thing you are watching is a restart.

### Two flags where there was one

| Flag | Means | Set by |
|---|---|---|
| `shutdown` | the *process* is going down; SSE and MJPEG generators end themselves so held-open responses complete | Ctrl+C via `_Server.handle_exit`, `POST /api/shutdown` |
| runner-internal | this *bot* stops; the server keeps serving | `POST /api/bot/stop`, `max_runs` reached |

The current `stop` Event is both at once. Splitting them is what makes Start
possible at all — otherwise pressing Stop kills the dashboard you pressed it
from.

`shutdown` inherits every existing responsibility of `stop`, unchanged: the
stop-watch thread, `_Server.handle_exit()` setting it before graceful
shutdown begins, and both stream generators checking it. The reasoning in
those docstrings survives verbatim; only the name narrows.

`serve_web()` stops owning a worker thread and owns a `BotRunner` instead.
`--web` calls `runner.start()` before `server.run()`, so it still means
serve-and-scan and nothing anyone runs today changes behaviour. `--web
--idle` skips that call and serves an empty dashboard waiting for Start.

## 8. Endpoints

```
GET    /api/strategies                    {active, names}
GET    /api/strategies/{name}             one Strategy as JSON
PUT    /api/strategies/{name}             create or overwrite; 422 with a field
POST   /api/strategies/{name}/activate    load live + persist .active
DELETE /api/strategies/{name}             409 if active or last

GET    /api/control                       {paused, strategy, affordability_available}
PATCH  /api/control                       live edit of the active strategy

POST   /api/bot/start                     200 | 409 running | 503 device
POST   /api/bot/stop                      {running: false}
POST   /api/shutdown                      {stopping: true}

GET    /api/status                        + bot: {running, since, error}
```

`POST /api/control/stop` is replaced by `/api/bot/stop` and `/api/shutdown`.
One route cannot mean both once they are different things.

**A `PATCH` also writes the active profile's file.** This is what "one
source of truth" costs, and it is the whole point of section 5's answer: an
edit that applied live but did not persist would leave the file and the loop
disagreeing until the next explicit save, which is the drift this design
exists to remove. Writes are rare — the page sends on blur and on toggle,
never per keystroke — and atomic, so no debounce is needed.

Activation applies the loaded strategy to `Controls` and publishes
`ControlChanged`, so every open tab converges over the SSE feed it already
listens to. No new push mechanism.

## 9. The pages

The sidebar gains a sixth entry. **Control** and **Strategy** split along
the line section 5 draws:

**Control** keeps session concerns only — Start, Stop bot, Shut down, pause,
and a read-only summary of the active strategy that links to the page that
edits it.

**Strategy** is new: a profile selector with activate, duplicate and delete,
then three sections mirroring the model — Purchases (rows with enabled,
up/down reorder, threshold, brightness ratio), Timing, and Run policy. The
two start-only fields carry an *applies on next Start* badge.

Reorder is up/down buttons, not drag-and-drop. Four rows do not justify a
dependency, and buttons are reachable by keyboard for free.

Editing the active profile applies live; editing any other writes its file
and nothing else. That rule needs no badge or explanation on the page — it
follows from "the active strategy is what the loop reads".

`StatBar` gains a running/stopped pill, fed by `status.bot`.

## 10. CLI

`--strategy <name>` selects the profile to load, defaulting to whatever
`.active` holds.

`--interval`, `--auto-navigate`, `--max-runs` and `--affordability` now
duplicate strategy fields. An explicitly-passed flag overrides the loaded
profile **and persists into it**, and the bot logs a line saying so.

The alternative — override without persisting — was rejected. It
reintroduces exactly the file-versus-live drift section 8 pays to avoid, and
it does so in the one situation where it is hardest to notice: the page
would show a value the file does not hold, with nothing on screen saying
why. Persisting is occasionally surprising; drift is quietly wrong. The log
line is what makes the surprise discoverable.

Distinguishing "passed" from "defaulted" needs `default=None` sentinels in
`parse_args`, with the real defaults coming from the loaded strategy.

## 11. Data flow

```
strategies/*.json ──load──► Strategy ──► Controls.strategy
                                              │
                          PATCH /api/control ─┤ (validate, replace, swap)
                                              │
                                              ├──► save() ──► strategies/*.json
                                              │
                                              └──► ControlChanged ──► bus
                                                                       │
                                       run_once(): snapshot() once per pass
                                                                       │
                                                          SSE ──► every open tab

Start ──► BotRunner.start() ──► connect_device ──► TowerBot(strategy) ──► thread
   │                                   │
   │                                   └── EmulatorError ──► BotError + 503
   └── carries next_run_id and resets BotState
```

## 12. Testing

New:

- `tests/test_strategy.py` — every validation bound; JSON round-trip;
  `save()` atomicity (a temp file left behind by a simulated crash does not
  become a profile); name sanitisation, including `../` and an absolute
  path; `ensure_seeded()` producing a profile equivalent to
  `config.ACTIONS`; `delete()` refusing the active and the last.
- `tests/test_runner.py` — start/stop/restart against a fake device
  factory; run ids continuing across a restart; a failing device factory
  surfacing as an error and a `BotError` rather than raising out of
  `start()`; a second start while running rejected; `BotState` reset on
  start; the bus *not* reset.

Extended:

- `tests/test_control.py` — patch merges into the strategy; all-or-nothing
  across a multi-field patch where the last field is invalid; the `changed`
  dict's shape.
- The web tests — every new route, its 409/503/422 paths, and traversal on
  `{name}`.
- `run_once` tests — row order honoured; a disabled row skipped; a per-row
  threshold actually reaching `locate_template`.

Frontend: vitest for both pages, following the existing per-page test
convention in `web/ui/app/*/page.test.tsx`.

The Python suite asserts against the committed `web/static/` build, so the
UI must be rebuilt before it will pass — the existing README note applies
unchanged.

## 13. Risks

**The unauthenticated surface grows again.** The dashboard could already
pause a bot, change what it buys and stop the process. It can now start one,
rewrite policy files on disk, and create and delete files under
`strategies/`. Loopback was already doing real work; it is now doing more.
The README's warning needs to say so, and `warn_if_web_host_exposed()`'s
message with it.

**`strategies/` is a new write path in a process that had one.** The web
layer's read-only-against-SQLite invariant is untouched — `db.reader()` is
still `mode=ro`, and no strategy touches the database. But "the web layer
never writes" stops being true in general, and the docstring in `web/app.py`
that says so needs to become the narrower, still-true claim.

**Restart is a new state to get wrong.** Run-id continuity, `BotState`
reset, and the bus deliberately *not* resetting are three separate decisions
that only matter on the second start, which is exactly the path a manual
test skips. `tests/test_runner.py` covers all three because nothing else
will.

## 14. Delivery

Four plans, each independently landable and each leaving the bot working:

1. **`strategy.py` and the store.** Pure model plus filesystem, no callers.
   Seeding, validation, atomic save, name sanitisation.
2. **`Controls` reshaped and the loop reading it.** The wire rename, the
   `Live` snapshot, `run_once` iterating strategy rows. The existing control
   page is updated only enough to keep working.
3. **`BotRunner` and the flag split.** `--idle`, the lifecycle routes, the
   status payload's `bot` block.
4. **The pages.** The Strategy page, the reshaped Control page, the sidebar
   entry, the StatBar pill, and the README.
