# The Tower bot — observability, screen awareness, and admin dashboard

Status: approved design, not yet implemented
Date: 2026-08-30

## 1. Context

The bot captures an emulator frame over ADB, matches templates against it, and
taps what it finds. It works mechanically, but it is blind: it has no idea which
screen the game is on, it reports nothing while it runs, and it keeps no history.

Investigation on 2026-08-30 found a concrete bug behind that blindness. The
templates in `templates/` had been cropped from `screen.png`, which is the
**GAME STATS death modal** — every upgrade label in it sits in the dimmed
backdrop behind the modal, not in a live run. Two consequences followed:

- The affordability gate was near-inert. `brightness_ratio` compares a matched
  region against the template's own grey level, so cropping the template from an
  already-dimmed frame set the baseline to the dimmed value. A dimmed button
  scored ~0.74 against a 0.75 threshold: right on the line.
- Because `cv2.TM_CCOEFF_NORMED` normalises out brightness, the templates scored
  **1.000 on the death modal** — they were cut from it. The bot would tap
  straight through the modal, which is exactly what the gate existed to prevent.

Templates were re-cut from a live run frame. Measured after re-cutting:

```
ANCHOR SCORES              MENU   IN_RUN  GAME_OVER
main_menu                 1.000    0.312      0.462
in_run                    0.308    1.000      0.317
game_over                 0.419    0.319      0.998

UPGRADE TEMPLATES         score / brightness-ratio
                           MENU          IN_RUN        GAME_OVER
upgrade_damage         0.517 / 1.41  1.000 / 1.00   1.000 / 0.28
upgrade_attack_speed   0.354 / 1.05  1.000 / 1.00   1.000 / 0.28
upgrade_crit_chance    0.375 / 1.00  1.000 / 1.00   1.000 / 0.28
upgrade_crit_factor    0.359 / 1.13  1.000 / 1.00   1.000 / 0.28
```

Two facts drive the whole design:

1. **Screen anchors separate cleanly** (1.000 vs <=0.462). Screen recognition is
   reliable at a 0.8 threshold.
2. **Template score alone cannot distinguish IN_RUN from GAME_OVER** — both read
   1.000, because the upgrade panel is still rendered behind the modal. Only
   brightness (1.00 vs 0.28) separates them. Both layers are required.

Background operation was verified empirically: with the emulator app fully
hidden (`visible = false`, not merely unfocused), `screencap` returned a live
frame and `input tap` registered. Host window visibility is irrelevant.

## 2. Goals

- Recognise which screen the game is on, and gate every action on it.
- Report continuously: structured log lines by default, an opt-in live TUI.
- Navigate between runs unattended (RETRY / BATTLE).
- Persist run history and serve a read-only admin dashboard.
- Replace the brightness heuristic with exact affordability where practical.

### Non-goals

- Controlling the bot from the browser. The dashboard is read-only; a command
  channel back into the loop is explicitly out of scope.
- Runtime-editable configuration. `config.py` stays the source of truth.
- Recognising screens beyond the three named below. Everything else is UNKNOWN
  and the bot holds.
- Remote access. The dashboard binds localhost only.

## 3. Architecture

Single process. The scan loop runs in a worker thread and publishes to a
thread-safe `EventBus`; sinks fan out to log, TUI, SQLite, and SSE. When `--web`
is passed, uvicorn runs on the main thread's asyncio loop.

This works because **`cv2.matchTemplate` releases the GIL**, so the CPU-bound
vision work genuinely runs in parallel with the web server rather than starving
it. The scan loop is both blocking (ADB I/O) and CPU-bound (7 full-frame matches
per scan), so it cannot live on the event loop directly.

```
device (ADB) -> scan loop [worker thread] -> EventBus -+-> LogSink    stdout
                                                       +-> TuiSink    rich
                                                       +-> StoreSink  SQLite
                                                       +-> SseSink    browser
```

Rejected alternatives:

- **Two processes with SQLite as the seam.** Better isolation, but loses true
  push, duplicates the event model, and adds WAL concurrency care for isolation
  a localhost hobby bot does not need. `EventBus` is the seam to split on if
  this is ever wanted.
- **Bot on the main thread, uvicorn in a daemon thread.** Makes signal handling
  and clean shutdown awkward for no benefit.

