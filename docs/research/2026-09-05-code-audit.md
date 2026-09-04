# The Tower bot — source capability audit

Audit date: 2026-09-05. Scope: the current working tree at `/Users/shahar/BifrostProjects/thetowerbot`, including uncommitted work. This is source inspection, not a claim of successful device execution or passing tests. No tests, device actions, strategy writes, commits, or pushes were performed for this audit. The running-session review is a separate part of the parent research task.

The branch observed was `codex/ocr-autopilot`, tracking `origin/codex/ocr-autopilot`. Another task was actively changing this checkout, including formatting `advisor.py` and its React panel during inspection. Line references identify the inspected region and can move. New advisor and guide-preset files were untracked; upgrades, shopping, policy tests, web API/UI, static exports, and README had uncommitted changes. Therefore “present in source” must not be equated with “committed,” “loaded in the existing process,” or “live verified.”

## Overall finding

The project has a substantial local observation/control foundation and an early-game upgrade bot. It can scan an Android emulator, manage a battle loop, make semantic OCR purchases, navigate standard upgrade tabs, perform bounded Workshop shopping, buy card batches, display live evidence, and persist run/economy history. Its current planning engine is a handful of manually curated priorities, not a complete account optimizer.

The new Effective Paths component is a normalized recommendation **import and draft-staging adapter**. It neither reads/recalculates the native Effective Paths workbook nor synchronizes account inputs. It only stages known Workshop stat-value targets costing coins; level-based recommendations and every other system remain advisory. The user must currently acquire/normalize an export, import it, stage chosen rows, save a strategy, and configure spending authority. There is no autonomous observe → update calculator → compare opportunities across systems → execute → verify → recalculate loop.

## Evidence scale

- **Implemented:** a callable execution path is present in current source.
- **Partial:** some useful code exists, but it lacks the broader concept, coverage, wiring, or lifecycle needed for unattended completion.
- **Advisory/static:** text, recommendation display, catalog identity, or schema support only.
- **Absent in inspected production code:** no operational handler was found in the relevant backend/UI code. This is scoped to this repository and snapshot.
- **Tests present:** test source asserts the behavior. Tests were not executed, so this is not a pass report.

## Current product surface

The UI is Next.js 15 / React 19 with a static export served by FastAPI; automation is Python 3.12, OpenCV, RapidOCR, adbutils, and local SQLite, not the Imagen stack in the global instructions. See `pyproject.toml:1`, `web/ui/package.json:1`, and `web/app.py:225`.

| Feature | Present behavior and limits | Source evidence |
|---|---|---|
| Live dashboard | Current screen, status, wallet, scans/taps/skips, live event feed, run history, unknown snapshots, device view, and autopilot explanation. | `web/ui/app/page.tsx:36`; `web/ui/components/AutopilotStatus.tsx:36`; `web/ui/components/DeviceView.tsx:1` |
| Control room | Start/stop worker, pause/resume, auto-navigation switch, speed target and commands, active strategy settings, and shutdown. Scan pause continues observation but suppresses normal actions. | `web/ui/app/control/page.tsx:1`; `web/app.py:447`; `web/app.py:558`; `web/app.py:579`; `tower_bot.py:380` |
| Strategy profiles | Read/create/update/activate/delete profiles; priority order and per-rule settings; most configuration read each scan, tracker debounce/navigation cooldown apply on next Start. | `strategy.py:502`; `strategy.py:799`; `web/app.py:594`; `web/ui/app/strategy/page.tsx:1` |
| OCR purchase planner | Battle and Workshop contexts; searchable upgrade catalog; observations, targets, priorities, categories, source-backed preset plans, unknown/locked/unreadable distinction. | `web/ui/components/AutopilotEditor.tsx:236`; `web/ui/app/strategy/page.tsx:223` |
| Live semantic commands | Buy one known battle upgrade, show a category, or scan upgrades. Commands are queued with expiry and only control a running, unpaused battle. | `autopilot.py:126`; `web/app.py:285`; `web/ui/components/AutopilotEditor.tsx:470` |
| Workshop/card controls | Enable, arm, visit cadence, tap cap, coin budget/reserve, unlock permission; card batch, gem floor, per-visit purchase cap. | `strategy.py:335`; `strategy.py:363`; `web/ui/components/ShoppingEditor.tsx:1` |
| Effective Paths advisor | Local JSON/normalized CSV import, source/account timestamp/version, health/damage/economy labels, visible blockers, templates, staging into an unsaved draft. | `advisor.py:1`; `web/advisor.py:49`; `web/advisor.py:91`; `web/ui/components/AdvisorPanel.tsx:34` |
| Runs | Completed/abandoned runs, wave, tier, payout, duration, purpose, per-run event drilldown. | `runs.py:1`; `web/ui/app/runs/page.tsx:1`; `web/app.py:331` |
| Statistics | Wave/run length charts, taps by action, skip/screen aggregates. Farm-tier comparison is based on observed coins per real hour. | `web/ui/app/stats/page.tsx:1`; `progression.py:8` |
| Errors | Stored error list and unknown-screen images. | `web/ui/app/errors/page.tsx:1`; `web/app.py:702`; `snapshots.py:1` |
| Ledger | Non-battle coin/gem history, payouts, Workshop/card costs, rehearsals, skips, visit/policy records, unexplained reconciliation, filters/pagination. | `ledger.py:1`; `web/ui/app/ledger/page.tsx:1`; `web/app.py:713` |
| Guide | Embedded beginner advice and links for Workshop/gems/cards/limitations; its account table is hardcoded from 2026-09-03. This page is not a current account scanner. | `web/ui/app/guide/page.tsx:33`; `web/ui/app/guide/page.tsx:52`; `web/ui/app/guide/page.tsx:293` |

