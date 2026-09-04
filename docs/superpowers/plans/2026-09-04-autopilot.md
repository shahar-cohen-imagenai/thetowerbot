# OCR Autopilot Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development task by task.

**Goal:** Deliver OCR purchases, editable guide strategies and a control room with
verified outcomes and budgeted permanent progression.

**Architecture:** Share semantic catalog and observations across battle/Workshop.
Keep decision policy pure and device navigation serialized in the bot loop.
Expose observations through read-only APIs and reuse profile Save/Revert controls.

**Tech Stack:** Existing Python/FastAPI, RapidOCR/OpenCV, Next.js/React.

**Spec:** `docs/superpowers/specs/2026-09-04-autopilot-design.md`

## Global constraints

No live purchases, automatic commits or broad test sweeps. Preserve existing
profiles and shopping arming. Unknown observations never authorize spending.
Work on `codex/ocr-autopilot`; leave the existing live process untouched.

## Task 1: Catalog and policy

Files: upgrades.py, policy.py, strategy.py, tests/test_autopilot_policy.py.
Interfaces: the catalog/policy/Strategy contracts in the spec.
- [x] Write tests for alias resolution, invalid rules, serialization, survival
  overrides and target completion. Example: with enemy_damage=100, Defense %=50,
  Defense Absolute=20, choose Defense Absolute over Cash Bonus in Turtle mode.
- [x] Run `.venv/bin/python -m pytest tests/test_autopilot_policy.py -q -p no:allure_pytest`.
- [x] Implement the catalog, strict policy parsing, presets and profile integration.
- [x] Verify new tests plus tests/test_strategy.py and tests/test_strategy_shopping.py.

## Task 2: Perception and executor

Files: perception.py, autopilot.py, device.py, tower_bot.py, shopping.py,
tests/test_autopilot.py, tests/test_perception.py, tests/test_shopping.py.
Consumes catalog and policy; produces observations and verified decisions.
- [x] Add recorded-frame tests for stat/price separation, unavailable rows and
  category inference. Test fresh-frame navigation and no repeated tap while pending.
- [x] Run those specific tests and observe expected missing behavior.
- [x] Implement OCR parsing, bounded tab/scroll navigation, purchase acknowledgement,
  budgets and target limits, and integrate with the bot loop.
- [x] Verify using recorded frames and targeted loop/shopping regression files.

## Task 3: API and control room

Files: web/app.py, runner.py, web/ui/components/AutopilotEditor.tsx,
web/ui/components/AutopilotStatus.tsx, web/ui/lib/types.ts, web/ui/lib/api.ts,
web/ui/app/strategy/page.tsx, web/ui/app/page.tsx; focused API/component tests.
Consumes shared read-only state and exact spec payloads.
- [x] Test catalog visibility, malformed policies, category/search filters, preset
  draft edits and disabled/unknown states without activating spending.
- [x] Implement APIs, categorized purchase editor, Workshop budget controls and
  Live decision panel. Preserve existing Save/Revert and compatibility controls.
- [x] Run only changed component/API tests and production frontend build.

## Task 4: Integration and review

Files: README.md and focused regression tests as issues are discovered.
- [x] Check disabled policies retain legacy behavior, game-over/unknown screens
  cannot purchase, and manual changes invalidate pending navigation safely.
- [x] Add tier comparisons using complete runs and actual elapsed time, with
  insufficient data shown explicitly and milestone intent separate from farming.
- [x] Inspect the built app against an isolated idle server and fixture data.
- [x] Review the diff, fix substantive findings, document capabilities and limits.
- [x] Report changed behavior, evidence, and remaining live-device validation.

## Progress

Status: All four implementation tasks complete. No planned coding tasks remain.
The updated idle preview runs at http://127.0.0.1:8766/strategy/. Changes are
uncommitted on codex/ocr-autopilot; the original live process was left untouched.

Delivered: full standard catalog; independent Cash/Coin unlock identities; editable
Manual/Turtle/Health policies; OCR stat/price/currency reads; bounded battle and
Workshop navigation; acknowledged purchases; reserve/budget controls; category
purchase UI; immediate manual commands; persisted farming/milestone run intent.

Review fixes: preserved pending evidence across pause/policy edits, bounded cached
row searches, prioritized survival over discovery, serialized speed and purchase
operations, reserved due Workshop visits before battle navigation, and shared
increasing/decreasing target semantics.

Validation is recorded-frame, fake-device and isolated-web testing. No live game
purchases were made. Live calibration for later unlocked panels remains outstanding.


Ruling: execute the already-approved design without another approval checkpoint.
Ruling: retain a feature branch in this checkout; do not disturb the existing
running process or the user's untracked worktrees.

### Final verification

- 239 passed: policy, Strategy, Shopping compatibility and Workshop automation.
- 31 passed: perception, battle executor and autopilot API.
- 61 passed: API, battle gating, speed/shopping loop integration and progression.
- 15 passed: runner lifecycle after observation-reset change.
- 42 passed: affected page, purchase editor, status and polling components.
- 59 passed: final event formatting and purchase editor checks.
- Additive purpose migration/store checks passed in the focused database tests.
- Final production frontend build, Python compilation and diff checks passed.
- Idle preview verified to serve 52 catalog entries, including 48 standard upgrades.

Counts above overlap; they are separate targeted runs, not a unique test total.
Outstanding validation: use the game device to calibrate later unlocked panels and
exercise a supervised run. No live purchases, automatic tier switching, labs or
perks are claimed by this implementation. Observations remain process-local.
