# The Tower — background ADB bot

Automates the Android game *The Tower* running in the Android Studio emulator.
All input goes through ADB (`input tap`), so the emulator window never needs
focus and your mouse is never taken over.

One scan is: capture a frame, work out which screen is showing, evaluate every
configured action against that frame, and tap the ones that pass. Actions only
fire in a run — the bot will not tap its way through a death modal or an ad.

Everything the scan loop does is published as an event. Sinks consume those
events on their own threads behind bounded queues, so a terminal panel, a
SQLite writer and a browser dashboard can all watch a running bot without
slowing the loop down. When a sink cannot keep up its events are dropped and
counted, never awaited.

## Install

Dependencies are managed with [uv](https://docs.astral.sh/uv/). If you don't
have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, from the repo root:

```bash
uv sync
```

That creates `.venv/`, installs the exact versions locked in `uv.lock`, and
downloads the Python pinned in `.python-version` (3.12) if it isn't already on
your machine. There is nothing to activate — every command below runs through
`uv run`, which re-syncs the environment first.

## Connect to the emulator

`adb` must be on your PATH (Android Studio ships it at
`~/Library/Android/sdk/platform-tools`):

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"
adb start-server
adb devices          # should list emulator-5554
```

`config.py` holds both endpoints: `ADB_HOST`/`ADB_PORT` for the adb *server*
(default `127.0.0.1:5037`) and `DEVICE_HOST`/`DEVICE_PORT` for the emulator
itself (default `127.0.0.1:5555` — an AVD listens on its console port + 1).
`--host` / `--port` override the device endpoint for a single run.

**The emulator must be 1080x2400** (`EXPECTED_RESOLUTION`). Every template and
every screen region in `config.py` was measured at that size, and
`cv2.matchTemplate` is not scale invariant — a different resolution invalidates
all of them at once.

## Run

```bash
uv run tower_bot.py                      # scan every 2s until Ctrl+C
uv run tower_bot.py --auto-navigate      # and loop runs unattended
uv run tower_bot.py --tui                # live terminal panel
uv run tower_bot.py --web                # browser dashboard on :8765
uv run tower_bot.py --debug-scores       # one-shot diagnostic, taps nothing
```

| Flag | Default | What it does |
|---|---|---|
| `--host` / `--port` | `127.0.0.1:5555` | emulator ADB endpoint |
| `--interval N` | `2.0` | seconds between scans |
| `--once` | off | scan just long enough for the screen tracker to settle, then exit |
| `--debug-scores` | off | capture one frame, print every action's and anchor's match score plus brightness ratio, exit without tapping |
| `--tui` | off | live `rich` panel instead of log lines |
| `--auto-navigate` | off | tap RETRY / BATTLE to loop runs unattended |
| `--max-runs N` | unlimited | stop after N runs |
| `--affordability` | `digits` | `digits` (read the numbers) or `brightness` (the older heuristic) |
| `--web` | off | serve the dashboard while the bot runs |
| `--web-host` / `--web-port` | `127.0.0.1:8765` | where the dashboard binds — read the warning below before changing the host |
| `--db PATH` | `tower_bot.db` | SQLite file for the event log |
| `--no-store` | store | run with no database at all |

`--tui` and `--web` can be on at once.

## Screens, gating, and runs

Every scan starts by asking which screen is showing. Three anchor templates are
matched against the frame and the best score wins, provided it clears
`ANCHOR_THRESHOLD` (`0.8`):

| State | Anchor |
|---|---|
| `MAIN_MENU` | `templates/screens/main_menu.png` |
| `IN_RUN` | `templates/screens/in_run.png` |
| `GAME_OVER` | `templates/screens/game_over.png` |
| `UNKNOWN` | nothing scored high enough |

A transition is only believed after `SCREEN_CONFIRMATIONS` (`2`) consecutive
identical readings, so one ambiguous frame mid-animation does not move the FSM.

**Gating is the point of all this.** Upgrade actions fire on `IN_RUN` and
nowhere else. That rule is hardcoded in `tower_bot.py` — it is not a per-action
setting — and it is what stops the bot tapping blindly into a modal that has
dimmed the screen behind it.

Skips are recorded rather than silent, with five reasons, checked cheapest
first: `paused`, then `screen_gated`, then the match score, then
`unaffordable` / `dimmed`, then `cooldown`.

### Unknown screens

Ads, daily rewards, anything unmodelled: the bot holds still. It taps nothing,
and writes the frame to `unknown/` as a nanosecond-timestamped PNG so you can
see what it hit. Writes are throttled to one per `UNKNOWN_MIN_INTERVAL`
(`30.0`s) and the directory is pruned to the newest `UNKNOWN_KEEP` (`50`). The
directory is gitignored; the dashboard shows the newest twelve as thumbnails.
These are how you decide which screen to model next.

### Runs

A run opens on the first confirmed `IN_RUN` and closes on the first confirmed
state that is not `IN_RUN` — `GAME_OVER` is a normal end, `MAIN_MENU` closes it
as abandoned. `UNKNOWN` neither opens nor closes one, so a run survives an ad
appearing mid-fight. On a clean end the bot reads wave, coins and tier off the
death modal.

Run ids are the primary key of the `runs` table, so at startup they are seeded
from `MAX(id)`: kill the bot and restart it and the next run is numbered after
the last stored one instead of overwriting it. Event sequence numbers are
seeded the same way, and both are read *before* the retention prune so an
aged-out number is never reissued. Under `--no-store` both restart at 1,
because there is nothing to collide with.

### `--auto-navigate`

Off by default. The bot still watches, gates, tracks runs and snapshots unknown
screens — it just never taps between screens, so it sits on the death modal
until you act.

Turned on, it taps exactly two buttons: `RETRY` on `GAME_OVER` and `BATTLE` on
`MAIN_MENU`, each at the matched template's centre, no closer together than
`NAVIGATION_COOLDOWN_SECONDS` (`3.0`s). `--max-runs N` stops after N runs and
suppresses the RETRY that would have started run N+1.

## Capturing templates

```bash
uv run grab_screen.py screen.png
```

Crop each button out of `screen.png` and save the crop into `templates/`.

Two rules, and breaking either one produces a template that matches nothing or
matches everything:

1. **Same emulator resolution as the bot runs at** (1080x2400).
   `cv2.matchTemplate` is not scale invariant.
2. **Cut from a lit, in-run frame.** Not from a screenshot where a modal has
   dimmed the screen, not from a greyed-out unaffordable button, not from a
   promotional image. The brightness check below compares each candidate region
   against the template's own grey level, so a template cut from a dimmed frame
   sets the baseline at the dimmed value and defeats the check entirely.

`tools/crop_preview.py` previews a candidate region against a captured frame
and prints a paste-ready `config.Region(...)` line, which is how the screen
regions in `config.py` were measured:

```bash
uv run tools/crop_preview.py tests/fixtures/in_run_wallet.png \
    --anchor IN_RUN --rect 40 -60 220 46 --out /tmp/crop.png
```

## Reading the numbers

The default affordability strategy, `digits`, reads the actual numbers off the
screen: it reads your wallet once per scan and each upgrade's price from beside
its matched label, and buys when `wallet >= price`. That is exact, and it is
also where the dashboard's wave, coin and tier stats come from.

It works by template-matching individual glyphs against an atlas. Because
`cv2.matchTemplate` is not scale invariant and the game renders numbers at three
sizes, there is one atlas per size class — `wallet`, `price` and `modal` — under
`templates/atlas/`, each a flat directory of single-glyph PNGs named for the
character they represent (`7.png`, `dollar.png`, `cap_w.png`).

Every read is all-or-nothing and every failure returns `None` rather than
raising: a partially recognised number is never returned, because reading `1?34`
as `134` would let the bot act on a price that looks plausible and is wrong by
an order of magnitude.

### Building the atlas

The committed atlas was built from a live emulator, so you only need this if a
resolution change invalidates it. One size class per run, and each wants a
different part of the game on screen:

```bash
# 1. start a run, then harvest the two in-run classes
uv run build_atlas.py --size-class wallet --frames 20
uv run build_atlas.py --size-class price  --frames 20

# 2. label wallet by hand — it is the bootstrap reference, with nothing
#    to score against:  templates/atlas/wallet/glyph_003.png -> .../7.png

# 3. auto-label the rest against it
uv run tools/label_glyphs.py --size-class price            # dry run + contact sheet
uv run tools/label_glyphs.py --size-class price --apply    # rename

# 4. let the run end, then harvest the death modal
uv run build_atlas.py --size-class modal --frames 10
uv run tools/label_glyphs.py --size-class modal --apply

# 5. verify
uv run pytest tests/test_atlas_real.py -v
```

`build_atlas.py` dumps unlabelled `glyph_NNN.png` files; `tools/label_glyphs.py`
scores them against an already-complete class and renames the confident
matches, writing a contact sheet of everything it was unsure about. Rename
those by hand — the caption letters and the coin icon score low precisely
because no reference class contains them.

### Known gap: no suffix glyphs

`K`, `M`, `B`, `.` and `,` are **not in any atlas on disk**. The parser handles
suffixes correctly, but the glyph matcher never gets that far, so a wallet or
price rendered as `$1.5K` fails to read and that action silently falls back to
brightness for the scan. Harvest those glyphs during a session that reaches
large enough numbers, and label them by hand off the contact sheet.

### Brightness, the older heuristic

`--affordability brightness` selects it outright. Otherwise it is the fallback:
`digits` uses it for any single read it could not complete, and
`build_affordability` drops to it for the whole session if *any* size class is
missing from the atlas.

The two are distinguishable in the event log on purpose. A skip reason of
`unaffordable` means both numbers were read and the wallet was genuinely short;
`dimmed` means we could not tell and fell back.

`cv2.TM_CCOEFF_NORMED` normalises away mean and variance, so it is **blind to
brightness**: a greyed-out "can't afford it yet" button still scores `1.000`
against a template cropped when the button was lit. So each match is also
checked against the template's own grey level and skipped when the region is
too dark. Tune it per button with the `Action`'s `brightness_ratio` field
(default `DEFAULT_BRIGHTNESS_RATIO`, `0.75`); `0.0` disables the check for that
action. `--debug-scores` prints the measured ratio next to the score, so the way
to pick a value is to read the ratio in both states and set the threshold
between them.

> **The `0.75` default is only half-calibrated.** Measured on the live device: a
> lit in-run button reads `1.00` and a modal-dimmed one reads `0.28`, so the
> modal case is verified with a wide margin. The greyed-out *unaffordable* state
> has never been captured, so brightness has never actually been measured doing
> the job it is named for. This is tolerable only because `digits` replaced it
> as the default. If you fall back to brightness deliberately, calibrate it
> first: spend your wallet down until an upgrade greys out, run
> `--debug-scores`, and check the greyed value really is well below `0.75`.

## Watching it work

Neither view is required — with no flags the bot writes ordinary log lines.

**`--tui`** replaces the log with a `rich` panel refreshed four times a second:
current screen, uptime, scan count, a tap tally per action, a skip tally per
reason, the last error, and the twelve most recent events. Logging is silenced
while it runs, because rich owns the terminal.

**`--web`** serves a dashboard at `http://127.0.0.1:8765`: the current run, a
wave sparkline, a filterable live event feed, a run-history table, and
thumbnails of unrecognised screens. Click a history row to replay that run's
stored events; **back to live** returns to the stream. The feed arrives over
SSE and reconnects on its own — a laptop that slept resumes from
`Last-Event-ID` rather than starting blank, as long as it was gone for less
than the 500-event ring.

It also serves a **control** page: a connected browser can pause the bot
(still scanning, not tapping), change the scan interval, auto-navigate, and
the affordability strategy, choose which actions are enabled, and stop the
bot and the dashboard together. Every change goes through the same
`Controls.apply()` validation a CLI flag would get, and every tab converges
on the current settings live, over the same SSE feed.

> **The dashboard has no authentication.** It serves screenshots of a live
> session and this machine's entire event history, and its control page lets
> anyone who can reach the port pause the bot, change what it buys, or stop
> it outright — which is why it binds loopback. `--web-host` will let you
> bind something else, and the bot logs a warning when you do, but it will
> not stop you. Do not put it on a network without real auth in front of it.

Ctrl+C stops both the bot and the dashboard, as does reaching `--max-runs` or
clicking **Stop** on the control page.

## Where the data goes

Events are written to SQLite (`tower_bot.db` by default, `--db` to move it,
`--no-store` to turn it off entirely). Two tables: `runs`, one row per run with
its wave, coins, tier, scan count and tap count; and `events`, one row per
state change.

`ScanCompleted` is deliberately **not** stored — it fires every two seconds,
roughly 43,000 near-identical rows a day. The run row carries `scan_count`
instead. What is left is state changes only, and `EVENT_RETENTION_DAYS` (`30`)
of those stays small. Pruning runs once at startup.

Columns worth querying are typed; everything else on an event goes into a JSON
`detail` blob, so adding a field to an event needs no migration. The writer sink
owns the only write connection and the web layer opens the file read-only, so
the dashboard physically cannot corrupt the log.

A run left open by a killed process is closed out and marked `abandoned` on the
next startup.

## Configure

Add buttons in `config.py` → `ACTIONS`, one `Action(name, template, threshold)`
per button. All of them are evaluated top to bottom against the same captured
frame on every scan — matching is not exclusive, so several can fire in one
pass, in list order:

```python
ACTIONS: tuple[Action, ...] = (
    Action(name="Damage", template="upgrade_damage.png", threshold=0.9),
)
```

Use `--debug-scores` to see the best match score for every template and pick a
threshold just below the score of a real match (the default is `0.8`).
`CLICK_COOLDOWN_SECONDS` (`1.0`) keeps a slow UI animation from producing a
burst of taps on a button that was already pressed.

## Dependencies

Use uv rather than editing `pyproject.toml` by hand — it updates the lockfile
and the environment in one step:

```bash
uv add <package>
uv remove <package>
uv lock --upgrade      # refresh the locked versions
```

Commit `pyproject.toml` and `uv.lock` together so everyone resolves to the same
versions.

The dashboard is a separate story: its source is the Next.js app in
`web/ui/`, and `npm run build` there republishes the git-tracked
`web/static/` that the bot actually serves. Node is only needed to change the
dashboard — never to run the bot.

## Tests

```bash
uv run pytest -q
```

No emulator and no browser required — the suite runs against committed
fixtures. It does check that `web/static/` isn't stale against `web/ui/`, so
if you edit the dashboard, rebuild it first (`npm run build` in `web/ui`) or
this suite goes red; `npm test` in `web/ui` runs the dashboard's own tests.