Navigation is explicitly Live, Runs, Stats, Errors, Ledger, Strategy, Control, Guide (`web/ui/components/Sidebar.tsx:19`). There are no operational Labs, UWs, Modules, Events, Tournaments, Perks, or account-inventory pages.

## Execution path already implemented

1. The runner builds one bot worker, connects to an ADB device, restores sequence/run numbering, and reuses shared controls/sinks. Duplicate starts are rejected; stopping the worker leaves the web server alive (`runner.py:128`, `runner.py:219`, `runner.py:247`).
2. Each scan obtains one strategy snapshot and screenshot, classifies the lifecycle screen, debounces transitions, opens/closes runs, and reads current cash only from an appropriate battle frame (`tower_bot.py:430`).
3. Screen lifecycle recognizes MAIN_MENU, IN_RUN, GAME_OVER, UNKNOWN. Menu-page recognition separately supports MAIN_MENU/WORKSHOP/CARDS/MISSIONS. Recognition of Missions is not mission completion (`screens.py:1`, `pages.py:1`, `config.py:280`).
4. Speed management and control commands execute before automated purchasing. When OCR autopilot is enabled or has manual work, it owns battle purchasing; otherwise legacy template action rules run (`tower_bot.py:548`).
5. Battle autopilot observes the visible upgrade panel and HUD, merges short-lived observations, chooses a rule, switches tabs/scrolls when necessary, validates available cash/reserve/price, taps one purchase, and waits for another frame to show a value/price/MAX change (`autopilot.py:199`).
6. A due shopping session can take ownership while on MAIN_MENU, blocking battle navigation during its visit. It visits categories according to global row priority, reads actual coins, enforces constraints, verifies Workshop purchases/unlocks, optionally buys cards, and returns to battle (`tower_bot.py:578`, `shopping.py:231`, `shopping.py:613`).
7. Navigation otherwise taps RETRY after death or BATTLE from the main menu. Maximum-run limits suppress starting another run (`config.py:145`, `navigate.py:38`, `tower_bot.py:355`).
8. Events fan out to bounded nonblocking sinks for logs/TUI/state/SSE/SQLite. Unknown screens are captured with throttling/retention; unexpected scan errors are reported and retried (`events.py:1`, `sinks/base.py:1`, `tower_bot.py:643`).

## Upgrade and decision coverage

### Semantic catalog

The catalog represents **59 identities: 48 standard stat rows plus 11 explicit early unlock tiles**, distributed 20 Attack / 23 Defense / 16 Utility. This is a repository catalog count, not a claim that it covers the complete current game. `upgrades.py:44`; catalog assertions in `tests/test_autopilot_policy.py:29`.

