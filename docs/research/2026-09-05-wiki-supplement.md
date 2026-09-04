# Wiki prose supplement: Workshop, Cards, Currency, Enemies and Modules

Reviewed on 2026-09-05 from the parent task's downloaded corpus at `docs/research/wiki-source-2026-09-05/`. This supplement covers **114 complete article prose files**: 48 Workshop, 35 Card, 11 Currency, 14 Enemy, 6 Module, totaling 283,423 characters. Each listed file was printed and read in full in bounded batches. Top-level category landing duplicates (`wiki__workshop.prose.txt`, etc.) and historical patch-note articles were excluded. Some substantive overview articles contain repeated sections; those article bodies were still read. Numerical tables were separately extracted by the parent task and are not claimed as fully manually reviewed here.

This is a conceptual review, not numerical verification against the running game. The wiki contains contradictions, outdated sentences and mislabeled sections. Its mechanics need a versioned validation layer before they authorize spending.

## Essential additions to the account and action model

| Domain | Distinctions the bot needs | Missing operational work |
|---|---|---|
| Workshop | Permanent purchased level; temporary cash level; free-upgrade level; base stat; derived stat; contextual caps; unlock-chain state; quantity selector; discounts. | Extend beyond the 11 early unlock tiles; read level and currently selected buy multiplier; calculate the exact bundle cost; distinguish purchased from free stat changes; constrain targets by the intended build. |
| Lab-controlled tuning | Highest researched level differs from currently selected level. Range and Shockwave have outside-run adjusters. | Read and persist researched/selected levels separately; apply a desired setup only in an allowed game state; confirm that the selected value matches the plan. |
| Damage/economy | Displayed damage/CPK/attack speed is not the complete effective damage/reward formula. Some bonuses are additive, some multiplicative, some on-hit or conditional. | Preserve unrounded inputs and source identities; distinguish projectile/UW/mine/rend/super-tower components; validate calculator outputs against displayed and effective values independently. |
| Survivability | Current tower health, base maximum, package overheal cap, Wall HP, Wall fortification, regeneration, invulnerability and revive charges are separate state. | Replace a single health ratio with layered survivability; model vampire drain/regen suppression, enemy heat-up, shield charges, Wall rebuild/regeneration, and resistance-specific thorns. |
| Time | Wall-clock duration, accelerated game-time duration and wave-based recharge are different clocks. | Timers must declare their clock. Wave Skip and Wave Accelerator cannot be treated as equivalent to increasing Game Speed. Persist cooldown progress and schedule active cards accordingly. |
| Free upgrades / ELS | Free-upgrade rolls across categories, >100% rollover, locked-out stats, and accumulated attack/health enemy-level skips are independent of purchase actions. | Reconcile automatic progression; model skip counters and eligible pools, prevent free-upgrade side effects from masquerading as purchase confirmation. |
| Enhancements | Three independent category-spend gates, enhancement level/multiplier, per-category discounts, and effects that modify multiple base stats. | Track cumulative spending by category and prerequisites; calculate marginal benefit across levels without assuming a one-row/one-stat mapping. |
| Cards | Ownership, copies/stars, rarity, maxed/drop-pool status, milestone eligibility, gem/key slot unlocks, active preset, equipped cards, run locks, mastery ownership/level. | Observe purchase results; optimize collection/slots versus Labs/modules; preserve presets; apply equipment changes with boss/fleet lock awareness; handle auto-completed card-buy missions. |
| Active cards | Equipped state differs from ready/used/recharging state; native smart automation may already own activation. | Model Demon Mode/Nuke/Second Wind/Shield readiness and recharge, avoid duplicate activation, use game-native automation where available, confirm the death-prevention chain. |
| Modules | Module type, original rarity/provenance, current rarity/stars, level/cap, main/sub/unique effects, locks/bans, shards, equipped primary/assist roles. | Read inventory and selected materials; validate exact merge recipes; protect unique modules and desired substats; budget pulls/rerolls; restore/reallocate levels; handle banners/pity and assist efficiency. |
| Currency | Cash run scope versus durable balances; coins versus gems/stones; module shards by type versus reroll shards; cells/medals/keys/bits/tokens/tickets. | Separate source/sink ledger entries, currency reservations and exchange offers; track event-earned medal progress separately from spendable medal balance and season stock. |
| Enemies | Normal/Boss/Elite/Fleet type; immunity/resistance; spawn cap/escort exception; age/mass/heat-up; type-specific labs; rewards and special attacks. | Add threat-specific observations and policies, farming objectives beyond coins/hour, encounter fixtures, and lab-aware enemy-stat models instead of fixed ratios. |

