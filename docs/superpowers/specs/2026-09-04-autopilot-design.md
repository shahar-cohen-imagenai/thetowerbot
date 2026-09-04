# OCR autopilot and control room

Approved direction: implement the recommendation reviewed in conversation.

## Outcome

Expose the full standard Attack, Defense and Utility upgrade catalog, discover
account availability and values with OCR, navigate and purchase in battle and in
the Workshop, and explain guide-based decisions in the web control room.

The catalog contains semantic identities and aliases, not prices or coordinates.
An unseen upgrade is unknown, never inferred locked. Explicit locks, maxed values,
unreadable values and insufficient currency are separate states. Observations carry
timestamps and context; battle values expire at run boundaries. A purchase uses a
fresh frame and must be confirmed from a changed price/value or an unlock transition.

## Contracts

- `upgrades.py`: frozen `Upgrade(id, name, category, aliases, unlock)` records;
  `CATALOG`, `by_id(id)`, `resolve(name, category=None)`, `catalog_payload()`.
  Categories are `ATTACK`, `DEFENSE`, `UTILITY`; IDs are snake_case names.
- `policy.py`: frozen `UpgradeRule(upgrade_id, enabled=True, target=None)` and
  `AutopilotPolicy(enabled=False, preset='manual', rules=(), economy_until_wave=20,
  survival_buffer=1.2, cash_reserve=0, max_scrolls=8)` with strict JSON validation.
  Presets: `manual`, `turtle`, `health`. `preset_rules(name)` returns rules.
  `choose(policy, observations, combat)` returns `Decision(phase, reason, upgrade_id)`.
  Observation dictionaries contain `value`, `price`, `status`, `observed_at`.
  Combat contains optional `wave`, `tier`, `enemy_damage`, `health`, `max_health`.
  Unknown critical combat values cause guide policies to wait rather than guess.
- `Strategy.autopilot` defaults disabled, round-trips through existing profile APIs.
- `Shopping` adds `coin_reserve=0`, `coin_budget=0` (zero means no armed Workshop
  spending until a budget is supplied), `allow_unlocks=False`. ShoppingRule adds
  optional `target`. Existing shopping rows remain editable and OCR-addressed.
- `perception.py`: typed tile and combat observations and category recognition.
- `autopilot.py`: bounded navigation/purchase state machine, one device operation
  per fresh frame, acknowledgement before subsequent buying, shared observation
  store with read-only snapshots for the API. Only acts on confirmed current screen.
- `GET /api/upgrades` returns catalog entries. `GET /api/autopilot` returns
  `{phase, reason, next_upgrade_id, category, observations, combat, updated_at,
  verified_purchases, last_purchase, tier_comparison}`. Observations are a list of
  `{upgrade_id, context, category, name, value, price, status, observed_at}`.
- `GET /api/autopilot/presets` returns preset names and rule lists. UI applies
  presets to its draft; saving follows the existing Save/Revert flow.

## Experience

Strategy contains a battle/Workshop selector, category tabs, search and active plan.
Rows show observed value, price, availability, target and configured priority.
Locked and unseen rows can be planned but cannot be blindly purchased. Workshop
controls expose coin reserve, visit budget and explicit unlock permission. OCR
details live in diagnostics. Live view shows current phase, decision, freshness,
next action and verified purchases, with existing pause/stop controls retained.

## Guide behavior

Turtle prioritizes early economy, interrupts for an inadequate Defense Absolute
buffer after Defense %, advances Thorns breakpoints, then supports health. Health
prioritizes economy while healthy and health/Defense %, lifesteal, attack speed,
knockback and orbs for survival. Rules remain editable and targets cap buying.
Use actual run duration and coins for tier comparisons, separate milestone intent
from farming, and never promote a tier based on one incomplete run.

Sources: [Turtle](https://the-tower.notion.site/Turtle-Strategy-early-game-1bc91383b93f809c81d0e2825cfeac07),
[progression](https://the-tower.notion.site/Guide-on-Tier-Progression-23f91383b93f8024b142c9163954e218),
[beginner](https://www.tower-hub.com/wiki/guide/beginner-guide).

## Constraints

Keep profiles backwards compatible and preserve disabled/armed controls. Do not
start or operate the user's live bot during development. No automatic commits.
Only specific changed or affected test files run locally, always with
`-p no:allure_pytest`. Later cards, labs and perks are separate follow-up systems;
this implementation does not claim to automate them.