- Attack: Damage, Attack Speed, Critical Chance/Factor, Range, Damage/Meter, Multishot Chance/Targets, Rapid Fire Chance/Duration, Bounce Shot Chance/Targets/Range, Super Crit Chance/Multiplier, Rend Armor Chance/Multiplier.
- Defense: Health, Health Regen, Defense %, Defense Absolute, Thorns, Lifesteal, Knockback Chance/Force, Orb Speed/Count, Shockwave Size/Frequency, Land Mine Chance/Damage/Radius, Death Defy, Wall Health/Rebuild.
- Utility: Cash Bonus/Wave, Coins per Kill Bonus/Wave, Free Attack/Defense/Utility Upgrades, Interest/Wave, Recovery Amount, Max Recovery, Package Chance, Enemy Attack/Health Level Skip.
- Explicit unlock chains: Range/Damage per Meter, Multishot, Rapid Fire, Defense %/Absolute, Thorns, Lifesteal, Knockback, Orbs, Cash Bonuses, Coin Bonuses, Free Upgrades.

Later stat names existing in this catalog does not imply that the bot knows their unlock prerequisites, lab dependencies, module/UW interactions, optimal levels, or whether it should buy them. Decreasing-value targets are specially handled for Shockwave Frequency and Wall Rebuild (`upgrades.py:277`).

### Presets

Only `manual`, `turtle`, and `health` exist (`policy.py:16`). The manually ordered policy picks the first enabled observed buyable rule. Turtle estimates survival from enemy damage and Defense %/Absolute with a buffer, prioritizes early economy, and uses wave-derived Thorns breakpoints (11/21/34/51). Health uses a fixed 75% current/max health threshold and early-economy wave threshold. These are deterministic heuristics, not an Effective Paths marginal-efficiency computation (`policy.py:309`, `policy.py:343`, `policy.py:439`, `policy.py:489`).

Guide presets supply paired battle and Workshop plans while retaining user enable/arm/budget settings. Their references are Beginner, Health, Turtle, Blender guides. Health guidance avoids early Wall/Rend, but there is no complete game-stage transition manager or dynamic build classifier (`guide_presets.py:34`, `guide_presets.py:67`).

### OCR evidence model

Observed upgrade fields are identity/category/context/value/price/status/time/rectangle/tap point. Combat extracts a small HUD subset: wave, enemy damage, current/max health, and cash. It does not provide base Workshop level, lab level, UW ownership, equipped cards/modules, stone/cell/shard balances, or complete account progression (`perception.py:43`, `perception.py:65`, `perception.py:75`).

Unknown names can be displayed as `discovered:*`, but their semantic identity is unconfirmed. Purchases use known rules; an unknown row becoming visible does not automatically teach the optimizer what it means. Battle cache entries expire after 60 seconds; UI marks observations stale sooner. Workshop observations are in-memory, not a versioned persistent account model (`autopilot.py:20`, `autopilot.py:37`).

## Effective Paths integration boundary

Current source contains all of the following:

- A strict schema for source metadata, account snapshot time, missing inputs, recommendation ID/path/system/upgrade/current/target/value kind/cost/currency/benefit.
- Normalized JSON/CSV only, size/row limits, formula rejection, identity validation, local persistence by strategy profile, and 24-hour staleness gating.
- Systems labels for Workshop, lab, ultimate weapon, enhancement, other; currencies include coins/gems/stones/medals/time/other. These labels do not implement those systems.
- Stageability restricted to a supported, non-unlock Workshop stat, coin cost, displayed stat units, complete numeric inputs, improving target, fresh account snapshot, and no missing inputs (`advisor.py:506`).
- Draft staging appends after existing Workshop priorities, preserves an existing row unchanged, checks a modeled prerequisite where available, and explicitly requires Save. It does not save/activate/arm automatically (`web/advisor.py:49`).

Missing for genuine autonomous Effective Paths operation:

1. A verified calculator/workbook input-output contract and version adapter, including sheet/formula semantics and account input coverage.
2. Automated acquisition of current account inputs with units, provenance, timestamps, confidence, and persistent base-versus-derived distinctions.
3. Recalculation or an authorized remote-calculator interface and parity fixtures against known workbook outputs.
4. Mapping from recommended *levels* to executable game actions and observable postconditions; displayed stat numbers alone are insufficient.
5. Cross-system opportunity ranking and resource/time allocation. Imported `benefit` is stored/displayed; there is no ROI allocator using it to coordinate Labs/UWs/Workshop.
6. Autonomous plan promotion under preconfigured user goals, budgets and invariants; prerequisite resolution; replanning after every confirmed relevant change.
7. Action executors and verifiers for each supported calculator recommendation type.

## Partial features that should not count as completed concepts