### Module layout

```
config.py       screens, actions, thresholds, regions
events.py       typed events + EventBus
device.py       ADB connect / capture / tap        (extracted)
vision.py       template cache, match, brightness  (extracted)
digits.py       digit atlas + segmenter -> number  (new)
screens.py      anchor matching -> ScreenState FSM (new)
bot.py          scan loop, gating, auto-navigate
sinks/log.py    structured lines
sinks/tui.py    rich live panel
sinks/store.py  SQLite
web/app.py      FastAPI + SSE + static dashboard
tower_bot.py    thin CLI entry point
```

## 4. Event model

Every event carries `ts` (wall clock) and `seq` (monotonic counter). `seq`
orders events across sinks and gives the SSE stream a resume point.

| Event | Payload |
|---|---|
| `ScreenChanged` | prev, curr, confidence, all anchor scores |
| `ScanCompleted` | screen, duration_ms, wallet |
| `Tapped` | action, x, y, score, price, wallet |
| `Skipped` | action, reason, detail |
| `RunStarted` | run_id, started_at |
| `RunEnded` | run_id, wave, coins, duration, abandoned |
| `Navigated` | target (RETRY / BATTLE) |
| `UnknownScreen` | snapshot_path, best_anchor, best_score |
| `BotError` | message, traceback |

`Skipped.reason` is one of `screen_gated`, `dimmed`, `unaffordable`, `cooldown`.

### EventBus invariant

`publish()` fans out synchronously, but **every sink must be non-blocking**.
Each sink owns a bounded queue; `publish` only does `put_nowait`. On a full
queue the event is dropped and a counter incremented, surfaced via
`/api/status`.

This is the single most important invariant in the design: a slow browser, a
locked database, or a stalled TUI must never stall the scan loop.

## 5. Screen FSM

States: `MAIN_MENU`, `IN_RUN`, `GAME_OVER`, `UNKNOWN`.

Each scan matches all three anchors full-frame and takes the argmax. Below
`ANCHOR_THRESHOLD = 0.8`, the result is `UNKNOWN`. Full-frame rather than
region-of-interest: ~100ms for three anchors against a 2s interval is not worth
optimising, and the death modal moves vertically (see section 6), so ROI padding
would need tuning for no gain.

**Debounce: a transition is declared only after 2 consecutive identical
readings.** Not speculative — the same region measured 0.74 mid-fade and 0.28
fully dimmed, proving that capture lands inside the modal's fade animation.
Without debounce those frames produce phantom transitions and corrupt run
boundaries.

### Run correlation

```
MAIN_MENU  -> IN_RUN      RunStarted
GAME_OVER  -> IN_RUN      RunStarted            (RETRY path)
IN_RUN     -> GAME_OVER   RunEnded              (read wave + coins)
IN_RUN     -> MAIN_MENU   RunEnded(abandoned)
any        -> UNKNOWN     hold; no state change
```

`UNKNOWN` deliberately does not end a run. A stray popup mid-battle must not
fabricate a run boundary; the run stays open and resumes.

### Unknown screen handling

Hold and snapshot. No taps fire. The frame is written to `unknown/<ts>.png`,
rate-limited to one per 30s and capped at the 50 most recent. The bot never
self-recovers from UNKNOWN; it idles until a known screen returns. The snapshots
are both the dashboard's diagnostic view and the raw material for adding screens
later.

## 6. Coordinates and digit reading

**All regions inside the death modal are anchor-relative, never absolute.** The
modal shifts ~46px vertically depending on whether the "New Highest Wave!" line
is present. Wave, coins, and the RETRY button are all expressed as offsets from
the matched `game_over` anchor's top-left. Upgrade price boxes are likewise
offsets from their own matched label, whose location the match already gives us.
No screen position is hardcoded.

### Reading a number

Threshold to binary (light glyphs on dark), column-projection to find gaps,
split into glyphs, match each against an atlas, concatenate.

Two complications:

1. **Scale.** `matchTemplate` is not scale-invariant and the UI uses at least
   three text sizes (wallet, price, modal). One atlas *per size class*.
