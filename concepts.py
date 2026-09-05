"""Versioned semantic references; execution remains restricted to upgrades.CATALOG.

The snapshot proves names were inventoried, not that game rules were verified.
Unknown versions, limits and prerequisites deliberately carry no inferred values.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
from typing import Any

import upgrades

REGISTRY_PATH = Path(__file__).resolve().parent / "catalog" / "concepts.v1.json"


@dataclass(frozen=True)
class Validity:
    status: str
    game_version_min: str | None
    game_version_max: str | None

    def includes(self, game_version: str) -> bool:
        version = _version(game_version)
        return (self.status == "known"
                and (self.game_version_min is None or _version(self.game_version_min) <= version)
                and (self.game_version_max is None or version <= _version(self.game_version_max)))


@dataclass(frozen=True)
class Caps:
    max_level: int | None
    max_value: float | None


@dataclass(frozen=True)
class Source:
    id: str
    file: str
    sha256: str
    captured_at: str
    revision: str | None
    content_scope: str


@dataclass(frozen=True)
class Concept:
    concept_id: str
    name: str
    domain: str
    kind: str
    aliases: tuple[str, ...]
    legacy_id: str | None
    unit: str | None
    caps: Caps | None
    prerequisites: tuple[str, ...] | None
    source_refs: tuple[str, ...]
    source_url: str | None
    validity: Validity
    definition_verified: bool
    rule_verified: bool
    execution_scopes: tuple[str, ...]
    unlocks: tuple[str, ...]


@dataclass(frozen=True)
class GlossaryEntry:
    id: str
    term: str
    domain: str
    concept_id: str
    mapping_kind: str
    source_ref: str
    source_url: str
    definition_verified: bool


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(s, str) or not s for s in value):
        raise ValueError("expected a list of nonempty strings")
    return tuple(value)


def _normalise(label: str) -> str:
    # A plus distinguishes Ultimate Weapons from their later UW+ system.
    return re.sub(r"[^a-z0-9+]+", "", label.casefold())


def _version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+){0,3}", value):
        raise ValueError("game version must have one to four numeric components")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


@dataclass(frozen=True)
class Registry:
    schema_version: int
    registry_version: str
    sources: tuple[Source, ...]
    concepts: tuple[Concept, ...]
    glossary: tuple[GlossaryEntry, ...]

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> Registry:
        """Validate immutable metadata and references before exposing records."""
        try:
            if set(raw) != {"schema_version", "registry_version", "sources", "concepts", "glossary"}:
                raise ValueError("unexpected registry schema fields")
            if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
                raise ValueError("unsupported registry schema version")
            if not isinstance(raw["registry_version"], str) or not raw["registry_version"]:
                raise ValueError("registry version is required")
            sources = tuple(Source(**s) for s in raw["sources"])
            records = []
            for item in raw["concepts"]:
                fields = dict(item)
                fields["validity"] = Validity(**fields["validity"])
                if fields["caps"] is not None:
                    fields["caps"] = Caps(**fields["caps"])
                if fields["prerequisites"] is not None:
                    fields["prerequisites"] = _strings(fields["prerequisites"])
                for key in ("aliases", "source_refs", "execution_scopes", "unlocks"):
                    fields[key] = _strings(fields[key])
                records.append(Concept(**fields))
            result = cls(raw["schema_version"], raw["registry_version"], sources,
                         tuple(records), tuple(GlossaryEntry(**g) for g in raw["glossary"]))
            result._validate()
            return result
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"malformed concept registry: {exc}") from exc

    def _validate(self) -> None:
        source_ids = {s.id for s in self.sources}
        ids = {c.concept_id for c in self.concepts}
        legacy = [c.legacy_id for c in self.concepts if c.legacy_id is not None]
        if len(source_ids) != len(self.sources) or len(ids) != len(self.concepts):
            raise ValueError("duplicate source or concept ID")
        if len(legacy) != len(set(legacy)):
            raise ValueError("duplicate legacy ID")
        if set(legacy) != {u.id for u in upgrades.CATALOG}:
            raise ValueError("legacy identities must match the existing execution catalog")
        for source in self.sources:
            if not all(isinstance(v, str) and v for v in
                       (source.id, source.file, source.captured_at, source.content_scope)):
                raise ValueError("source snapshot metadata is required")
            if not re.fullmatch(r"[0-9a-f]{64}", source.sha256):
                raise ValueError("source snapshot requires a SHA-256 digest")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", source.captured_at):
                raise ValueError("source snapshot requires a capture date")
        for concept in self.concepts:
            if not re.fullmatch(r"[a-z][a-z0-9-]*\.[a-z0-9][a-z0-9_-]*", concept.concept_id):
                raise ValueError("concept IDs must be namespaced")
            if any(not isinstance(v, str) or not v.strip()
                   for v in (concept.name, concept.domain, concept.kind)):
                raise ValueError("concept name, domain and kind are required")
            if concept.unit is not None and (not isinstance(concept.unit, str) or not concept.unit):
                raise ValueError("unit must be a label or unknown")
            if not concept.source_refs or not set(concept.source_refs) <= source_ids:
                raise ValueError("unknown source reference")
            if not set(concept.unlocks) <= ids:
                raise ValueError("unknown unlock reference")
            validity = concept.validity
            bounds = (validity.game_version_min, validity.game_version_max)
            if validity.status == "unknown":
                if any(v is not None for v in bounds):
                    raise ValueError("unknown validity cannot declare version bounds")
            elif validity.status == "known":
                versions = [_version(v) for v in bounds if v is not None]
                if not versions or len(versions) == 2 and versions[0] > versions[1]:
                    raise ValueError("known validity requires consistent version bounds")
            else:
                raise ValueError("invalid validity status")
            if type(concept.definition_verified) is not bool or type(concept.rule_verified) is not bool:
                raise ValueError("verification flags must be boolean")
            if concept.caps is not None:
                level, value = concept.caps.max_level, concept.caps.max_value
                if level is not None and (type(level) is not int or level < 0):
                    raise ValueError("max level must be a nonnegative integer")
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError("max value must be finite and nonnegative")
            if concept.prerequisites is not None and not set(concept.prerequisites) <= ids:
                raise ValueError("unknown prerequisite reference")
            if concept.legacy_id is None:
                if concept.execution_scopes or concept.unlocks:
                    raise ValueError("reference concepts cannot grant execution or unlocks")
            else:
                upgrade = upgrades.by_id(concept.legacy_id)
                if upgrade is None or upgrade.concept_id != concept.concept_id:
                    raise ValueError("invalid legacy concept mapping")
                if concept.execution_scopes != ("battle", "workshop"):
                    raise ValueError("legacy execution scopes must remain unchanged")
                if concept.unlocks != tuple(upgrades.by_id(child).concept_id for child in upgrade.unlocks):
                    raise ValueError("legacy unlock references must remain unchanged")
        if len({g.id for g in self.glossary}) != len(self.glossary):
            raise ValueError("duplicate glossary ID")
        for entry in self.glossary:
            if entry.concept_id not in ids or entry.source_ref not in source_ids:
                raise ValueError("unknown glossary concept or source reference")
            if entry.mapping_kind not in {"concept", "reference", "strategy"}:
                raise ValueError("invalid glossary mapping kind")
            if entry.definition_verified is not False:
                raise ValueError("glossary definitions remain unverified")

    def by_id(self, concept_id: str) -> Concept | None:
        return next((c for c in self.concepts if c.concept_id == concept_id), None)

    def resolve(self, name: str, *, domain: str | None = None, kind: str | None = None,
                game_version: str | None = None) -> Concept | None:
        """Return exactly one matching reference, never an executable decision."""
        if not isinstance(name, str) or not _normalise(name):
            return None
        if game_version is not None:
            _version(game_version)
        key = _normalise(name)
        matches = [c for c in self.concepts
                   if (domain is None or c.domain == domain) and (kind is None or c.kind == kind)
                   and (game_version is None or c.validity.includes(game_version))
                   and any(_normalise(label) == key for label in (c.concept_id, c.name, *c.aliases))]
        return matches[0] if len(matches) == 1 else None

    def payload(self) -> dict[str, Any]:
        """Return detached JSON-compatible metadata for read-only consumers."""
        return json.loads(json.dumps(asdict(self)))


REGISTRY = Registry.from_payload(json.loads(REGISTRY_PATH.read_text(encoding="utf-8")))
