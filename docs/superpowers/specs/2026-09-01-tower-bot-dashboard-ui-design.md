# The Tower bot — dashboard rewrite: Next.js UI, control plane, live device view

Status: implemented, across the three plans in `docs/superpowers/plans/`
(`2026-09-01-dashboard-a-scaffold.md`, `2026-09-01-dashboard-b-control.md`,
`2026-09-01-dashboard-c-views.md`)
Date: 2026-09-01

Supersedes section 9 of `2026-08-30-tower-bot-observability-design.md`.

## 1. Context

Phase 4 delivered a working dashboard: SQLite persistence, a FastAPI JSON API,
an SSE feed, and one hand-written `web/static/index.html` — about 230 lines of
vanilla HTML, CSS and JavaScript with no build step. It works, and it proved
the API shape. It is also the ceiling of what a single static file should be
asked to do. The event feed, the run history, the sparkline and the unknown-
screen strip all share one global scope, one render path and one file.

Three things are wanted from it now, and each one on its own would justify a
rewrite:

- **Look and feel.** A real application UI — sidebar navigation, cards, light
  and dark themes, readable on a phone — rather than one scrolling column.
- **More to see.** The live device frame, per-run drill-down, aggregate stats
  across every stored run, and a proper home for errors and unknown screens.
- **Control, not just observation.** Pause, resume and stop the bot, and change
  what the loop does — scan interval, auto-navigate, affordability strategy,
  which actions are enabled — from the browser.

The third is the significant one. Until now the system has been strictly
one-way: the scan loop publishes to a bus, sinks consume, and the web layer
reads. `db.reader()` opens `mode=ro` specifically so that no route can write.
Control reverses a direction of flow that was deliberately one-way, so it needs
a design rather than an endpoint.

The stack is chosen: Next.js with React and TypeScript, replacing the static
file entirely.

## 2. Goals

- Replace `web/static/index.html` with a Next.js application, statically
  exported and served by the existing FastAPI process.
- Keep `uv run tower_bot.py --web` working on a fresh clone with **no node
  installed** — a one-command bot stays a one-command bot.
- Add a control plane the browser can drive, without weakening the read-only
  guarantee on the database or the bus invariant that `publish()` never blocks.
- Add four views: live device screen, run drill-down, aggregate stats, errors
  and unknown screens.
- Leave the TUI, the log sink, the store sink and the scan loop's existing
  behaviour untouched except where control explicitly changes them.

### Non-goals

- Authentication. The dashboard stays bound to loopback. See §9.
- Raw device remote-control (tap a coordinate, force a screen). Considered and
  cut: it is a debugging tool, not a dashboard feature, and it is the one class
  of command that can put the emulator into a state the bot cannot reason about.
- Server-side rendering, route handlers, or any node process at runtime.
- Multi-host or multi-bot support. One bot, one machine.

## 3. Architecture

```
web/
  ui/                        Next.js 15 App Router · TypeScript · Tailwind · shadcn/ui
    app/layout.tsx             sidebar shell: Live · Runs · Stats · Errors · Control
    app/page.tsx               Live
    app/runs/page.tsx          run list, and drill-down via ?id=
    app/stats/page.tsx         aggregates over every stored run
    app/errors/page.tsx        BotError history + unknown-screen snapshots
    app/control/page.tsx       pause/resume/stop + live settings
    components/                stat cards, event feed, device view, charts
    lib/api.ts                 typed fetch wrappers, one per endpoint
    lib/useEventStream.ts      the SSE subscription hook, called by each route that needs it
    lib/types.ts               TypeScript mirrors of the Python payloads
  static/                    built output — tracked in git
  app.py                     FastAPI: /api/* plus the static mount
control.py                   the control plane's state (new, top level)
```

`control.py` sits at the top level beside `db.py` and `events.py`, not inside
`web/`, for the same reason `db.py` does: the scan loop depends on it, and the
scan loop must not import the web layer.

### Serving model

`next build` in static-export mode emits plain HTML, JS and CSS into
`web/static/`. FastAPI serves that directory exactly as it serves `index.html`
today. One process, one port, no node at runtime.

Development keeps hot reload: `npm run dev` serves the UI on `:3000` with a
`rewrites` rule proxying `/api/*` to `:8765`, while `uv run tower_bot.py --web`
runs the bot as normal. The browser talks to `:3000`; the data comes from the
real bot.

| | Production | Development |
|---|---|---|
| UI | FastAPI serves `web/static/` on `:8765` | `next dev` on `:3000`, HMR |
| API | FastAPI on `:8765` | same, reached through a rewrite |
| node needed | no | yes |

### The static-export constraint on run drill-down

Static export renders one HTML file per route **at build time**. A route
segment like `/runs/[id]` requires `generateStaticParams()` to enumerate its
ids, and run ids do not exist until the bot has run. There is no correct value
to enumerate.