| Existing piece | What remains missing |
|---|---|
| `purpose = farm/milestone` | Actual milestone objective tracking, tier unlocking/selection, push scheduling, completion detection and reward claims. |
| Farm-tier comparison | Requires at least 3 qualifying farm runs per tier and only returns advice; explicitly does not change game tier (`progression.py:24`). No exploration policy, cells/hour or multiobjective optimization. |
| Missions page template | No task lifecycle, daily/event objective progress, action planning or reward-claim flow. |
| Card buying | No inventory/levels/copies, equipment/slots/loadouts, situational swaps, active-card skills, mastery policy, or lab-slot-first allocator. |
| Late-game stat catalog rows | No comprehensive prerequisites, stage-specific economics, dependencies or account unlock model. |
| Workshop target values | Targets are displayed values, not levels. One successful ordinary row is exhausted for the current visit, so a target can require repeated visits; no proof of best marginal investment. |
| Ledger reserved kinds LAB/CARD_SLOT/MODULE/RELIC/UW/MANUAL | These are explicitly unused future names, not implemented subsystem histories (`ledger.py:28`). |
| Source-backed preset text | Static community priorities, not validated account-specific optimization. |
| Imported recommendations with lab/UW/enhancement labels | Visible advisory rows, no executors or automatic allocation. |
| Error retry | Retry scan after exceptions, not emulator/game process recovery or bounded incident resolution. |

The Guide explicitly states that Labs/lab slots, UW picks/upgrades, card equipment, modules, relics and shop are unsupported (`web/ui/app/guide/page.tsx:293`). This accurately describes major capability gaps even though some of these names occur in schemas.

## Highest-priority autonomy gaps inferred from the actual wiring

These are design/research findings; they have not been reproduced on the live device in this subtask.

1. **Reach the menu when account maintenance is due.** `tower_bot.py:578` starts shopping only on MAIN_MENU, while `config.py:145` routes GAME_OVER to RETRY. There is no due-shopping-aware death → home transition. If current-game RETRY goes directly to the next run, ordinary repeated runs can starve subsequent Workshop visits. The parent live review should validate that game transition.
2. **Reconcile purchase evidence independently of a single stat change.** Battle confirmation accepts any changed displayed value or increased price/MAX, which can also be affected by automatic/free upgrades. Workshop confirmation is stronger for unlocks, but card purchase emits `Purchased` immediately after the tap without post-frame card/balance acknowledgement (`autopilot.py:229`, `shopping.py:746`, `shopping.py:899`). Need transaction evidence across cash/gem deltas, before/after account state, free-upgrade effects, and uncertain outcomes.
3. **Recover progress after process restart.** In-memory `AutopilotState` and pending actions are lost on process restart; runner resets a shopping visit. Persistent ledger/runs exist, but there is no durable action intent/acknowledgement journal and restart reconciliation (`autopilot.py:20`, `runner.py:128`).
4. **Make history durable where decisions depend on it.** The event bus can drop sink events on overflow, including persistence consumers, by design. Counts are visible but there is no durable execution journal behind the action model (`events.py:1`, `sinks/base.py:1`).
5. **Handle all blocking screens.** Unknown pages hold still. Model expected popups/rewards/perk selection/errors/update overlays; add bounded recovery and a defined terminal blocked state instead of indefinite waiting (`screens.py:1`, `snapshots.py:1`).
6. **Extend device/session supervision.** ADB capture errors retry the same loop; connection is established when starting. No persistent supervisor, emulator relaunch/game launch, explicit verified account/device identity or recovery workflow was found. Device fallback selects the first attached device if the expected endpoint is missing (`device.py:29`, `device.py:59`, `tower_bot.py:710`).
7. **Remove calibration as a recurring human dependency.** Supported screen geometry is fixed at 1080×2400; templates/digit atlases and battle-tab geometry are calibrated. Need supported-version detection, layout validation, regression capture corpus, and graceful handling of extra unlocked tabs (`config.py:138`, `autopilot.py:71`).
8. **Resolve missing prerequisites autonomously.** Current unlock mapping covers only 11 early tiles, and unrecognized/missing rows are bounded and marked unknown. A global planner needs unlock/availability/affordability reasons with ordered prerequisite acquisition across systems.
9. **Use measured account state in the product.** Guide page account facts are a dated literal table; there is no durable account snapshot page. Current source, saved strategy, process-loaded implementation, and current game state must be shown as distinct versions.

## Missing game systems to feed into the parent glossary/roadmap

The external wiki review should supply canonical terminology and game-version coverage. From source alone the following operational domains are absent or severely partial:

