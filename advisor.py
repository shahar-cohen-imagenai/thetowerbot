"""Strict local imports for account-specific upgrade recommendations.

The accepted CSV is the normalized table described by the Effective Paths
integration plan, not a native workbook export. This module evaluates no
formulas and performs no network or device I/O.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from upgrades import by_id, resolve, target_reached


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 256 * 1024
MAX_RECOMMENDATIONS = 200
STALE_AFTER_SECONDS = 24 * 60 * 60

_PATHS = frozenset({"health", "damage", "economy"})
_SYSTEMS = frozenset(
    {"workshop", "lab", "ultimate_weapon", "enhancement", "other"}
)
_VALUE_KINDS = frozenset({"level", "stat"})
_CURRENCIES = frozenset(
    {"coins", "gems", "stones", "medals", "time", "other"}
)
_PROFILE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_SOURCE_FIELDS = frozenset(
    {"name", "version", "account_name", "exported_at", "account_snapshot_at", "url"}
)
_RECOMMENDATION_FIELDS = (
    "id",
    "path",
    "system",
    "upgrade",
    "upgrade_id",
    "current_value",
    "target_value",
    "value_kind",
    "cost",
    "currency",
    "benefit",
)
_REQUIRED_RECOMMENDATION_FIELDS = frozenset(_RECOMMENDATION_FIELDS) - {"upgrade_id"}
_CSV_FIELDS = (
    "schema_version",
    "source_name",
    "source_version",
    "source_url",
    "account_name",
    "exported_at",
    "account_snapshot_at",
    "missing_inputs",
    *_RECOMMENDATION_FIELDS,
)
_CSV_METADATA_FIELDS = _CSV_FIELDS[:8]
_STORE_FIELDS = frozenset({"schema_version", "imports"})
_RECORD_FIELDS = frozenset(
    {"import_id", "imported_at", "source", "missing_inputs", "recommendations"}
)


class AdvisorError(ValueError):
    """An invalid import or profile, with the offending field attached."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def inspect_native_workbooks(effective_paths: bytes, ids_master: bytes) -> dict[str, Any]:
    """Validate native source identity separately from normalized row imports.

    A known native layout never authorizes Workshop staging or spending. Account
    input mapping, calculation parity and native path adapters are prerequisites.
    """
    from effective_paths_contract import validate_native_workbooks

    return validate_native_workbooks(effective_paths, ids_master)


def _profile_name(profile: object) -> str:
    if not isinstance(profile, str) or _PROFILE.fullmatch(profile) is None:
        raise AdvisorError(
            "profile",
            "profile must be 1-64 letters, digits, underscores or hyphens",
        )
    return profile