2. **Suffixes.** The game abbreviates large numbers (`$1.23K`, `4.5M`). A
   long-running bot will hit these. The parser must accept
   `[digits][.digits][K|M|B|T]` and the atlas must include `.`, `,` and the
   suffix letters. A digit-only parser works briefly, then silently misreads.

`build_atlas.py` bootstraps the atlas: capture frames over a session, crop the
configured regions, segment glyphs, dump unlabeled crops for one-time manual
labelling by renaming (~10-30 files per size class).

### Affordability strategy

```
AffordabilityCheck (protocol)
  BrightnessAffordability   phase 2 — the measured 1.00 vs 0.28 gate
  DigitAffordability        phase 3 — wallet >= price, exact
```

Digit reading is the only real technical risk in this design, and it is isolated
behind this interface. Phases 1-2 deliver a fully working gated bot with no
dependency on it. If segmentation proves unreliable, brightness affordability
stays and only wave/coins on the dashboard are lost.

The greyed-out "cannot afford" state was **never captured** during
investigation — the run ended before the wallet could be drained. Brightness
affordability is therefore verified for modal-dim only, not for its stated
purpose. This is the reason to prefer reading the numbers.

## 7. Gating and auto-navigate

Cheapest rejects first, so a non-IN_RUN scan costs almost nothing:

```
screen != IN_RUN     -> skip all (screen_gated)
score < threshold    -> no match
brightness < 0.75    -> skip (dimmed)          modal guard
wallet < price       -> skip (unaffordable)
within cooldown      -> skip (cooldown)
                     -> TAP
```

**Why the brightness gate survives screen gating.** On `GAME_OVER` the screen
gate fires first, so the brightness check is never reached there — which makes
it look redundant. It is not. Its job is the case the FSM cannot see: an overlay
that dims the screen *without* displacing the `in_run` anchor, so the FSM still
reads `IN_RUN` while the panel behind is inert (an in-run pause menu, a level-up
popup, a mid-fade frame that survived debounce). Screen gating handles modals we
modelled; the brightness gate handles the ones we did not. Do not remove it on
the grounds that the death-modal case is already covered.

**Wallet estimation.** After a tap the wallet has changed but the frame has not.
Rather than re-capturing after each tap (4 extra ADB round-trips per scan), the
loop decrements a local wallet estimate by the price and continues. The estimate
self-corrects on the next scan.

**Auto-navigate.** On `GAME_OVER`, locate RETRY by template match and tap; on
`MAIN_MENU`, likewise for BATTLE. Never fixed coordinates — the modal moves.
Guarded by the FSM debounce plus a 3s navigation cooldown so a fade frame cannot
double-tap. `--max-runs N` optionally caps a session.

**`--auto-navigate` defaults to off.** A bot that launches battles the moment it
starts is surprising while thresholds are still being tuned.

Note: auto-navigate spends no permanent resources. In-run upgrades are bought
with per-run `$` which resets each run; coins are only ever earned.

## 8. Persistence

```sql
runs(id, started_at, ended_at, wave, coins, tier,
     abandoned, scan_count, tap_count)

events(seq INTEGER PRIMARY KEY, run_id, ts, type, screen, action, reason,
       score, price, wallet, detail TEXT)   -- detail is JSON
```