- Labs: slots, levels, timers, queue/research priorities, acceleration and currency allocation.
- Ultimate Weapons: ownership, unlock choices, upgrade levels, synchronization and activation policies, UW+.
- Cards: inventory/levels/copies, slots, equipment/loadouts, active use and mastery.
- Modules: inventory, rarity/unique effects, levels, merging/shattering, reroll sub-effects, resource budgets, loadouts.
- Perks: acquisition, priority, bans, tradeoffs, build interactions, automatic selection.
- Workshop progression beyond modeled early unlock chains; enhancements; complete cost/level/stat model.
- Tier progression/milestone rewards, farming objectives, tournaments and their changing constraints.
- Events/missions/rewards/shops; daily/weekly activities; medals, gems, stones, cells, shards and other resource flows.
- Account relics/themes/skins/background bonuses and unlock dependencies.
- Event bots and any additional progression systems identified by the current wiki glossary.
- Full combat/build model: damage/effective health/economy, enemy types/modifiers, crowd control, survivability, free upgrades, recoveries, perks/UW/modules/lab interactions.

Do not infer a feature from the generic word “bot,” “lab” inside “label,” a UI paragraph, or an enum reserved for future events.

## Existing validation infrastructure

A source-only inventory found 64 Python files under `tests/` and 25 frontend `*.test.*` files; a pattern count found 864 Python `test_` function definitions. Counts can change because the checkout is active, and parametrization means these are not collected-test counts. No test suite was run.

Useful existing targeted test seams:

| Area | Existing test evidence |
|---|---|
| Catalog, rule validation, guide selection, Turtle/Health behavior | `tests/test_autopilot_policy.py:29` |
| Battle cash/reserve/target gating, tab verification, bounded search, post-frame acknowledgement, expiry, pause/run boundaries | `tests/test_autopilot.py:32` |
| Workshop budget/unlock/target enforcement, exact-child acknowledgement, global priority, savings and scroll bounds | `tests/test_shopping_autopilot.py:61` |
| Visit orchestration, pause, run cap, OCR-unavailable behavior | `tests/test_shopping_loop.py:42` |
| Advisor validation, staleness, import isolation, failed-write preservation | `tests/test_advisor.py:107` (line numbers moved during concurrent edits) |
| Advisor staging is draft-only, preserves existing plans, blocks level targets and missing prerequisites | `tests/test_advisor_api.py:58` |
| Guide preset unlock order and preservation of spending authority | `tests/test_guide_presets.py:53` |
| Ledger arithmetic/reconciliation/backfill | `tests/test_ledger.py:13` |
| Farm-only comparison and legacy run-purpose migration | `tests/test_progression.py:12` |
| Worker lifecycle/concurrent starts/restart numbering | `tests/test_runner.py:101` |
| SSE replay/reconnect and API behavior | `tests/test_web_api.py:193` |
| Frontend planner/advisor and save/profile interactions | `web/ui/components/AutopilotEditor.test.tsx`; `web/ui/components/AdvisorPanel.test.tsx`; `web/ui/app/strategy/page.test.tsx` |

There are meaningful unit/API/fixture seams to extend. The missing validation layer is comprehensive account snapshots/calculator parity plus staged live end-to-end certification across game phases, crashes, unknown screens and resource types. That should use focused fixtures and explicit acceptance scenarios; broad local test-suite sweeps were not needed for this inventory.

## Recommended engineering ordering

1. Freeze and label a source/runtime baseline; reconcile the active implementation task with this audit.
2. Define a canonical game ontology plus versioned persistent account snapshot, with units and provenance.
3. Define every action’s prerequisites, affordability authority, postconditions, evidence and recovery policy; unify transaction verification and durable intent tracking.
4. Complete the normal battle → home → maintenance → battle loop and expected interruption recovery.
5. Formalize the Effective Paths contract and obtain calculator parity using trusted fixtures.
6. Implement account readers and executors in dependency order: Labs and gem allocation, cards/loadouts, UWs, modules, perks, progression/rewards, and later-game systems.
7. Add a cross-system scheduler/optimizer that satisfies the configured goal while respecting resources, time, prerequisites and exclusions.
8. Certify bounded unattended sessions first, then expand by game phase and supported screen/version coverage; make completion measurable by scenarios and unknown-action rate rather than a percentage of catalog names.

No implementation change is proposed as already delivered by this audit. All recommendations are inputs to the parent task’s detailed dependency graph and high-level completion plan.