Sources: [Workshop upgrades](https://www.tower-hub.com/wiki/workshop/workshop-upgrades), [Buy multiplier](https://www.tower-hub.com/wiki/workshop/buy-multiplier), [Range](https://www.tower-hub.com/wiki/workshop/range), [Cards](https://www.tower-hub.com/wiki/card/cards), [Modules](https://www.tower-hub.com/wiki/module/modules), [Enemies](https://www.tower-hub.com/wiki/enemy/enemies).

## Concepts that need explicit roadmap nodes or acceptance scenarios

1. **Full Workshop unlock chain.** Add Bounce Shot → Super Critical → Rend Armor; Orbs → Shockwave → Land Mines → Death Defy → Wall; Free Upgrades → Interest → Recovery Packages → EALS/EHLS. An available late-game stat name in the current catalog is not an implemented unlock action. Include Buy Multiplier, Respec and discount dependencies as distinct capabilities. [Attack](https://www.tower-hub.com/wiki/workshop/attack-upgrades), [Wall](https://www.tower-hub.com/wiki/workshop/wall), [ELS](https://www.tower-hub.com/wiki/workshop/enemy-level-skip).
2. **Orb/range tuning.** Workshop and Extra Orbs differ in source, direction, speed and adjustable radius; range/card/lab changes alter geometry. The Extra Orb Adjuster is disabled while a boss is present. Validate the supported build's geometry rather than blindly maximizing Range or Shockwave. [Orbs](https://www.tower-hub.com/wiki/workshop/orbs), [Shockwave](https://www.tower-hub.com/wiki/workshop/shockwave).
3. **Layered recovery.** Packages heal automatically; no package-tapping executor is needed. The planner does need Package After Boss, recovery amount/max/chance, overheal, Wall Regen/Fortification, and vampire-specific sustain behavior. [Packages](https://www.tower-hub.com/wiki/workshop/recovery-packages), [Vampire labs](https://www.tower-hub.com/wiki/enemy/vampire-labs).
4. **Native automation ownership.** Death prevention is ordered: Death Defy → Energy Shield → Second Wind → smart Demon Mode → smart Nuke. Timed recharge versus wave recharge must be distinguished; Smart Nuke has a post-trigger invulnerability effect and does not erase existing projectiles. The exact versioned mechanics require fixtures. [Demon Mode](https://www.tower-hub.com/wiki/card/demon-mode), [Energy Shield](https://www.tower-hub.com/wiki/card/energy-shield), [Nuke](https://www.tower-hub.com/wiki/card/nuke).
5. **Card state transitions.** Mid-run preset swaps require matching locked cards; boss/fleet presence locks all cards; swapping can mutate the active preset. Mastery unlocks require progression and maxed-card prerequisites, then stones and separate Lab investment; their effect depends on equipment. [Card presets](https://www.tower-hub.com/wiki/card/card-presets), [Masteries](https://www.tower-hub.com/wiki/card/card-masteries).
6. **Module material accounting.** A rare merged to epic is not a natural epic and gains no unique effect. Merge recipes distinguish exact duplicate materials from type/rarity wildcards. Restore refunds levels' coins/shards while retaining subeffects, but does not refund reroll shards. Unmerge and shatter have their own outcomes and unlocks. [Modules](https://www.tower-hub.com/wiki/module/modules), [Module labs](https://www.tower-hub.com/wiki/module/module-labs).
7. **Module reroll planner.** Add effect/rarity acceptance lists, locks that change cost, researched bans, native Auto Roll cancellation and stop budgets, and effects removed from the pool by already-maxed UW stats. [Sub-effects](https://www.tower-hub.com/wiki/module/sub-module-effects).
8. **Assist modules.** Distinct slots, stone unlock/efficiency investment, milestone/Lab prerequisites, independent levels, no same-name pairing, fractional quantity floors and hard-cap behavior all affect ROI. This is more than a second equipment slot. [Modules](https://www.tower-hub.com/wiki/module/modules), [Assist labs](https://www.tower-hub.com/wiki/module/assist-module-labs).
9. **Currency-source planning.** Cells fund Lab boosts; keys connect Legend tournament performance to Vault upgrades; bits fund Chips; tokens buy guild stock; medals persist but event relic progress has different eligibility. Sources that involve real-money purchases or external offers are descriptive game concepts, not implicit automation scope. [Cells](https://www.tower-hub.com/wiki/currency/currency-elite-cells), [Keys](https://www.tower-hub.com/wiki/currency/currency-keys), [Bits](https://www.tower-hub.com/wiki/currency/currency-bits), [Medals](https://www.tower-hub.com/wiki/currency/currency-medals).
10. **Fleet encounters.** Saboteur, Commander, Overcharge, escorts, reduced-control effects, current-health thorns, special attack behaviors, and forced spawns across skipped waves require explicit support. High-wave fleets appear below Tier 14 according to later sections of the same article. Avoid a global `tier < 14 => no fleets` shortcut. [Fleet enemies](https://www.tower-hub.com/wiki/enemy/fleet-enemies).
11. **Better run acquisition.** More Round Stats unlocks the game's Battle History, which includes normal/tournament runs but retains a bounded recent history. Reading that history can complement bot observations and recover missing run summaries after restart. [More Round Stats](https://www.tower-hub.com/wiki/workshop/more-round-stats).
12. **Reward eligibility, not just effects.** Critical-hit event missions have stricter qualifying kill rules than Critical Coin income. Scatter splits do not each produce independent cell rewards. Module effects may still trigger on an orb/shockwave contact against an enemy immune to the underlying kill/control effect. [Critical Coin](https://www.tower-hub.com/wiki/workshop/critical-coin), [Enemies](https://www.tower-hub.com/wiki/enemy/enemies), [Unique effects](https://www.tower-hub.com/wiki/module/epic-module-unique-effect).

## Source conflict register — resolve before numerical automation

| Finding in reviewed prose | Consequence |
|---|---|
| Enemy overview says Protector cap 8 in one section and 10 in another; several headings pair the wrong enemy name and lab content. | Semantic extraction must use verified identity, not heading position alone; cap requires current-version confirmation. |
| Fleet articles begin with Tier 14-only wording but later explicitly describe high-wave fleets on all tiers. | Preserve tier/wave/version predicates; prefer verified spawn tables and current-game evidence over the opening summary. |
| Modules overview initially lists substat slot thresholds only through 161, then later includes 201 and 241. | Use a complete validated table, not an early prose list as the cap. |
| Sub-effects article says effect rarity is bounded by module rarity, then repeats an ambiguous “and vice versa” sentence. | Roll-pool legality needs a tested rule with provenance. |
| Currency overview claims six currencies while describing additional ones; Shards article explicitly says documentation is unfinished. | No currency completeness claim from the overview count; distinguish module and reroll shards using module-system sources. |
| Critical Coin card page says critical shots only; newer Workshop article says all qualifying kills except noncritical bullets and separates event rules. The Workshop introduction also calls the card a Workshop upgrade. | Source route/category and natural-language summary do not reliably identify the implementation system or kill predicate. |
| Wave Accelerator Workshop page claims shorter boss spacing allows faster Shield charge accumulation; Energy Shield page explains charges depend on game time, so more waves can pass per charge. Wave Skip also loosely calls this “real time.” | Explicit clocks and empirical recharge fixtures are necessary. |
| Free Upgrades says combined maximum 90.75% while also describing >100% rollover and a later enhancement multiplier. | Caps must include source context and progression prerequisites. |
| Critical/Super Critical enhancement prose gives increments inconsistent with its own level cap/final multiplier. | Reconcile full tables, actual game values and calculator parity before computing ROI. |
| Several pages mix system terminology: Game Speed and Starting Cash called Workshop upgrades though Labs are described; Rend enhancement called a lab. Attack discount wording mentions spending coins “during a run.” | Canonical system identity must be independently validated; do not generate executors directly from article category or prose label. |
| Attack Speed page explicitly says current behavior is still under analysis and displayed speed is not shots/second. | Mark model uncertainty; use measured outcomes rather than precision claims unsupported by the source. |

These are not reasons to discard the wiki. They are concrete requirements for a mechanic/version/conflict registry, evidence-backed state model, calculator-parity tests, and staged live certification.

## Exact reviewed-file manifest

The following list contains only files actually read in full for this supplement. Paths are relative to `docs/research/wiki-source-2026-09-05/`.

### Workshop — 48 files

- `wiki__workshop__attack-speed.prose.txt`
- `wiki__workshop__attack-upgrades.prose.txt`
- `wiki__workshop__bounce-shot.prose.txt`
- `wiki__workshop__buy-multiplier.prose.txt`
- `wiki__workshop__cash-bonus.prose.txt`
- `wiki__workshop__cash-per-wave.prose.txt`
- `wiki__workshop__coins-per-kill-bonus.prose.txt`
- `wiki__workshop__coins-per-wave.prose.txt`
- `wiki__workshop__critical-coin.prose.txt`
- `wiki__workshop__critical-hits.prose.txt`
- `wiki__workshop__damage-per-meter.prose.txt`
- `wiki__workshop__damage.prose.txt`
- `wiki__workshop__death-defy.prose.txt`
- `wiki__workshop__defense-absolute.prose.txt`
- `wiki__workshop__defense-percent.prose.txt`
- `wiki__workshop__defense-upgrades.prose.txt`
- `wiki__workshop__enemy-level-skip.prose.txt`
- `wiki__workshop__free-upgrades.prose.txt`
- `wiki__workshop__game-speed.prose.txt`
- `wiki__workshop__health-regen.prose.txt`
- `wiki__workshop__health.prose.txt`
- `wiki__workshop__interest.prose.txt`
- `wiki__workshop__knockback.prose.txt`
- `wiki__workshop__land-mine.prose.txt`
- `wiki__workshop__lifesteal.prose.txt`
- `wiki__workshop__more-round-stats.prose.txt`
- `wiki__workshop__multishot.prose.txt`
- `wiki__workshop__orbs.prose.txt`
- `wiki__workshop__range.prose.txt`
- `wiki__workshop__rapid-fire.prose.txt`
- `wiki__workshop__recovery-packages.prose.txt`
- `wiki__workshop__rend-armor.prose.txt`
- `wiki__workshop__shockwave.prose.txt`
- `wiki__workshop__starting-cash.prose.txt`
- `wiki__workshop__super-critical.prose.txt`
- `wiki__workshop__thorn-damage.prose.txt`
- `wiki__workshop__utility-upgrades.prose.txt`
- `wiki__workshop__wall.prose.txt`
- `wiki__workshop__wave-accelerator.prose.txt`
- `wiki__workshop__wave-skip.prose.txt`
- `wiki__workshop__workshop-attack-discount.prose.txt`
- `wiki__workshop__workshop-enhancement-attack.prose.txt`
- `wiki__workshop__workshop-enhancement-defense.prose.txt`
- `wiki__workshop__workshop-enhancement-utility.prose.txt`
- `wiki__workshop__workshop-enhancement.prose.txt`
- `wiki__workshop__workshop-respec.prose.txt`
- `wiki__workshop__workshop-upgrades.prose.txt`
- `wiki__workshop__workshop-utility-discount.prose.txt`

### Card — 35 files

- `wiki__card__card-masteries.prose.txt`
- `wiki__card__card-presets.prose.txt`
- `wiki__card__card-slots.prose.txt`
- `wiki__card__cards-area-of-effect.prose.txt`
- `wiki__card__cards-attack-speed.prose.txt`
- `wiki__card__cards-berserker.prose.txt`
- `wiki__card__cards-cash.prose.txt`
- `wiki__card__cards-coins.prose.txt`
- `wiki__card__cards-critical-chance.prose.txt`
- `wiki__card__cards-critical-coin.prose.txt`
- `wiki__card__cards-damage.prose.txt`
- `wiki__card__cards-enemy-balance.prose.txt`
- `wiki__card__cards-extra-defense.prose.txt`
- `wiki__card__cards-extra-orb.prose.txt`
- `wiki__card__cards-fortress.prose.txt`
- `wiki__card__cards-free-upgrades.prose.txt`
- `wiki__card__cards-health-regen.prose.txt`
- `wiki__card__cards-health.prose.txt`
- `wiki__card__cards-intro-sprint.prose.txt`
- `wiki__card__cards-land-mine-stun.prose.txt`
- `wiki__card__cards-range.prose.txt`
- `wiki__card__cards-recovery-package-chance.prose.txt`
- `wiki__card__cards-super-tower.prose.txt`
- `wiki__card__cards-ultimate-crit.prose.txt`
- `wiki__card__cards-wave-accelerator.prose.txt`
- `wiki__card__cards-wave-skip.prose.txt`
- `wiki__card__cards.prose.txt`
- `wiki__card__death-ray.prose.txt`
- `wiki__card__demon-mode.prose.txt`
- `wiki__card__energy-net.prose.txt`
- `wiki__card__energy-shield.prose.txt`
- `wiki__card__nuke.prose.txt`
- `wiki__card__plasma-cannon.prose.txt`
- `wiki__card__second-wind.prose.txt`
- `wiki__card__slow-aura.prose.txt`

### Currency — 11 files

- `wiki__currency__currency-bits.prose.txt`
- `wiki__currency__currency-cash.prose.txt`
- `wiki__currency__currency-coins.prose.txt`
- `wiki__currency__currency-elite-cells.prose.txt`
- `wiki__currency__currency-gems.prose.txt`
- `wiki__currency__currency-keys.prose.txt`
- `wiki__currency__currency-medals.prose.txt`
- `wiki__currency__currency-power-stones.prose.txt`
- `wiki__currency__currency-shards.prose.txt`
- `wiki__currency__currency-tokens.prose.txt`
- `wiki__currency__currency.prose.txt`

### Enemy — 14 files

- `wiki__enemy__boss-labs.prose.txt`
- `wiki__enemy__brick-discriptions.prose.txt`
- `wiki__enemy__common-enemy-labs.prose.txt`
- `wiki__enemy__elite-enemies.prose.txt`
- `wiki__enemy__enemies.prose.txt`
- `wiki__enemy__enemy-balance.prose.txt`
- `wiki__enemy__fast-enemy-labs.prose.txt`
- `wiki__enemy__fleet-enemies.prose.txt`
- `wiki__enemy__protector-labs.prose.txt`
- `wiki__enemy__ranged-enemy-labs.prose.txt`
- `wiki__enemy__ray-labs.prose.txt`
- `wiki__enemy__scatter-labs.prose.txt`
- `wiki__enemy__tank-enemy-labs.prose.txt`
- `wiki__enemy__vampire-labs.prose.txt`

### Module — 6 files

- `wiki__module__assist-module-labs.prose.txt`
- `wiki__module__epic-module-unique-effect.prose.txt`
- `wiki__module__module-labs-reroll-shards.prose.txt`
- `wiki__module__module-labs.prose.txt`
- `wiki__module__modules.prose.txt`
- `wiki__module__sub-module-effects.prose.txt`
