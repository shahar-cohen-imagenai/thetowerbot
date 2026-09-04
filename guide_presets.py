"""Guide-backed battle and Workshop plans for the strategy editor.

The plans contain priorities only. Applying one must preserve the Strategy's
enable switches, arm switch, budgets, reserves, cadence and card policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from policy import PRESETS, PolicyError, preset_rules
from strategy import ShoppingRule
from upgrades import by_id


@dataclass(frozen=True)
class GuideSource:
    title: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url}


@dataclass(frozen=True)
class GuidePreset:
    description: str
    workshop: tuple[ShoppingRule, ...]
    notes: tuple[str, ...]
    sources: tuple[GuideSource, ...]


_BEGINNER = GuideSource(
    "Tower Hub Beginner Guide",
    "https://www.tower-hub.com/wiki/guide/beginner-guide",
)
_HEALTH = GuideSource(
    "Tower Hub Health Build",
    "https://www.tower-hub.com/wiki/guide/health-build",
)
_TURTLE = GuideSource(
    "Community Turtle Strategy",
    "https://the-tower.notion.site/"
    "Turtle-Strategy-early-game-1bc91383b93f809c81d0e2825cfeac07",
)
_BLENDER = GuideSource(
    "Community Blender Strategy",
    "https://the-tower.notion.site/"
    "Blender-Strategy-early-game-1bc91383b93f80e4b384c148c67b66f8",
)


def _row(
    upgrade_id: str,
    *,
    enabled: bool = True,
    target: float | None = None,
) -> ShoppingRule:
    upgrade = by_id(upgrade_id)
    if upgrade is None:  # A catalog edit should fail at import, not at runtime.
        raise RuntimeError(f"guide preset uses unknown upgrade_id {upgrade_id!r}")
    return ShoppingRule(upgrade.name, upgrade.category, enabled, target)


_GUIDES: dict[str, GuidePreset] = {
    "manual": GuidePreset(
        description="Choose and order every battle and Workshop upgrade yourself.",
        workshop=(),
        notes=(),
        sources=(),
    ),
    "turtle": GuidePreset(
        description=(
            "Tier 1 turtle priorities: unlock thorns, establish defense, and build "
            "the early economy that funds in-run survival."
        ),
        workshop=(
            _row("unlock_defense_upgrades"),
            _row("unlock_thorns"),
            _row("defense_absolute"),
            _row("thorns", target=11),
            _row("unlock_cash_bonuses"),
            _row("cash_per_wave"),
            _row("cash_bonus"),
            _row("unlock_coin_bonuses"),
            _row("coins_per_wave"),
            _row("coins_per_kill_bonus"),
            _row("health"),
            _row("defense_percent"),
        ),
        notes=(
            "Tier 1 is the useful range for an absolute-defense turtle; transition "
            "toward Health and Defense % as that stat loses effectiveness.",
            "Thorns stops at the first useful 11% breakpoint in Workshop; the "
            "in-run turtle policy advances later breakpoints as waves rise.",
            "The beginner guide's roughly 50 Workshop levels for Defense Absolute "
            "cannot be encoded as a value target because OCR reads the resulting "
            "stat, not the Workshop level.",
        ),
        sources=(_BEGINNER, _TURTLE),
    ),
    "health": GuidePreset(
        description=(
            "Early effective-health priorities: unlock crowd control and orbs, "
            "then grow Health, Defense %, income, and supporting damage."
        ),
        workshop=(
            _row("unlock_cash_bonuses"),
            _row("unlock_coin_bonuses"),
            _row("coins_per_kill_bonus"),
            _row("unlock_defense_upgrades"),
            _row("health"),
            _row("defense_percent"),
            _row("unlock_thorns"),
            _row("unlock_lifesteal"),
            _row("unlock_knockback"),
            _row("knockback_chance"),
            _row("knockback_force"),
            _row("unlock_orbs"),
            _row("orbs"),
            _row("orb_speed"),
            _row("thorns"),
            _row("unlock_range_upgrades"),
            _row("unlock_multishot"),
            _row("unlock_rapid_fire"),
            _row("attack_speed"),
            _row("unlock_free_upgrades"),
            _row("free_utility_upgrade"),
            _row("free_defense_upgrade"),
            _row("free_attack_upgrade"),
            _row("lifesteal"),
            _row("cash_bonus"),
            _row("damage"),
        ),
        notes=(
            "Unlock Attack through Rapid Fire, Defense through Orbs, and Utility "
            "through Free Upgrades; Bounce Shot and Land Mines are optional.",
            "Max inexpensive upgrades first, especially Defense, then prioritize "
            "Health and Damage without unlocking Wall or Rend Armor early.",
            "For Free Upgrades, prefer Utility, then Defense, then Attack.",
        ),
        sources=(_BEGINNER, _HEALTH, _BLENDER),
    ),
}


if set(_GUIDES) != set(PRESETS):
    raise RuntimeError("guide presets and battle policy presets must stay in sync")


def preset_payload(name: str) -> dict[str, Any]:
    """Return one independent JSON-ready plan for the strategy editor."""

    if not isinstance(name, str) or name not in _GUIDES:
        raise PolicyError("preset", f"preset must be one of {PRESETS}")
    guide = _GUIDES[name]
    return {
        "name": name,
        "rules": [rule.to_dict() for rule in preset_rules(name)],
        "workshop": [rule.to_dict() for rule in guide.workshop],
        "description": guide.description,
        "notes": list(guide.notes),
        "sources": [source.to_dict() for source in guide.sources],
    }
