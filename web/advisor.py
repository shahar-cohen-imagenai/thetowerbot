"""Local recommendation imports and pure, append-only Strategy proposals."""

from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

import upgrades
from advisor import AdvisorError, AdvisorStore
from autopilot import AutopilotState
from strategy import ControlError, ShoppingRule, Strategy, StrategyStore


PROFILE_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ImportRequest(BaseModel):
    profile: str = Field(pattern=PROFILE_PATTERN)
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=262144)


class DraftRequest(BaseModel):
    profile: str = Field(pattern=PROFILE_PATTERN)
    import_id: str = Field(min_length=1, max_length=128)
    recommendation_id: str = Field(min_length=1, max_length=128)
    draft: dict[str, Any]


def example_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": {"name": "EXAMPLE — replace with source name", "version": "EXAMPLE_VERSION",
                   "account_name": "EXAMPLE_ACCOUNT", "exported_at": 0, "account_snapshot_at": 0},
        "missing_inputs": ["Replace example with your account data"],
        "recommendations": [{"id": "health-1", "path": "health", "system": "lab",
                             "upgrade": "Health", "current_value": 1, "target_value": 2,
                             "value_kind": "level", "cost": None, "currency": "coins",
                             "benefit": None}],
    }


def propose_draft(body: DraftRequest, snapshot: dict[str, Any],
                  observations: list[dict[str, Any]]) -> dict[str, Any]:
    if snapshot["import_id"] != body.import_id:
        raise HTTPException(409, "The import changed. Refresh the advisor before staging.")
    row = next((r for r in snapshot["recommendations"] if r["id"] == body.recommendation_id), None)
    if row is None:
        raise HTTPException(404, "Recommendation not found in this import.")
    if not row["can_stage"]:
        raise HTTPException(409, row["blocked_reason"] or "This recommendation is advisory only.")
    try:
        draft = Strategy.from_dict(body.draft).validated()
    except ControlError as exc:
        raise HTTPException(422, str(exc)) from exc
    if draft.name != body.profile:
        raise HTTPException(409, "The draft belongs to a different profile.")
    entry = upgrades.resolve(row["upgrade"])
    if entry is None or entry.unlock or entry.id != row["upgrade_id"]:
        raise HTTPException(409, "This recommendation has no supported Workshop stat identity.")
    matches = [rule for rule in draft.shopping.workshop
               if (known := upgrades.resolve(rule.name)) is not None and known.id == entry.id]
    if any(rule.category != entry.category for rule in matches):
        raise HTTPException(409, f"Correct the category of the existing {entry.name} row first.")
    if matches:
        return {"draft": draft.to_dict(), "added": False,
                "message": f"{entry.name} is already planned. Its target, enabled state and priority were preserved."}
    parent = next((item for item in upgrades.CATALOG if entry.id in item.unlocks), None)
    if parent is not None:
        planned = any(rule.enabled and rule.category == parent.category
                      and (known := upgrades.resolve(rule.name)) is not None and known.id == parent.id
                      for rule in draft.shopping.workshop)
        observed = any(item.get("context") == "workshop" and item.get("category") == entry.category
                       and ((item.get("upgrade_id") == entry.id and item.get("status") in {"available", "maxed"})
                            or (item.get("upgrade_id") == parent.id and item.get("status") == "unlocked"))
                       for item in observations)
        if not planned and not observed:
            raise HTTPException(409, f"Plan and enable {parent.name} first, or scan the unlocked Workshop upgrade.")
    shopping = replace(draft.shopping, workshop=(*draft.shopping.workshop,
                       ShoppingRule(entry.name, entry.category, target=row["target_value"])))
    return {"draft": replace(draft, shopping=shopping).to_dict(), "added": True,
            "message": f"Added {entry.name} after your existing Workshop priorities. Save to apply the draft."}


def advisor_router(advisor: AdvisorStore, strategies: StrategyStore | None,
                   observations: AutopilotState) -> APIRouter:
    router = APIRouter(prefix="/api/advisor")

    async def check_profile(profile: str) -> None:
        if strategies is not None:
            try:
                await asyncio.to_thread(strategies.load, profile)
            except ControlError as exc:
                raise HTTPException(404 if exc.code == "not_found" else 422, str(exc)) from exc

    @router.get("")
    async def snapshot(profile: str = Query(default="default", pattern=PROFILE_PATTERN)) -> dict[str, Any]:
        await check_profile(profile)
        try:
            return await asyncio.to_thread(advisor.snapshot, profile)
        except AdvisorError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/import")
    async def import_file(body: ImportRequest) -> dict[str, Any]:
        await check_profile(body.profile)
        try:
            return await asyncio.to_thread(advisor.import_file, body.profile, body.filename, body.content)
        except AdvisorError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/draft")
    async def draft(body: DraftRequest) -> dict[str, Any]:
        current = await snapshot(body.profile)
        return await asyncio.to_thread(propose_draft, body, current, observations.snapshot()["observations"])

    @router.get("/template.json")
    async def json_template() -> dict[str, Any]:
        return example_document()

    @router.get("/template.csv")
    async def csv_template() -> Response:
        example = example_document()
        source = example["source"]
        row = {"schema_version": 1, "source_name": source["name"], "source_version": source["version"],
               "source_url": "", "account_name": source["account_name"], "exported_at": 0,
               "account_snapshot_at": 0, "missing_inputs": ";".join(example["missing_inputs"]),
               **example["recommendations"][0], "upgrade_id": ""}
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(row), lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
        return Response(output.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="advisor-example.csv"'})

    return router