Typed columns for what is filtered and aggregated; a JSON `detail` blob for the
long tail, so adding a field to an event does not require a migration. WAL mode,
single writer (the store sink's consumer thread), concurrent reads from the web
layer.

**`seq` must be seeded from the database at startup.** It is a monotonic
in-process counter, so a restart would reset it to zero and collide with stored
rows on the primary key. On boot the bus seeds itself from
`SELECT COALESCE(MAX(seq), 0) FROM events`. This also keeps SSE `Last-Event-ID`
resume coherent across a bot restart, not just across a browser reconnect. When
running without `--web` and without a store, the counter starts at zero, which
is harmless because nothing persists it.

**`ScanCompleted` is never persisted.** It fires every 2s — roughly 43,000 rows
per day of near-redundant data. It goes to the TUI and SSE, where "is it alive
right now" is the actual question, but not to disk. The run row carries
`scan_count` instead. Only state-changing events are stored, which keeps the
database small and every row in it meaningful. Events older than 30 days are
pruned at startup.

## 9. Web layer

```
GET /                       static dashboard (single HTML file)
GET /api/status             screen, uptime, current run, counters,
                            last error, dropped-event count
GET /api/runs?limit=50      run history
GET /api/runs/{id}/events   one run's events
GET /api/events/stream      SSE live push
GET /api/unknown            snapshot list (+ /{name}.png)
```

**SSE reconnect.** The server keeps a ring buffer of the last 500 events. A
reconnecting browser sends `Last-Event-ID`; the server replays from there. This
is what `seq` is for.

**Security boundary.** Binds `127.0.0.1`, no auth — localhost is the boundary.
The dashboard exposes session screenshots, so binding `0.0.0.0` requires real
auth first. This warning belongs in `config.py` beside the host setting, not
left implicit.

**Dashboard.** One static HTML file, vanilla JS and `EventSource`. No framework,
no build step. Header (connection, current screen, uptime, run counter); current
run panel (wallet, taps per upgrade); filterable live event feed; run history
table; unknown-screen thumbnails; a wave sparkline drawn on a small canvas —
ten integers do not justify a chart library.

## 10. Testing

The vision layer is pure functions over images, so the frames captured during
investigation become golden fixtures. **The entire suite runs with no emulator
attached**; only a `--once` smoke check needs a device.

```
tests/fixtures/
  main_menu.png        menu
  in_run_lit.png       live run, all four upgrades affordable
  game_over.png        fully dimmed, ratio 0.28
  game_over_fade.png   mid-fade, ratio 0.74
```

`game_over_fade.png` is the frame that fooled the original template capture.
Keeping it permanently regression-tests the debounce and brightness logic
against the exact condition that broke them.

- **FSM classification with margin assertions** — best >= 0.8 *and* runner-up
  <= 0.5. A margin test catches a badly re-cut anchor; a correctness test alone
  would not.
- **`test_game_over_does_not_tap`** — named regression test for the originating
  bug. The game-over fixture must produce **no `Tapped` event**. Asserting the
  absence of the tap is the point; the reason will be `screen_gated`, since the
  screen gate fires before the brightness check. A separate unit test exercises
  `BrightnessAffordability` directly against `game_over_fade.png` to cover the
  gate itself, which the full-loop test never reaches.
- **Debounce** — synthetic reading sequences; transitions only after 2
  consecutive identical readings.
- **Bus back-pressure** — `publish()` must not block when a sink queue is full.
- **Store** — in-memory SQLite; `ScanCompleted` absent, run correlation correct.
- **Digits** — real crops plus `K`/`M` suffix cases.

Device layer mocked throughout. TDD: tests first.

## 11. Phases

| Phase | Delivers | New deps |
|---|---|---|
| 0 | Commit corrected lit templates, screen anchors, fixtures | — |
| 1 | `events.py`, `screens.py`, extracted `device.py`/`vision.py`, log sink. `--once` reports screen; tapping unchanged | pytest (dev) |
| 2 | Gating, brightness affordability, auto-navigate, `--tui`, unknown snapshots. **Originating bug fixed; bot usable** | rich |
| 3 | Digit atlas, exact affordability, wave/coins | — |
| 4 | SQLite + FastAPI + SSE dashboard | fastapi, uvicorn |
| 5 | Rewrite README brightness section; document that templates must be cut from a lit in-run frame | — |

Phase 2 is the milestone; everything after is enrichment.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Digit segmentation unreliable | Isolated behind `AffordabilityCheck`; falls back to brightness, losing only dashboard stats |
| Greyed "cannot afford" state never captured | Phase 3 removes the dependency entirely; capture it during phase 2 to calibrate the interim gate |
| Number suffixes (K/M/B) misread | Atlas includes suffix glyphs; parser tested against them explicitly |
| Unmodelled screens (ads, daily rewards) | UNKNOWN holds and snapshots; snapshots drive adding screens later |
| Emulator resolution change invalidates every template | Startup check: warn if the captured frame is not 1080x2400 |

## 13. Open items

- The greyed-out unaffordable state still needs one capture during phase 2 to
  calibrate `BrightnessAffordability` before phase 3 supersedes it.
- Tier is in the schema but nothing reads it yet; it needs the death modal's
  "Tier N" line, which arrives with phase 3.
