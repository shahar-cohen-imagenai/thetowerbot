"""Farming comparisons from completed runs, using actual elapsed time."""
from __future__ import annotations

from statistics import median
from typing import Any


def compare_tiers(runs: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[int, list[tuple[float, float, int | None]]] = {}
    for run in runs:
        if run.get("purpose", "farm") != "farm":
            continue
        tier, end, coins = run.get("tier"), run.get("ended_at"), run.get("coins")
        start = run.get("started_at")
        if (tier is None or start is None or end is None or coins is None
                or run.get("abandoned") or end <= start or coins < 0):
            continue
        grouped.setdefault(tier, []).append((coins, end-start, run.get("wave")))
    tiers = []
    for tier, samples in sorted(grouped.items()):
        waves = [r[2] for r in samples if r[2] is not None]
        tiers.append(dict(tier=tier, runs=len(samples),
                          coins_per_hour=sum(r[0] for r in samples) * 3600 / sum(r[1] for r in samples),
                          median_wave=median(waves) if waves else None))
    eligible = [t for t in tiers if t["runs"] >= 3]
    best = max(eligible, key=lambda t: t["coins_per_hour"], default=None)
    return dict(tiers=tiers, recommended_tier=best["tier"] if best else None,
                reason=("Best observed coin rate among tiers with at least three completed runs. "
                        "Milestone pushes are a separate goal; this does not change the game tier."
                        if best else "Complete at least three runs on a tier to establish a farming baseline."))
