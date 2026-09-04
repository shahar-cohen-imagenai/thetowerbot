# Remaining automation task catalog

Proposed remaining deliverables. Partial means reusable code exists; no task below is certified complete. Dependencies are engineering prerequisites, not a schedule.

## Account truth and execution

Existing integration points: `upgrades.py`, `perception.py`, `autopilot.py`, `device.py`, `runner.py`, `events.py`, `ledger.py`, `web/app.py`.

Proposed focused modules: `account.py`, `transactions.py`, `supervisor.py`, `currencies.py`. These are suggested boundaries, not implemented files.

Game reference: [source](https://www.tower-hub.com/glossary).

### B01 — Identify the exact running build

- [ ] **Partial** — dependencies: none.

Expose backend revision, frontend build, loaded capabilities, game/device identity and active profile; reconcile uncommitted work with the running process.

**Acceptance:** A mismatched frontend/API is visibly blocked from offering unsupported commands; readiness distinguishes watching from playing.

### B02 — Version the game concept catalog

- [ ] **Partial** — dependencies: B01.

Extend upgrades.py into namespaced IDs for stats, unlocks, labs, UWs, cards, modules, perks, currencies and later systems; attach aliases, units, caps, prerequisites, authoritative sources and game-version validity.

**Acceptance:** Every glossary entry maps to a concept/domain or an explicit alias/strategy/reference record; unfamiliar OCR text never silently becomes an executable upgrade.

### B03 — Persist account and run state separately

- [ ] **Missing** — dependencies: B02.

Create immutable account revisions and separate run observations. Store base Workshop levels, lab levels, effective stats, inventory, unlocks and settings with observation time, confidence, frame evidence and unknown values.

**Acceptance:** A battle-buffed Health value cannot overwrite base Workshop Health; missing levels stay unknown; restart restores the latest verified account revision.

### B04 — Discover all supported screens and rows

- [ ] **Partial** — dependencies: B02, B03.

Generalize OCR navigation to menus, nested tabs, scroll lists, popups and account readers; validate screen geometry and locale; distinguish locked, unseen, unavailable, maxed and unreadable.

**Acceptance:** Recorded screens across supported unlock stages resolve stable IDs without fixed row ordering; new tabs do not shift a purchase to a different target.

### B05 — Journal and verify every transaction

- [ ] **Partial** — dependencies: B03, B04.

Introduce durable intent, preconditions, one device action, confirmation and reconciliation. Correlate wallet, level/inventory and UI acknowledgement; separate free upgrades and passive income from purchased effects.

**Acceptance:** A crash after a successful tap does not buy twice; a free upgrade does not falsely confirm a cash purchase; card opening is not logged as bought without evidence.

### B06 — Recover the device and bot lifecycle

- [ ] **Partial** — dependencies: B01, B05.

Use a single device lease and durable supervisor for ADB reconnect, game resume/relaunch, transient failures, bounded retry/backoff and crash recovery; preserve the verified account identity.

**Acceptance:** Inject disconnect, stale frame and process restart; recover the correct device and pending action or enter an explicit bounded blocked state without uncontrolled tapping.

### B07 — Allocate currencies and reserves

- [ ] **Partial** — dependencies: B03, B05.

Separate run cash from permanent coins, gems, stones, medals, cells, keys, shards, tickets, Bits and other versioned resources. Reserve across competing plans and timers, not just per shopping visit.

**Acceptance:** Two queued actions cannot reserve the same funds; lab-slot savings and UW synchronization savings survive lower-priority purchases and process restarts.

### B08 — Build representative replay fixtures

- [ ] **Partial** — dependencies: B02, B04.

Extend OCR fixtures with before/after action sequences and account snapshots for each supported subsystem, ambiguous reads, expensive confirmations, overlays and late-game layouts.

**Acceptance:** Each enabled capability has positive, unavailable and ambiguous-state examples plus a reproducible focused verification command; unsupported capabilities remain disabled.

## Reliable farming loop

Existing integration points: `tower_bot.py`, `autopilot.py`, `policy.py`, `shopping.py`, `navigate.py`, `progression.py`, `runs.py`.

Proposed focused modules: `maintenance.py`, `perks.py`, `run_loadouts.py`. These are suggested boundaries, not implemented files.

Game reference: [source](https://www.tower-hub.com/wiki/guide).

### F01 — Observe combat and run context

- [ ] **Partial** — dependencies: B03, B04.

Read wave, tier, game speed, elapsed time, health/recovery/wall state, enemy damage and modifiers, perks, cash, UW/card state and run purpose as separate observations.

**Acceptance:** The planner detects a changed run/build and expires cached rows; decision-critical missing HUD values prevent the affected action instead of defaulting to zero.

### F02 — Complete battle upgrade execution

- [ ] **Partial** — dependencies: F01, B05, B07.

Finish Attack/Defense/Utility traversal, target semantics, buy mode, reserves and maxed rows; make Turtle/Health policy prerequisites explicit and support revisiting affordable priorities.

**Acceptance:** A recorded multi-tab run buys the intended semantic upgrade once, handles free-upgrade interference, respects cash reserve and reaches its configured target.

### F03 — Complete Workshop progression

- [ ] **Partial** — dependencies: B02, B04, B05, B07.

Model every standard unlock chain and actual levels/costs, purchase batches and decreasing stats. Replace limited early unlock links with verified account prerequisites and stage-aware exclusions.

**Acceptance:** An initially locked upgrade becomes discoverable through its prerequisite chain; no premature Wall/Rend or irrelevant unlock is bought; target spending remains bounded.

### F04 — Route runs through maintenance

- [ ] **Partial** — dependencies: F02, F03, B06.

Choose retry versus home after death based on due work; arbitrate battle, Workshop, research, rewards and return navigation with a durable maintenance queue.

**Acceptance:** Complete run → game over → home → due maintenance → next run without a manual tap; repeated RETRY cannot starve account upgrades.

### F05 — Choose tiers and run objectives

- [ ] **Partial** — dependencies: F01, F04, F09.

Execute tier selection/unlocks and farm versus milestone objectives. Compare coins/hour and cells/hour including downtime; explore enough eligible tiers and record confidence/build context.

**Acceptance:** Select and visually confirm the requested tier; do not infer a best tier from Tier 1 alone or mix short milestone attempts with ordinary farming samples.

### F06 — Choose and configure perks

- [ ] **Missing** — dependencies: B04, B05, F01.

Read offered normal/UW/tradeoff perks, current stacks and perk settings; implement build/purpose-specific picks, bans, priorities and unlocks for automatic perk facilities.

**Acceptance:** Handle a blocking perk choice and resume the run; reject a harmful tradeoff under the active build and record why the chosen perk was preferred.

### F07 — Manage run loadouts and active skills

- [ ] **Missing** — dependencies: F01, C01, C02.

Select farming, milestone, event and tournament card sets; track swap restrictions and active-card cooldown/usage; coordinate UW and targeting settings when their executors become available.

**Acceptance:** Loadout is confirmed before the run; locked in-run cards are not swapped; active abilities fire at configured conditions and restore the farming preset afterward.

### F08 — Handle rewards and blocking overlays

- [ ] **Missing** — dependencies: B04, B05, B06.

Classify daily login, gems, reward claims, result screens, known ad flows, disconnect notices and known dismissible popups. Track reward availability, completion and timeout.

**Acceptance:** Known interruptions resume the prior objective with verified rewards; an unrecognized overlay triggers bounded recovery instead of blind clicks or endless scanning.

### F09 — Measure outcomes with build context

- [ ] **Partial** — dependencies: B03, B05, F01.

Extend run history with verified tier/wave, coins/cells/shards, real elapsed time including visits, death reason, active build and optimizer/account revision; retain missing-data flags.

**Acceptance:** Rates exclude invalid/zero-duration data; two build variants can be compared using reproducible eligible samples rather than counting taps as successful upgrades.

## Labs and acceleration

Existing integration points: `shopping.py`, `strategy.py`, `ledger.py`.

Proposed focused modules: `labs.py`, `research_policy.py`. These are suggested boundaries, not implemented files.

Game reference: [source](https://www.tower-hub.com/wiki/lab/laboratory).

### L01 — Read labs, slots and research timers

- [ ] **Missing** — dependencies: B03, B04.

Read lab ownership, unlocked research, exact levels/costs/durations, active jobs, queues, speeds and acceleration state; model unlock milestones.

**Acceptance:** All owned lab slots and active completions survive a restart; an unseen lab is not assumed unresearched.

### L02 — Run continuous research

- [ ] **Missing** — dependencies: L01, B05, B07, F04.

Unlock slots under the gem plan and execute start/queue/repeat/replace research with explicit prerequisites, completion confirmation and policy for interrupted jobs.

**Acceptance:** A lab finishing during a battle is replenished at the next safe opportunity; coins remain reserved and the same research is not started twice.

### L03 — Schedule lab acceleration

- [ ] **Missing** — dependencies: L02, B07, F09.

Allocate cells to supported speed-up tiers/durations and optional gem rushing under explicit saved limits; model real completion time, queue occupancy and next boost expiry.

**Acceptance:** Boosts stay within projected cell income/reserve, expired boosts are renewed when eligible, and unavailable acceleration is skipped without blocking research.

### L04 — Balance essential and optimizer research

- [ ] **Missing** — dependencies: L02, E04, E05.

Keep essential progression/QoL research and account prerequisites alongside eHP/eRegen/eDamage/eEcon recommendations; choose independent slot jobs by goal, time and currency constraints.

**Acceptance:** All slots receive eligible work; a high-ranked optional stat lab cannot displace a required unlock or lab-speed policy merely because it appears in a path.

## Cards and modules

Existing integration points: `shopping.py`, `strategy.py`.

Proposed focused modules: `cards.py`, `modules.py`, `loadouts.py`. These are suggested boundaries, not implemented files.

Game reference: [source](https://www.tower-hub.com/wiki/module).

### C01 — Read cards, slots and mastery

- [ ] **Missing** — dependencies: B03, B04.

Inventory card identities, copies, levels, unlocked/equipped slots, presets, in-run locks and mastery ownership; distinguish passive bonuses from active abilities.

**Acceptance:** Exact equipped cards and available slots are known before calculating effective stats; missing card observations remain explicit.

### C02 — Verify card purchases and equipment

- [ ] **Partial** — dependencies: C01, B05, B07, F04.

Confirm card pack results and gem deductions, open slots, equip/save presets and enforce the lab-slot-first resource plan; integrate event card-purchase goals.

**Acceptance:** A purchased pack reconciles copies and gems; a lab-slot reserve is preserved; each preset is read back after applying it.

### C03 — Read primary and assist modules

- [ ] **Missing** — dependencies: B03, B04.

Inventory Cannon/Armor/Generator/Core and assist modules, equipped slots, levels, rarity, stars, unique effects, subeffects, shards and restrictions.

**Acceptance:** Account state distinguishes unique-effect rarity scaling, main stats and primary/assist effects; every equipped module resolves to the correct category.

### C04 — Upgrade, merge and recycle modules

- [ ] **Missing** — dependencies: C03, B05, B07, F04.

Implement level-up, restoration, merge/unmerge, shatter and acquisition policies with protected copies, rarity prerequisites and shard accounting.

**Acceptance:** A proposed merge preserves required/protected inventory; post-action rarity and resource deltas reconcile; uncertain actions are not repeated.

### C05 — Optimize module loadouts and subeffects

- [ ] **Missing** — dependencies: C03, C04, B07, E05.

Rank equip/assist combinations per build, reroll with desired effects and locked slots, enforce shard budgets and exact stop conditions; preserve existing useful subeffects.

**Acceptance:** Reroll stops on desired outcomes or budget exhaustion; replacing a module invalidates affected optimizer inputs before another recommendation executes.

### C06 — Plan card mastery investments

- [ ] **Missing** — dependencies: C01, C02, L02, E05, U02.

Model mastery unlock conditions, stone unlocks, coin/lab follow-ups and active/persistent effects; compare expensive mastery paths with competing UWs and research.

**Acceptance:** A mastery plan checks its full unlock and follow-up costs, updates effective stats, and never spends the stone reserve twice.

## Effective Paths loop

Existing integration points: `advisor.py`, `web/advisor.py`, `web/ui/components/AdvisorPanel.tsx`.

Proposed focused modules: `effective_paths/contract.py`, `effective_paths/inputs.py`, `effective_paths/connector.py`, `planner.py`. These are suggested boundaries, not implemented files.

Game reference: [source](https://the-tower.notion.site/Effective-Paths-1bb91383b93f80d5aed2c098cbbd9e46).

### E01 — Pin the native workbook contract

- [ ] **Partial** — dependencies: B02.

Version Effective Paths, IDS worksheets, actual input/output ranges, currencies, path families and formula dependencies; identify stale guide references and unsupported paths.

**Acceptance:** A known workbook revision produces a machine-readable contract; a changed or unknown workbook layout is detected before any recommendation can execute.

### E02 — Populate calculator inputs from account state

- [ ] **Missing** — dependencies: E01, B03, L01, C01, C03, U01, F09.

Map readers to IDS and direct EP inputs, preserving base levels versus run-max levels, raw UWs versus lab/subeffect modifiers, loadout, perks, modules, economy outcomes and estimated assumptions. Add Guardian/Vault/Dissonance inputs as those readers become available; each path declares its required capabilities.

**Acceptance:** Every required field has provenance or an explicit assumption; public template/example values cannot be mistaken for this account; stale critical fields block only dependent paths.

### E03 — Recalculate a versioned personal model

- [ ] **Missing** — dependencies: E01, E02.

Implement a connector to an authorized personal workbook or a compatible validated local calculation engine. Detect formula errors, external IDS dependencies, calculation completion and output freshness.

**Acceptance:** A calculation result names both workbook and account revision, matches golden cases, and is rejected if inputs changed or dependent formulas are still stale.

### E04 — Normalize all native recommendation types

- [ ] **Partial** — dependencies: E03, B02.

Extend the manual advisor import to native eHP/eRegen/eDamage/eEcon variants, coin/stone/key and discount outputs with levels, units, gain, cost, duration, uncertainty and executor capability.

**Acceptance:** Each result is executable, blocked for a named prerequisite, or advisory with a reason; standard Workshop planning is not falsely presented as a native EP path.

### E05 — Choose goals and resolve prerequisites

- [ ] **Missing** — dependencies: E04, B07.

Allocate a portfolio across incomparable health, damage, regen and economy goals using saved preferences and build constraints; schedule prerequisite chains and savings; retain essential non-EP priorities.

**Acceptance:** The selected next action is explained with opportunity cost and dependencies; goals in different units are not ranked by a raw benefit number; hard exclusions override optional rankings.

### E06 — Check proposals before execution

- [ ] **Missing** — dependencies: E05, B05, B08.

Run feasibility and affordability checks, simulate the planned stat/resource change, assert sync and build invariants, and compare against workbook fixture outputs in shadow mode.

**Acceptance:** Invalid levels, wrong units, unaffordable bundles, stale evidence and broken synchronization are rejected in replay without affecting the device.

### E07 — Close the observe–optimize–act loop

- [ ] **Missing** — dependencies: E06, F04, L04.

Promote eligible plans under saved authority, dispatch to available domain executors, verify the change, refresh inputs and replan; continue safe farming when an optional path is unsupported.

**Acceptance:** One supported native recommendation completes from account observation through confirmed in-game change and a new calculation without import, stage or Save clicks.

## Ultimate Weapons and event bots

Existing integration points: `policy.py`, `strategy.py`.

Proposed focused modules: `ultimate_weapons.py`, `synchronization.py`, `event_bots.py`. These are suggested boundaries, not implemented files.

Game reference: [source](https://www.tower-hub.com/wiki/uw).

### U01 — Read Ultimate Weapons and UW+

- [ ] **Missing** — dependencies: B03, B04.

Read owned UWs, offered unlock choices, raw levels/values/costs, labs, toggles, cooldowns/durations, plus-system state and account prerequisites.

**Acceptance:** All nine base UW identities and current observed ownership are represented; lab/subeffect-adjusted values never masquerade as raw stone upgrades.

### U02 — Choose and buy stone upgrades

- [ ] **Missing** — dependencies: U01, B05, B07, E05.

Evaluate offered UWs and stone-level paths against the account build, save-up goals and prerequisite bundles; perform verified selection and upgrade actions.

**Acceptance:** A stone action preserves reserves and intended synchronization and confirms the actual weapon/level; unavailable offers trigger replanning.

### U03 — Maintain UW and bot synchronization

- [ ] **Missing** — dependencies: U01, U02, F01.

Model base cooldowns, durations, overlap, partial versus full sync, UW timing toggles and module/perk modifiers; preserve account-specific GT/BH/DW/Golden Bot constraints.

**Acceptance:** A proposed cooldown change cannot silently break a protected overlap; actual observed cycles and model assumptions are reported separately.

### U04 — Execute UW+ and advanced combinations

- [ ] **Missing** — dependencies: U02, U03, L02, C05.

Add plus unlocks, advanced UW labs and activation policies for late-game damage/economy builds using version-specific formulas and observation.

**Acceptance:** All enabled UW+ upgrades have prerequisites, cost and verified postconditions; advanced build presets apply consistently across labs/modules/perks.

### U05 — Manage event bots and Bot+

- [ ] **Missing** — dependencies: B03, B04, B05, B07, U03, T02.

Model each current event bot, unlock/medal levels, cooldown/range/duration/bonus, labs and toggles; cover Bot Bot and Bot+ when eligible.

**Acceptance:** A bot upgrade reconciles medals and levels while preserving protected sync and event purchases; a glossary count cannot hide a current bot.

## Advanced builds and systems

Existing integration points: `upgrades.py`, `policy.py`, `strategy.py`.

Proposed focused modules: `enhancements.py`, `builds.py`, `vault.py`, `guardian.py`. These are suggested boundaries, not implemented files.

Game reference: [source](https://www.tower-hub.com/wiki/system).

### A01 — Buy Workshop Enhancements

- [ ] **Missing** — dependencies: F03, B07, E05.

Read enhancement unlocks, levels, coin costs and category progression; execute EP enhancement recommendations independently of ordinary Workshop stats.

**Acceptance:** An enhancement purchase updates the matching calculator multiplier once and confirms any newly unlocked enhancement options.

### A02 — Unlock and develop the Wall deliberately

- [ ] **Partial** — dependencies: F03, L04, A01, E05.

Treat existing Wall stat catalog entries as only a starting point; add readiness thresholds, Wall lab commitments, regen/fortification/thorns and health interactions.

**Acceptance:** Wall is unlocked only after the configured full readiness predicate; required research and budget follow through instead of leaving a harmful half-built state.

### A03 — Manage build transitions and respecs

- [ ] **Missing** — dependencies: F02, F03, F06, F07, C05, U03, E05.

Model Turtle, Blender/eHP, hybrid, Devo/orb-devo/orbless and glass-cannon eligibility with account-specific goals; plan reversible loadout changes and controlled Workshop respecs.

**Acceptance:** A chosen transition has a verified precondition checklist and ordered restore plan; free-upgrade, range and sync settings match the intended build before farming resumes.

### A04 — Control targeting, range and positioning

- [ ] **Missing** — dependencies: B04, B05, F01, F06, A03.

Execute target priority, orb adjustments, range/shockwave sliders, UW toggles and other supported in-run controls; account for enemies, elites and battle conditions.

**Acceptance:** Controls are applied and read back for the current mode; locked or unsupported controls are skipped with an explicit capability reason.

### A05 — Manage Vault trees and keys

- [ ] **Missing** — dependencies: B03, B04, B05, B07, E05, T03.

Read the current tree catalog, unlock prerequisites and key economy; implement tree purchases/respec policy and integrate the native EP key path where supported.

**Acceptance:** A tree node purchase confirms prerequisite, key cost and resulting bonus; old two-tree documentation cannot truncate the current catalog.

### A06 — Manage Guardian chips and materials

- [ ] **Missing** — dependencies: B03, B04, B05, B07, E05.

Read Guardian unlocks and current chip catalog including Scout, levels, cooldowns, materials and effects; support IDS input mapping, purchases and run usage.

**Acceptance:** An eligible chip action reconciles materials and updates affected economy/combat inputs; unobserved chips remain unknown rather than absent.

## Events and long-term progression

Existing integration points: `pages.py`, `runs.py`, `progression.py`.

Proposed focused modules: `missions.py`, `tournaments.py`, `rewards.py`, `guilds.py`, `dissonance.py`. These are suggested boundaries, not implemented files.

Game reference: [source](https://www.tower-hub.com/wiki/system).

### T01 — Complete daily and weekly objectives

- [ ] **Missing** — dependencies: B03, B04, B05, F04, F08.

Read reset timers, objective progress and reward tracks; claim free rewards and schedule compatible actions with deduplication and missed-reset handling.

**Acceptance:** Across a reset, completed rewards are claimed once and unfinished goals are carried or expired correctly without interrupting a more valuable run unnecessarily.

### T02 — Plan events and event-shop spending

- [ ] **Missing** — dependencies: T01, B07, E05.

Read timed missions, event progress, shop inventory and prices; choose attainable goals and medal purchases, accounting for relic/theme priorities and rising purchase costs.

**Acceptance:** Reserve event currency for the selected targets, execute compatible mission runs, claim milestones and stop at the configured completion/budget limit.

### T03 — Participate in tournaments

- [ ] **Missing** — dependencies: F05, F07, F09, T01, E05.

Read tournament schedule/league/tickets/rewards and battle conditions; choose a competitive loadout and time window, enter, run, claim, and restore farming.

**Acceptance:** One tournament cycle respects tickets, applies the correct conditions, records result and returns to farming; tournament runs do not distort farming comparisons.

### T04 — Track milestones, relics and cosmetics

- [ ] **Missing** — dependencies: F05, T01, T02, B03, B05.

Read and claim tier milestones and unlocks; inventory relics, skins/backgrounds/songs and effective bonuses; model eligible free/owned pass rewards separately.

**Acceptance:** Claimed rewards and passive bonuses reconcile once; future model calculations include owned bonuses even when a cosmetic is not equipped.

### T05 — Support guild and seasonal systems

- [ ] **Missing** — dependencies: T01, T02, F07, B07.

Version guild objectives, co-op/dungeon activities, seasonal modifiers, tokens and Bits; add readers, attainable-goal policy and actions for the supported account/version.

**Acceptance:** A supported activity has known schedule, resource and reward semantics; social/account setup dependencies are recorded and do not block ordinary solo farming.

### T06 — Automate Dissonance and Echo progression

- [ ] **Missing** — dependencies: A03, A04, U04, A05, A06, T04.

Read Dissonance unlocks/run constraints, Echo and build-specific requirements; plan specialized attempts, respec/loadout restoration and permanent reward effects.

**Acceptance:** Complete a supported Dissonant objective with verified setup, reward and return to farming; no late-game mode is marked supported from its name alone.

## Control room

Existing integration points: `web/app.py`, `web/ui/components/Sidebar.tsx`, `web/ui/app/strategy/page.tsx`, `web/ui/components/AutopilotStatus.tsx`.

Proposed focused modules: `web/ui/app/account/page.tsx`, `web/ui/app/progression/page.tsx`. These are suggested boundaries, not implemented files.

### O01 — Show a complete account inspector

- [ ] **Partial** — dependencies: B03.

Replace dated account literals with observed account inventory, field freshness, source frame, unlock dependencies, supported capability and missing-input views.

**Acceptance:** The user can trace every optimizer-critical value to an observation or declared estimate and distinguish unavailable from not yet scanned.

### O02 — Configure goals and standing authority

- [ ] **Partial** — dependencies: B07, E05.

Provide saved farming/progression priorities, reserves, forbidden upgrades, sync constraints, allowed currencies and mode schedules; make no-routine-approval mode explicit.

**Acceptance:** A saved policy authorizes supported routine actions without repeated confirmations while unsupported/forbidden actions cannot be armed accidentally.

### O03 — Explain each plan and purchase

- [ ] **Partial** — dependencies: E06, B05.

Extend live decisions into a dependency view: current goal, next action, alternatives, blockers, cost, source version and expected/observed result.

**Acceptance:** A displayed decision can be replayed from its account/policy/workbook revisions, including why a cheaper-looking alternative was not chosen.

### O04 — Expose schedules and progression results

- [ ] **Partial** — dependencies: F09, L03, T01.

Show research queue, currency commitments, event/tournament deadlines, next maintenance visit, progression metrics and goal completion; keep automation schedule independent of the dashboard tab.

**Acceptance:** Closing the browser does not stop scheduling; measured results link to the exact build and decision history.

### O05 — Provide incident and recovery controls

- [ ] **Partial** — dependencies: B06, F08, O03.

Show recovery attempts, current blocking evidence, degraded capabilities and safe resume/stop controls; retain machine-readable incident bundles.

**Acceptance:** A stuck optional subsystem can be quarantined while safe core farming continues; unresolved global state stops spending and gives one actionable incident.

## Unattended qualification

Existing integration points: `tests/test_autopilot.py`, `tests/test_shopping_autopilot.py`, `tests/test_advisor.py`, `tests/test_runner.py`.

Proposed focused modules: `tests/fixtures/account/`, `tests/fixtures/effective_paths/`, `tests/test_recovery.py`. These are suggested boundaries, not implemented files.

### V01 — Prove subsystem coverage in replay

- [ ] **Partial** — dependencies: B08, B05.

Maintain a scenario matrix per enabled reader/executor, covering unlocks, max levels, missing inputs, currencies, ambiguous outcomes and version changes.

**Acceptance:** All enabled actions pass their relevant focused fixture/API checks; catalogs, schemas and unexecuted tests do not count as live support.

### V02 — Prove calculator and decision parity

- [ ] **Missing** — dependencies: E06, V01.

Run representative account fixtures through native EP and the connector; verify ranges, units, output order and tolerances; shadow proposed decisions against actual account observations.

**Acceptance:** Every enabled native path has version-pinned golden cases; any mismatch disables affected execution until resolved.

### V03 — Prove recovery under faults

- [ ] **Missing** — dependencies: B06, F04, F08, V01.

Inject process loss before/after taps, ADB loss, unknown overlays, missing UI replies, stale calculator output, full queues and resource races.

**Acceptance:** Every scenario reaches an explicit recover/skip/blocked outcome within its configured bound; no duplicate spending or silent data corruption occurs.

### V04 — Certify overnight farming

- [ ] **Missing** — dependencies: F04, F05, F06, F07, F08, L02, C02, V03, O05.

Run a controlled 24-hour acceptance session with available early-game systems, a fixed policy and recorded baseline; inspect intervention and action outcomes.

**Acceptance:** Zero routine manual taps; every purchase reconciled; no indefinitely blocked known screen; all due supported maintenance serviced; resource reserves preserved.

### V05 — Certify optimizer-led weekly operation

- [ ] **Missing** — dependencies: E07, L03, C05, C06, U04, U05, A01, A02, A03, A04, A05, A06, T01, T02, T03, T04, T05, T06, O02, O04, V02, V04.

Qualify supported late-game/account capabilities incrementally, then run a seven-day soak spanning resets, research turnover and available scheduled activities.

**Acceptance:** Zero routine intervention across the enabled scope; all covered recommendation types execute and recalculate correctly; unavailable game modes have explicit coverage exclusions.

### V06 — Maintain version compatibility

- [ ] **Missing** — dependencies: B01, B02, E01, V01, O05.

Detect game, wiki, glossary and workbook changes; diff concepts and fixtures; quarantine incompatible paths and roll out updated adapters after focused qualification.

**Acceptance:** A changed label, new system or workbook schema cannot silently reroute spending; unaffected verified farming continues when safe.