Run drill-down is therefore `/runs?id=42`: one exported route, the id read
client-side from the query string. Deep-linkable, refreshable, and it needs no
SPA fallback rule in FastAPI. This is a real constraint of the chosen serving
model, recorded here so it is not rediscovered as a bug.

### Committed build output

`web/static/` is tracked in git. A fresh clone runs `--web` immediately, with
no node, no `npm install`, no build — which is the property that makes the
static-export model worth its constraints in the first place.

The cost is a build that can go stale against its sources. That is mitigated by
a test, not by discipline: see §8.

## 4. The control plane

### State

```python
@dataclass
class Controls:
    paused: bool = False
    interval: float = config.SCAN_INTERVAL_SECONDS
    auto_navigate: bool = False
    strategy: str = "digits"                 # "brightness" | "digits"
    enabled_actions: set[str] = field(
        default_factory=lambda: {a.name for a in config.ACTIONS}
    )
```

Held behind a lock, with `snapshot()` returning a JSON-safe copy and
`apply(patch)` validating and merging a partial update. The shape deliberately
mirrors `BotState`: one lock, one snapshot method, no shared mutable objects
handed across threads.

**The dataclass defaults are a fallback, not the source of truth.** `main()`
constructs `Controls` from the parsed CLI arguments, so `--interval`,
`--auto-navigate` and `--affordability` keep meaning exactly what they mean
today; the browser then changes them from wherever the command line left
them. A control plane whose defaults silently overrode the flags you
launched with would be a bug, not a feature.

`interval` is seeded from `args.interval` exactly as `auto_navigate` and
`strategy` are seeded from theirs: `tower_bot.py` already defines an
`--interval` flag (defaulting to `config.SCAN_INTERVAL_SECONDS`), and
`main()` already passes `args.interval` into `run_forever`. No new flag is
needed — the dashboard just becomes the other place you change it, once the
bot is running.

### How the loop reads it

`run_once()` takes one snapshot at the top of each pass and uses it for that
whole pass — never re-reading mid-scan, so a setting cannot change underneath a
half-finished scan. `run_forever()` sleeps `controls.interval` instead of its
fixed `interval` argument, so an interval change takes effect on the next sleep.

**Paused keeps scanning.** It captures, classifies, reads the wallet and
publishes `ScanCompleted` as usual; it skips only the action loop and
auto-navigate, emitting one `Skipped(reason="paused")` per scan. A paused bot
that also stopped reporting would blank the dashboard at the exact moment you
paused it to look at something, and would lose the screen tracking that makes
resuming safe.

`enabled_actions` filters `config.ACTIONS` at the top of the action loop.
`strategy` swaps the affordability check through the existing
`build_affordability()`, which already degrades to brightness when no glyph
atlas is available. That degradation is silent today, which is fine for a
startup flag and wrong for a live setting: switching to `digits` in the browser
and getting brightness without being told would misrepresent what the bot is
doing. So a request for `digits` with no usable atlas is **rejected with 422
naming the field**, and the setting stays where it was.

Stop reuses the `--max-runs` exit path that already works: set the shared `stop`
Event and call `bot.stop()`. The loop returns, uvicorn's server exits, the SSE
generator ends on the `stop` flag, and the process winds down through code that
is already tested. No second shutdown path.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/control` | current knobs, plus the list of action names |
| `PATCH` | `/api/control` | partial update, Pydantic-validated |
| `POST` | `/api/control/stop` | stop the bot and the server |

`PATCH` returns the full new state, so a client never has to guess what was
accepted. Validation failures return 422 naming the field.

### Control changes are events

Every accepted change publishes a new `ControlChanged` event on the bus. It
reaches the feed, the TUI and SQLite alongside everything else.

This is not decoration. The premise of the whole system is that state changes
are events; a control surface that mutated the bot without leaving a trace
would be the one thing in it you cannot reconstruct afterwards — and "why did
it stop tapping at 3am" is precisely the question the event log exists to
answer. The `events` table's JSON `detail` blob absorbs the new fields with no
schema migration.

```python
@dataclass(frozen=True, kw_only=True)
class ControlChanged(Event):
    changed: dict[str, Any]   # only the fields that actually changed
    source: str = "web"
