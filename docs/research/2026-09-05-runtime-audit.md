# Tower bot runtime and active-task audit

Observed 2026-09-05, Asia/Jerusalem. Read-only inspection: no game taps, configuration changes, process restarts, or purchases were performed by this audit. The machine-readable API responses and UTC capture timestamp are in `2026-09-05-runtime-snapshot.json`.

## What is actually running

| Surface | Observation | Interpretation |
|---|---|---|
| Main server | PID 19668, started September 4 at 23:45:52 local, `tower_bot.py --web`, working directory this repository, `127.0.0.1:8765` | Existing Python process; files changed after process startup are not necessarily loaded |
| Preview | PID 21312 initially listened on 8766 with `--idle --no-store` and temporary strategies | Connection refused when the API audit ran; not evidence of another operational bot |
| Device | Emulator/ADB listeners are present; live API frame size 1080×2400 | Correct configured resolution, not proof of every screen being recognized |
| Session | `bot.running=true`, `screen=GAME_OVER`, 836 scans, zero completed runs, no taps, 836 screen-gated skips, no current error | Process is healthy enough to observe, but is not playing |
| Configuration | `auto_navigate=false`, `autopilot.enabled=false`, `shopping.enabled=false`, `shopping.armed=false`, `coin_budget=0`, card buying disabled | Current inactivity is explained by settings, not evidence that navigation/purchasing code is absent |
| Active profile | `default`, the only listed profile; four enabled legacy Attack rules | OCR autopilot has no enabled rules in this profile |
| Autopilot | Idle, waiting for battle; zero verified purchases; empty combat and row observations | No live end-to-end OCR purchase verification from this session |
| Catalog | `/api/upgrades` returns 52 entries across Attack, Defense and Utility | Catalog coverage exists; does not establish that this account has unlocked or successfully purchased every entry |
| Advisor | `/api/advisor` returns 404 and no advisor routes appear in this process's OpenAPI schema | New advisor source is not loaded by this server |
| History | Seven stored run rows; some have missing wave/coins, one zero-duration row, some abandoned | Useful instrumentation exists; not an unattended reliability benchmark |
| Tier advice | Only Tier 1 has qualifying history; approximately 500 coins/hour from five eligible rows | Advice compares observed data, does not select the in-game tier; cannot establish the best tier overall |
| Ledger | Run payouts visible; balances unknown; historic `SHOP_UNAVAILABLE` due to missing header-atlas digits | Ledger exists, but these recent records do not prove successful permanent purchases |
| Historic errors | Device-offline and no-ADB-device errors | Recovery is a real operational requirement; they are not current errors |

## Browser-visible features

The served dashboard has Live, Runs, Stats, Errors, Ledger, Strategy, Control, and Guide navigation. Live shows the device feed, match overlay control, screen/run/wave data, autopilot decisions, tier comparison, event feed, run history and unknown-screen gallery.

Strategy exposes Battle/Workshop purchase contexts; Attack/Defense/Utility grouping and search; Manual/Turtle/Health presets; observed availability and price; target values; purchase priority; scan/show-category/buy-once commands; draft Save/Revert semantics; timing, jitter, run policy, shopping budget/reserve/unlock gates and card-buy settings. Controls are appropriately disabled when the bot is outside battle. These are visible controls, not proof each action works on all account stages.

The Guide page's account box explicitly says it was measured on September 3 (1.77K coins, 40 gems, Tier 1, highest wave 10). Treat it as historical documentation. The live API reports no current wallet; do not pass the hardcoded Guide box into an optimizer as current account state.

## Current Codex task

Read the user-owned task **Expand bot upgrade autopilot** (`01a06e04-d50c-76f0-b6ab-12d9feb436a5`). The app reports it active and its readable turn in progress. Its request covers OCR battle category switching, broader purchases, Workshop unlocks, richer strategy controls and beginner/tier-guide integration. Its readable latest commentary describes economy-first spending, Turtle-to-health progression and measured coin-rate tier choice. A permanent-spending preference question is visible in that task.

The task's retrieved transcript is a limited status surface. The filesystem contains substantial uncommitted work, including guide presets and advisor import files, beyond the latest readable commentary. The audit therefore inventories the actual files separately and does not use task text as proof of implementation or successful tests. The source tree and static assets can change during this audit.

## Immediate consequences for the roadmap

1. Establish a reproducible baseline with backend version, frontend build version, active strategy and capability flags. Hot-replaced static files with an older Python process can make features look available while their APIs are missing.
2. Make operational readiness explicit: running, observing, farming, progressing, blocked and recovering are different states. Expose the reason and next automatic step.
3. Validate the existing battle and Workshop executors before duplicating them. Capture real post-purchase evidence, multi-tab traversal, unlock discovery and a complete run → home → maintenance → run cycle.
4. Build durable account state; the live session currently lacks most optimizer inputs, and static Guide content is not a substitute.
5. Keep historical observations and proposed future acceptance targets separate. No broad test suite was run for this read-only audit.