def _text(
    value: object,
    field: str,
    *,
    limit: int = 256,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise AdvisorError(field, f"{field} must be a string")
    result = value.strip()
    if not allow_empty and not result:
        raise AdvisorError(field, f"{field} may not be empty")
    if len(result) > limit:
        raise AdvisorError(field, f"{field} may not exceed {limit} characters")
    try:
        result.encode("utf-8")
    except UnicodeEncodeError:
        raise AdvisorError(field, f"{field} must be valid UTF-8 text") from None
    return result


def _enum(value: object, field: str, allowed: frozenset[str]) -> str:
    result = _text(value, field, limit=64)
    if result not in allowed:
        raise AdvisorError(field, f"{field} must be one of {tuple(sorted(allowed))}")
    return result


def _number(value: object, field: str, *, nullable: bool) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        suffix = " or null" if nullable else ""
        raise AdvisorError(field, f"{field} must be a number{suffix}")
    try:
        result = float(value)
    except OverflowError:
        raise AdvisorError(field, f"{field} must be finite and non-negative") from None
    if not math.isfinite(result) or result < 0:
        raise AdvisorError(field, f"{field} must be finite and non-negative")
    return result


def _csv_number(value: str, field: str, *, nullable: bool) -> float | None:
    stripped = value.strip()
    if not stripped and nullable:
        return None
    if not stripped:
        raise AdvisorError(field, f"{field} may not be empty")
    try:
        parsed = float(stripped)
    except ValueError:
        raise AdvisorError(field, f"{field} must be a number") from None
    return _number(parsed, field, nullable=nullable)


def _reject_formula(value: str) -> None:
    if value.lstrip().startswith(_FORMULA_PREFIXES):
        raise AdvisorError("content", "spreadsheet formula cells are not accepted")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdvisorError("content", f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _reject_json_formulas(value: object) -> None:
    if isinstance(value, str):
        _reject_formula(value)
    elif isinstance(value, Mapping):
        for nested in value.values():
            _reject_json_formulas(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_json_formulas(nested)


def _schema_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdvisorError("schema_version", "schema_version must be the integer 1")
    if value != SCHEMA_VERSION:
        raise AdvisorError("schema_version", "unsupported advisor schema version")
    return value


def _source(raw: object, now: float) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AdvisorError("source", "source must be a mapping")
    unknown = set(raw) - _SOURCE_FIELDS
    if unknown:
        field = sorted(unknown)[0]
        raise AdvisorError(field, f"unknown source field {field!r}")
    required = _SOURCE_FIELDS - {"url"}
    missing = required - set(raw)
    if missing:
        field = sorted(missing)[0]
        raise AdvisorError(field, f"source.{field} is required")
    result: dict[str, Any] = {
        "name": _text(raw["name"], "name"),
        "version": _text(raw["version"], "version", limit=128),
        "account_name": _text(raw["account_name"], "account_name"),
        "exported_at": _number(raw["exported_at"], "exported_at", nullable=False),
        "account_snapshot_at": _number(
            raw["account_snapshot_at"], "account_snapshot_at", nullable=False
        ),
    }
    url = raw.get("url")
    if url is not None:
        parsed_url = _text(url, "url", limit=2048)
        try:
            parsed = urlparse(parsed_url)
        except ValueError:
            raise AdvisorError("url", "source.url must be a valid HTTPS URL") from None
        if parsed.scheme != "https" or not parsed.netloc:
            raise AdvisorError("url", "source.url must be an absolute HTTPS URL")
        result["url"] = parsed_url
    exported_at = result["exported_at"]
    snapshot_at = result["account_snapshot_at"]
    if exported_at > now:
        raise AdvisorError("exported_at", "exported_at may not be in the future")
    if snapshot_at > now:
        raise AdvisorError(
            "account_snapshot_at", "account_snapshot_at may not be in the future"
        )
    if snapshot_at > exported_at:
        raise AdvisorError(
            "account_snapshot_at", "account snapshot may not be newer than its export"
        )
    return result


def _missing_inputs(raw: object) -> list[str]:
    if not isinstance(raw, list):
        raise AdvisorError("missing_inputs", "missing_inputs must be a string list")
    if len(raw) > MAX_RECOMMENDATIONS:
        raise AdvisorError("missing_inputs", "too many missing inputs")
    result = [_text(item, "missing_inputs") for item in raw]
    if len(result) != len(set(result)):
        raise AdvisorError("missing_inputs", "missing_inputs must be unique")
    return result


def _recommendation(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AdvisorError("recommendations", "each recommendation must be a mapping")
    unknown = set(raw) - set(_RECOMMENDATION_FIELDS)
    if unknown:
        field = sorted(unknown)[0]
        raise AdvisorError(field, f"unknown recommendation field {field!r}")
    missing = _REQUIRED_RECOMMENDATION_FIELDS - set(raw)
    if missing:
        field = sorted(missing)[0]
        raise AdvisorError(field, f"recommendation.{field} is required")

    source_upgrade_id = raw.get("upgrade_id")
    if source_upgrade_id is not None:
        source_upgrade_id = _text(source_upgrade_id, "upgrade_id", limit=128)
    upgrade_name = _text(raw["upgrade"], "upgrade")
    named = resolve(upgrade_name)
    identified = by_id(source_upgrade_id) if source_upgrade_id is not None else None
    canonical_id: str | None
    if source_upgrade_id is not None and identified is None:
        canonical_id = None
    elif identified is not None and named is not None and identified.id != named.id:
        canonical_id = None
    elif identified is not None and named is None:
        canonical_id = None
    elif identified is not None:
        canonical_id = identified.id
    elif named is not None:
        canonical_id = named.id
    else:
        canonical_id = None

    return {
        "id": _text(raw["id"], "id", limit=128),
        "path": _enum(raw["path"], "path", _PATHS),
        "system": _enum(raw["system"], "system", _SYSTEMS),
        "upgrade": upgrade_name,
        "upgrade_id": canonical_id,
        "current_value": _number(raw["current_value"], "current_value", nullable=True),
        "target_value": _number(raw["target_value"], "target_value", nullable=True),
        "value_kind": _enum(raw["value_kind"], "value_kind", _VALUE_KINDS),
        "cost": _number(raw["cost"], "cost", nullable=True),
        "currency": _enum(raw["currency"], "currency", _CURRENCIES),
        "benefit": _number(raw["benefit"], "benefit", nullable=True),
    }


def _normalise_document(raw: object, now: float) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AdvisorError("content", "advisor JSON must contain one object")
    allowed = {"schema_version", "source", "missing_inputs", "recommendations"}
    unknown = set(raw) - allowed
    if unknown:
        field = sorted(unknown)[0]
        raise AdvisorError(field, f"unknown import field {field!r}")
    missing = allowed - set(raw)
    if missing:
        field = sorted(missing)[0]
        raise AdvisorError(field, f"import field {field!r} is required")
    _schema_version(raw["schema_version"])
    recommendations = raw["recommendations"]
    if not isinstance(recommendations, list):
        raise AdvisorError("recommendations", "recommendations must be a list")
    if len(recommendations) > MAX_RECOMMENDATIONS:
        raise AdvisorError(
            "recommendations",
            f"recommendations may not exceed {MAX_RECOMMENDATIONS} rows",
        )
    rows = [_recommendation(row) for row in recommendations]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise AdvisorError("id", "recommendation IDs must be unique")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": _source(raw["source"], now),
        "missing_inputs": _missing_inputs(raw["missing_inputs"]),
        "recommendations": rows,
    }


def _revision_id(parsed: Mapping[str, Any]) -> str:
    payload = json.dumps(
        parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stored_recommendation(raw: object) -> dict[str, Any]:
    expected = set(_RECOMMENDATION_FIELDS)
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise AdvisorError("store", "stored advisor recommendation has an invalid shape")
    upgrade_name = _text(raw["upgrade"], "store")
    upgrade_id = raw["upgrade_id"]
    if upgrade_id is not None:
        upgrade_id = _text(upgrade_id, "store", limit=128)
    entry = by_id(upgrade_id) if isinstance(upgrade_id, str) else None
    named = resolve(upgrade_name)
    if upgrade_id is not None and (
        entry is None or named is None or named.id != upgrade_id
    ):
        raise AdvisorError("store", "stored advisor identity is inconsistent")
    return {
        "id": _text(raw["id"], "store", limit=128),
        "path": _enum(raw["path"], "store", _PATHS),
        "system": _enum(raw["system"], "store", _SYSTEMS),
        "upgrade": upgrade_name,
        "upgrade_id": upgrade_id,
        "current_value": _number(raw["current_value"], "store", nullable=True),
        "target_value": _number(raw["target_value"], "store", nullable=True),
        "value_kind": _enum(raw["value_kind"], "store", _VALUE_KINDS),
        "cost": _number(raw["cost"], "store", nullable=True),
        "currency": _enum(raw["currency"], "store", _CURRENCIES),
        "benefit": _number(raw["benefit"], "store", nullable=True),
    }


def _stored_record(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _RECORD_FIELDS:
        raise AdvisorError("store", "stored advisor import has an invalid shape")
    import_id = _text(raw["import_id"], "store", limit=64)
    if re.fullmatch(r"[0-9a-f]{64}", import_id) is None:
        raise AdvisorError("store", "stored advisor import_id is invalid")
    imported_at = _number(raw["imported_at"], "store", nullable=False)
    source = _source(raw["source"], float("inf"))
    missing_inputs = _missing_inputs(raw["missing_inputs"])
    raw_rows = raw["recommendations"]
    if not isinstance(raw_rows, list) or len(raw_rows) > MAX_RECOMMENDATIONS:
        raise AdvisorError("store", "stored advisor recommendations are invalid")
    rows = [_stored_recommendation(row) for row in raw_rows]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise AdvisorError("store", "stored advisor recommendation IDs are not unique")
    parsed = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "missing_inputs": missing_inputs,
        "recommendations": rows,
    }
    if _revision_id(parsed) != import_id:
        raise AdvisorError("store", "stored advisor content revision does not match")
    return {
        "import_id": import_id,
        "imported_at": imported_at,
        "source": source,
        "missing_inputs": missing_inputs,
        "recommendations": rows,
    }


def _parse_json(content: str, now: float) -> dict[str, Any]:
    try:
        raw = json.loads(
            content,
            object_pairs_hook=_json_pairs,
        )
    except AdvisorError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        detail = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise AdvisorError("content", f"invalid advisor JSON: {detail}") from None
    try:
        _reject_json_formulas(raw)
        return _normalise_document(raw, now)
    except RecursionError:
        raise AdvisorError("content", "advisor JSON is nested too deeply") from None


def _parse_csv(content: str, now: float) -> dict[str, Any]:
    try:
        reader = csv.DictReader(io.StringIO(content, newline=""))
        headers = reader.fieldnames
        if headers is None:
            raise AdvisorError("content", "advisor CSV needs a header row")
        if len(headers) != len(set(headers)):
            raise AdvisorError("content", "advisor CSV headers must be unique")
        missing = set(_CSV_FIELDS) - set(headers)
        unknown = set(headers) - set(_CSV_FIELDS)
        if missing or unknown:
            detail = sorted(missing or unknown)[0]
            raise AdvisorError("content", f"invalid advisor CSV column {detail!r}")
        csv_rows = list(reader)
    except csv.Error as exc:
        raise AdvisorError("content", f"invalid advisor CSV: {exc}") from None
    if not csv_rows:
        raise AdvisorError("recommendations", "advisor CSV needs at least one row")
    if len(csv_rows) > MAX_RECOMMENDATIONS:
        raise AdvisorError(
            "recommendations",
            f"recommendations may not exceed {MAX_RECOMMENDATIONS} rows",
        )
    for row in csv_rows:
        if None in row or any(value is None for value in row.values()):
            raise AdvisorError("content", "advisor CSV rows must match the header")
        for value in row.values():
            _reject_formula(value)
    metadata = {field: csv_rows[0][field] for field in _CSV_METADATA_FIELDS}
    for row in csv_rows[1:]:
        for field, value in metadata.items():
            if row[field] != value:
                raise AdvisorError(field, f"CSV {field} must be consistent on every row")
    try:
        schema_value = int(metadata["schema_version"])
    except ValueError:
        raise AdvisorError("schema_version", "schema_version must be the integer 1") from None
    source: dict[str, Any] = {
        "name": metadata["source_name"],
        "version": metadata["source_version"],
        "account_name": metadata["account_name"],
        "exported_at": _csv_number(metadata["exported_at"], "exported_at", nullable=False),
        "account_snapshot_at": _csv_number(
            metadata["account_snapshot_at"], "account_snapshot_at", nullable=False
        ),
    }
    if metadata["source_url"].strip():
        source["url"] = metadata["source_url"]
    missing_inputs = (
        [item.strip() for item in metadata["missing_inputs"].split(";")]
        if metadata["missing_inputs"].strip()
        else []
    )
    recommendations: list[dict[str, Any]] = []
    for row in csv_rows:
        recommendations.append(
            {
                "id": row["id"],
                "path": row["path"],
                "system": row["system"],
                "upgrade": row["upgrade"],
                "upgrade_id": row["upgrade_id"].strip() or None,
                "current_value": _csv_number(
                    row["current_value"], "current_value", nullable=True
                ),
                "target_value": _csv_number(
                    row["target_value"], "target_value", nullable=True
                ),
                "value_kind": row["value_kind"],
                "cost": _csv_number(row["cost"], "cost", nullable=True),
                "currency": row["currency"],
                "benefit": _csv_number(row["benefit"], "benefit", nullable=True),
            }
        )
    return _normalise_document(
        {
            "schema_version": schema_value,
            "source": source,
            "missing_inputs": missing_inputs,
            "recommendations": recommendations,
        },
        now,
    )


def _is_stale(source: Mapping[str, Any], now: float) -> bool:
    return now - float(source["account_snapshot_at"]) > STALE_AFTER_SECONDS


def _blocked_reason(
    row: Mapping[str, Any], *, stale: bool, missing_inputs: list[str]
) -> str | None:
    if missing_inputs:
        return "Source is missing required account inputs."
    if stale:
        return "Account snapshot is stale."
    if row["system"] != "workshop":
        return "Only Workshop recommendations can be staged."
    if row["currency"] != "coins":
        return "Workshop staging requires costs in coins."
    if row["value_kind"] != "stat":
        return "Workshop staging requires targets in displayed stat units."
    upgrade_id = row["upgrade_id"]
    upgrade = by_id(upgrade_id) if isinstance(upgrade_id, str) else None
    if upgrade is None:
        return "Upgrade identity is not a supported consistent standard Workshop stat."
    if upgrade.unlock:
        return "Workshop unlock rows do not have numeric stat targets."
    current = row["current_value"]
    target = row["target_value"]
    cost = row["cost"]
    if current is None or target is None or cost is None:
        return "Current value, target value, and cost are required."
    if target == current or not target_reached(upgrade.id, target, current):
        return "Target does not improve the current value."
    return None


def _public_row(
    row: Mapping[str, Any], *, stale: bool, missing_inputs: list[str]
) -> dict[str, Any]:
    result = {field: row[field] for field in _RECOMMENDATION_FIELDS}
    blocked = _blocked_reason(row, stale=stale, missing_inputs=missing_inputs)
    result["can_stage"] = blocked is None
    result["blocked_reason"] = blocked
    return result


class AdvisorStore:
    """One atomically persisted normalized import per local strategy profile."""

    def __init__(
        self,
        path: Path | None,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.path = path
        self._now = now or time.time
        self._memory: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def import_file(
        self,
        profile: str,
        filename: str,
        content: str,
    ) -> dict[str, Any]:
        """Validate and atomically replace one profile's current import."""

        valid_profile = _profile_name(profile)
        valid_filename = _text(filename, "filename", limit=255)
        if "/" in valid_filename or "\\" in valid_filename:
            raise AdvisorError("filename", "filename may not contain a path")
        if not isinstance(content, str):
            raise AdvisorError("content", "content must be a string")
        try:
            content_size = len(content.encode("utf-8"))
        except UnicodeEncodeError:
            raise AdvisorError("content", "content must be valid UTF-8 text") from None
        if content_size > MAX_FILE_BYTES:
            raise AdvisorError(
                "content", f"content may not exceed {MAX_FILE_BYTES} UTF-8 bytes"
            )
        with self._lock:
            now = self._now_value()
            suffix = Path(valid_filename).suffix.casefold()
            if suffix == ".json":
                parsed = _parse_json(content, now)
            elif suffix == ".csv":
                parsed = _parse_csv(content, now)
            else:
                raise AdvisorError("filename", "advisor imports must be .json or .csv")

            record = {
                "import_id": _revision_id(parsed),
                "imported_at": now,
                "source": parsed["source"],
                "missing_inputs": parsed["missing_inputs"],
                "recommendations": parsed["recommendations"],
            }
            imports = self._load_imports()
            updated = {**imports, valid_profile: record}
            try:
                self._save_imports(updated)
            except OSError as exc:
                raise AdvisorError("store", f"advisor import could not be saved: {exc}") from None
            return self.snapshot(valid_profile)

    def get_import(self, profile: str) -> dict[str, Any] | None:
        """Return an independent normalized stored import, without readiness fields."""

        valid_profile = _profile_name(profile)
        with self._lock:
            found = self._load_imports().get(valid_profile)
            return copy.deepcopy(found) if found is not None else None

    def snapshot(self, profile: str) -> dict[str, Any]:
        """Return the current strict public JSON snapshot for one profile."""

        valid_profile = _profile_name(profile)
        with self._lock:
            record = self.get_import(valid_profile)
            if record is None:
                return {
                    "profile": valid_profile,
                    "import_id": None,
                    "source": None,
                    "imported_at": None,
                    "missing_inputs": [],
                    "stale": False,
                    "recommendations": [],
                }
            now = self._now_value()
            source = record["source"]
            missing_inputs = record["missing_inputs"]
            stale = _is_stale(source, now)
            return {
                "profile": valid_profile,
                "import_id": record["import_id"],
                "source": copy.deepcopy(source),
                "imported_at": record["imported_at"],
                "missing_inputs": list(missing_inputs),
                "stale": stale,
                "recommendations": [
                    _public_row(row, stale=stale, missing_inputs=missing_inputs)
                    for row in record["recommendations"]
                ],
            }

    def _now_value(self) -> float:
        value = self._now()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AdvisorError("time", "clock must return Unix seconds")
        result = float(value)
        if not math.isfinite(result) or result < 0:
            raise AdvisorError("time", "clock must return finite non-negative seconds")
        return result

    def _load_imports(self) -> dict[str, dict[str, Any]]:
        if self.path is None:
            return copy.deepcopy(self._memory)
        try:
            raw = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_json_pairs,
            )
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, RecursionError) as exc:
            raise AdvisorError("store", f"advisor store is unreadable: {exc}") from None
        if (
            not isinstance(raw, Mapping)
            or set(raw) != _STORE_FIELDS
            or raw.get("schema_version") != SCHEMA_VERSION
            or not isinstance(raw.get("imports"), Mapping)
        ):
            raise AdvisorError("store", "advisor store has an invalid schema")
        imports = raw["imports"]
        try:
            return {
                _profile_name(profile): _stored_record(record)
                for profile, record in imports.items()
            }
        except AdvisorError as exc:
            if exc.field == "store":
                raise
            raise AdvisorError("store", f"advisor store is invalid: {exc}") from None

    def _save_imports(self, imports: dict[str, dict[str, Any]]) -> None:
        if self.path is None:
            self._memory = copy.deepcopy(imports)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(
                    {"schema_version": SCHEMA_VERSION, "imports": imports},
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