```

### What control does not touch

`db.reader()` still opens `mode=ro`, and the web layer still cannot write to
the database. What it now writes to is one in-memory dataclass. The bus
invariant is untouched: control publishes through `bus.publish()`, which never
blocks.

## 5. The live device screen

A `FrameBuffer` on the bot stores, under a lock, the frame captured by
`refresh_screen()`, that scan's match boxes and tap points, and a monotonically
increasing frame number.

`GET /api/frame` serves `multipart/x-mixed-replace` MJPEG. A plain `<img>` tag
renders it natively — no polling loop, no cache-busting query strings, no
JavaScript decoding path. JPEG bytes are encoded **once per frame and cached**
against the frame number, so ten open tabs cost one `cv2.imencode` per scan
rather than ten.

Overlays travel as JSON on `/api/status`, not burned into the pixels. React
positions them absolutely over the image. This keeps the JPEG a plain cacheable
image, lets the overlay be toggled off, and turns scaling into a CSS problem
instead of a coordinate-mapping problem in two languages.

## 6. Aggregates and errors

New query functions in `db.py`, as SQL aggregates against the existing
`events_ts_idx` and `events_run_idx` — not Python loops over `list_runs()`:

| Function | Answers |
|---|---|
| `run_stats(conn)` | wave, coins and duration per run, over time |
| `taps_by_action(conn)` | which upgrades actually get bought, and how often |
| `screen_histogram(conn)` | where the bot spends its scans |
| `error_log(conn, limit)` | `BotError` rows with their tracebacks |

Served by `GET /api/stats` and `GET /api/errors`. Charts use Recharts. The
`dataviz` skill is to be loaded before the first chart is written.

## 7. Data flow

- **Live.** One SSE subscription to `/api/events/stream` per route, via the
  `useEventStream()` hook. The design called for that subscription to be
  owned by a shared React context so several consumers on one page could
  fan out from it; that was **not built**. Each route that needs the stream
  (Live, Control) calls the hook directly and gets its own connection —
  no route has more than one consumer, so a context would be indirection
  with no consumer to share it with. This is a deliberate deviation, not an
  oversight: add the context back if a future page needs more than one
  subscriber per tab.
- **Status.** `/api/status` polled every 2s, as today, for the authoritative
  snapshot plus the frame overlay boxes. Deliberately kept separate from the
  stream: it is cheap, it is a full snapshot rather than a fold over deltas,
  and it is what makes a freshly opened tab correct immediately.
- **History.** Fetched on navigation, not streamed.
- **Control.** `PATCH` returns the new state; the same change also arrives over
  SSE as `ControlChanged`, so a second open tab converges without polling.

## 8. Testing

Python, under the existing pytest suite:

- `control.py` — patch semantics, validation, thread safety, and that a
  rejected strategy change leaves state untouched.
- The control endpoints, including 422 shapes.
- The loop honouring `paused` (scans and publishes, does not tap or navigate),
  `interval`, and `enabled_actions`.
- `/api/frame` against a synthetic frame, and that encoding happens once per
  frame rather than once per subscriber.
- The stats and errors SQL against an in-memory database.

React, under Vitest and Testing Library, for the logic worth testing on its
own: event formatting, the stream reducer, and `lib/api` response parsing. No
Playwright — the end-to-end value here is low and the maintenance cost is not.

**Build freshness.** One test fails if `web/static/` does not match the sources
under `web/ui/`. This was designed as an mtime comparison but built as a
content hash instead: git does not preserve modification times, so a fresh
clone's checked-out files would all get the same mtime and an mtime check
would decide nothing. `tools/ui_manifest.py` (mirrored byte-for-byte by
`web/ui/scripts/manifest.mjs`) hashes every source file under `app/`,
`components/`, `lib/`, `public/` and a fixed list of top-level config files,
and the build writes that hash into `web/static/.build-manifest.json`; the
test recomputes the hash from source and compares. Committing build output
creates exactly one new failure mode — a stale bundle shipped alongside
fresh source — and it is caught by a test rather than by remembering.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Control endpoints on an unauthenticated server | Binding stays `127.0.0.1`; the warning beside `config.WEB_HOST` is strengthened to say the surface is no longer read-only. A non-loopback `--web-host` already warns; that warning now matters more. |
| Stale committed build | Test fails on `web/static/` not matching a content hash of `web/ui/` (§8). |
| MJPEG connection held open blocks shutdown | Same failure that SSE already hit and solved: the frame generator checks the shared `stop` Event, exactly as `event_stream()` does. |
| Frame encoding on the request thread stalls the loop | Encode is cached per frame number and happens on the web thread, never on the scan loop. `cv2.imencode` releases the GIL. |
| Pause leaves the screen tracker stale | Pause keeps scanning and classifying (§4), so the tracker stays current and resume is safe. |
| A setting changes mid-scan | One snapshot per pass, taken at the top of `run_once()` (§4). |

## 10. Delivery

Three plans, each independently shippable. After Plan A the dashboard is
strictly better than what exists today, and nothing in B or C is load-bearing
for it.

| Plan | Delivers | New deps |
|---|---|---|
| A | Next.js + TS + Tailwind + shadcn scaffold, static export, FastAPI serving it, sidebar shell, Live page at parity with today's `index.html`, committed build, staleness test | node toolchain (dev only) |
| B | `control.py`, the three endpoints, `ControlChanged`, loop integration, control UI | — |
| C | Frame streaming and overlay, run drill-down, stats page, errors page | Recharts |
