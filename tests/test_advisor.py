"""Strict local imports for Effective Paths-style advisory recommendations."""

from __future__ import annotations

import csv
import concurrent.futures
import io
import json
import os
import threading

import pytest

from advisor import AdvisorError, AdvisorStore


NOW = 2_000_000


def recommendation(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "health-1",
        "path": "health",
        "system": "workshop",
        "upgrade": "Health",
        "upgrade_id": "health",
        "current_value": 100,
        "target_value": 120,
        "value_kind": "stat",
        "cost": 50,
        "currency": "coins",
        "benefit": 1.2,
    }
    row.update(over)
    return row


def document(*rows: dict[str, object], **over: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "schema_version": 1,
        "source": {
            "name": "Effective Paths normalized export",
            "version": "2026.09",
            "account_name": "Tower One",
            "exported_at": NOW - 60,
            "account_snapshot_at": NOW - 120,
            "url": "https://example.com/export",
        },
        "missing_inputs": [],
        "recommendations": list(rows or (recommendation(),)),
    }
    raw.update(over)
    return raw


def csv_content(raw: dict[str, object]) -> str:
    source = raw["source"]
    assert isinstance(source, dict)
    rows = raw["recommendations"]
    assert isinstance(rows, list)
    fields = [
        "schema_version",
        "source_name",
        "source_version",
        "source_url",
        "account_name",
        "exported_at",
        "account_snapshot_at",
        "missing_inputs",
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
    ]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        assert isinstance(row, dict)
        writer.writerow(
            {
                "schema_version": raw["schema_version"],
                "source_name": source["name"],
                "source_version": source["version"],
                "source_url": source.get("url", ""),
                "account_name": source["account_name"],
                "exported_at": source["exported_at"],
                "account_snapshot_at": source["account_snapshot_at"],
                "missing_inputs": ";".join(raw["missing_inputs"]),
                **row,
            }
        )
    return stream.getvalue()


def store(path=None, *, now: int = NOW) -> AdvisorStore:
    return AdvisorStore(path, now=lambda: now)


def test_empty_snapshot_has_the_exact_plain_json_contract() -> None:
    assert store().snapshot("default") == {
        "profile": "default",
        "import_id": None,
        "source": None,
        "imported_at": None,
        "missing_inputs": [],
        "stale": False,
        "recommendations": [],
    }


def test_json_import_maps_a_supported_row_and_returns_independent_data() -> None:
    advisor = store()
    snapshot = advisor.import_file("default", "effective-paths.json", json.dumps(document()))
    assert snapshot["profile"] == "default"
    assert isinstance(snapshot["import_id"], str) and len(snapshot["import_id"]) == 64
    assert snapshot["imported_at"] == NOW
    assert snapshot["stale"] is False
    row = snapshot["recommendations"][0]
    assert row["upgrade_id"] == "health"
    assert row["can_stage"] is True
    assert row["blocked_reason"] is None
    snapshot["recommendations"].clear()
    assert advisor.snapshot("default")["recommendations"]


def test_normalized_csv_and_json_have_identical_content_revisions() -> None:
    raw = document()
    json_store = store()
    csv_store = store()
    json_snapshot = json_store.import_file("mine", "advice.json", json.dumps(raw))
    csv_snapshot = csv_store.import_file("mine", "advice.csv", csv_content(raw))
    assert csv_snapshot == json_snapshot


@pytest.mark.parametrize(
    "row,reason_fragment",
    [
        (recommendation(system="lab"), "Workshop"),
        (recommendation(currency="stones"), "coins"),
        (recommendation(value_kind="level"), "stat"),
        (recommendation(upgrade="Mystery", upgrade_id=None), "supported"),
        (recommendation(upgrade="Mystery", upgrade_id="health"), "identity"),
        (recommendation(upgrade="Damage", upgrade_id="health"), "identity"),
        (recommendation(current_value=None), "required"),
        (recommendation(target_value=100), "improve"),
        (
            recommendation(
                upgrade="Unlock Thorns",
                upgrade_id="unlock_thorns",
                current_value=0,
                target_value=1,
            ),
            "unlock",
        ),
    ],
)
def test_unsupported_rows_remain_visible_but_cannot_stage(
    row: dict[str, object], reason_fragment: str
) -> None:
    snapshot = store().import_file("mine", "advice.json", json.dumps(document(row)))
    result = snapshot["recommendations"][0]
    assert result["can_stage"] is False
    assert reason_fragment.casefold() in result["blocked_reason"].casefold()


def test_decreasing_timer_target_is_direction_aware() -> None:
    improving = recommendation(
        upgrade="Wall Rebuild",
        upgrade_id="wall_rebuild",
        current_value=120,
        target_value=100,
    )
    worse = {**improving, "id": "timer-2", "target_value": 140}
    snapshot = store().import_file(
        "mine", "advice.json", json.dumps(document(improving, worse))
    )
    assert [row["can_stage"] for row in snapshot["recommendations"]] == [True, False]


def test_missing_inputs_and_age_block_every_otherwise_supported_row() -> None:
    missing = document(missing_inputs=["Workshop levels", "Cards"])
    missing_snapshot = store().import_file("mine", "missing.json", json.dumps(missing))
    assert missing_snapshot["recommendations"][0]["can_stage"] is False
    assert "missing" in missing_snapshot["recommendations"][0]["blocked_reason"].casefold()

    stale_raw = document()
    stale_raw["source"]["account_snapshot_at"] = NOW - 86_401
    stale_raw["source"]["exported_at"] = NOW - 60
    stale_snapshot = store().import_file("mine", "stale.json", json.dumps(stale_raw))
    assert stale_snapshot["stale"] is True
    assert "stale" in stale_snapshot["recommendations"][0]["blocked_reason"].casefold()


def test_staleness_is_recomputed_from_account_snapshot_time() -> None:
    raw = document()
    advisor = AdvisorStore(None, now=lambda: NOW)
    advisor.import_file("mine", "advice.json", json.dumps(raw))
    advisor._now = lambda: NOW + 86_400
    assert advisor.snapshot("mine")["stale"] is True


@pytest.mark.parametrize(
    "mutate,field",
    [
        (lambda raw: raw.update(schema_version=2), "schema_version"),
        (lambda raw: raw.update(extra=True), "extra"),
        (lambda raw: raw.update(recommendations=[recommendation(), recommendation()]), "id"),
        (lambda raw: raw["source"].update(exported_at=NOW + 1), "exported_at"),
        (
            lambda raw: raw["source"].update(
                account_snapshot_at=NOW - 10, exported_at=NOW - 20
            ),
            "account_snapshot_at",
        ),
        (lambda raw: raw["source"].update(url="http://example.com"), "url"),
        (lambda raw: raw["recommendations"][0].update(cost=-1), "cost"),
        (lambda raw: raw["recommendations"][0].update(benefit=float("nan")), "benefit"),
        (lambda raw: raw["recommendations"][0].update(path="speed"), "path"),
        (lambda raw: raw["recommendations"][0].update(extra=True), "extra"),
    ],
)
def test_json_validation_is_strict_and_names_the_field(mutate, field: str) -> None:
    raw = document()
    mutate(raw)
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.json", json.dumps(raw))
    assert caught.value.field == field


def test_limits_reject_oversize_files_and_too_many_rows() -> None:
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.json", " " * (256 * 1024 + 1))
    assert caught.value.field == "content"
    raw = document(*[recommendation(id=f"r-{index}") for index in range(201)])
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.json", json.dumps(raw))
    assert caught.value.field == "recommendations"


def test_file_limit_counts_utf8_bytes_and_strings_are_bounded() -> None:
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.json", "é" * (MAX_CHARS := 131_073))
    assert MAX_CHARS < 256 * 1024
    assert caught.value.field == "content"

    raw = document(recommendation(upgrade="x" * 257, upgrade_id=None))
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.json", json.dumps(raw))
    assert caught.value.field == "upgrade"


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_value", True),
        ("target_value", float("inf")),
        ("cost", float("-inf")),
    ],
)
def test_numeric_fields_reject_booleans_and_non_finite_values(
    field: str, value: object
) -> None:
    raw = document(recommendation(**{field: value}))
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.json", json.dumps(raw))
    assert caught.value.field == field


def test_json_overflowing_exponent_is_rejected_as_a_non_finite_field() -> None:
    content = json.dumps(document()).replace('"cost": 50', '"cost": 1e999')
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.json", content)
    assert caught.value.field == "cost"


def test_huge_json_integer_is_a_validation_error_instead_of_an_overflow() -> None:
    raw = document(recommendation(cost=10**400))
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.json", json.dumps(raw))
    assert caught.value.field == "cost"


def test_json_decoder_limits_and_malformed_https_urls_stay_in_error_contract() -> None:
    huge_literal = json.dumps(document()).replace('"cost": 50', '"cost": ' + "1" * 5000)
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "huge.json", huge_literal)
    assert caught.value.field == "content"

    deeply_nested = "[" * 2000 + "]" * 2000
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "deep.json", deeply_nested)
    assert caught.value.field == "content"

    raw = document()
    raw["source"]["url"] = "https://["
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "url.json", json.dumps(raw))
    assert caught.value.field == "url"


def test_csv_rejects_formulas_duplicate_headers_and_inconsistent_metadata() -> None:
    raw = document()
    formula = csv_content(raw).replace("Health,health", "   =1+1,health")
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.csv", formula)
    assert caught.value.field == "content"

    duplicate_header = csv_content(raw).replace("id,path", "id,id,path", 1)
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.csv", duplicate_header)
    assert caught.value.field == "content"

    two = document(recommendation(), recommendation(id="health-2"))
    inconsistent = csv_content(two).replace("2026.09", "2026.09-other", 1)
    with pytest.raises(AdvisorError) as caught:
        store().import_file("mine", "advice.csv", inconsistent)
    assert caught.value.field == "source_version"


def test_failed_import_preserves_previous_memory_value() -> None:
    advisor = store()
    before = advisor.import_file("mine", "good.json", json.dumps(document()))
    with pytest.raises(AdvisorError):
        advisor.import_file("mine", "bad.json", "not json")
    assert advisor.snapshot("mine") == before


def test_escaped_lone_surrogate_is_rejected_before_hashing_and_preserves_import() -> None:
    advisor = store()
    before = advisor.import_file("mine", "good.json", json.dumps(document()))
    raw = document()
    raw["source"]["name"] = "\ud800"
    with pytest.raises(AdvisorError) as caught:
        advisor.import_file("mine", "surrogate.json", json.dumps(raw))
    assert caught.value.field == "name"
    assert advisor.snapshot("mine") == before


def test_file_store_persists_profiles_in_isolation(tmp_path) -> None:
    path = tmp_path / "advisor.json"
    advisor = store(path)
    mine = advisor.import_file("mine", "mine.json", json.dumps(document()))
    other_raw = document(recommendation(id="damage-1", upgrade="Damage", upgrade_id="damage"))
    other = advisor.import_file("other", "other.json", json.dumps(other_raw))
    reopened = store(path)
    assert reopened.snapshot("mine") == mine
    assert reopened.snapshot("other") == other
    assert reopened.get_import("absent") is None


def test_concurrent_profile_imports_serialize_the_whole_read_modify_write(
    tmp_path, monkeypatch
) -> None:
    advisor = store(tmp_path / "advisor.json")
    real_save = advisor._save_imports
    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def paused_save(imports) -> None:
        nonlocal calls
        with calls_lock:
            calls += 1
            call = calls
        if call == 1:
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        real_save(imports)

    monkeypatch.setattr(advisor, "_save_imports", paused_save)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            advisor.import_file, "mine", "mine.json", json.dumps(document())
        )
        assert first_entered.wait(2)
        second = pool.submit(
            advisor.import_file,
            "other",
            "other.json",
            json.dumps(document(recommendation(id="other"))),
        )
        overlapped = second_entered.wait(0.1)
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert overlapped is False
    assert advisor.get_import("mine") is not None
    assert advisor.get_import("other") is not None


def test_failed_atomic_replace_preserves_the_previous_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    advisor = store(path)
    advisor.import_file("mine", "good.json", json.dumps(document()))
    before = path.read_text()

    def fail_replace(*args, **kwargs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(AdvisorError) as caught:
        advisor.import_file(
            "other", "other.json", json.dumps(document(recommendation(id="other")))
        )
    assert caught.value.field == "store"
    assert path.read_text() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["advisor.json"]
    assert advisor.snapshot("mine")["recommendations"][0]["id"] == "health-1"


@pytest.mark.parametrize("corruption", ["shape", "revision"])
def test_corrupt_stored_records_fail_as_advisor_errors(tmp_path, corruption: str) -> None:
    path = tmp_path / "advisor.json"
    advisor = store(path)
    advisor.import_file("mine", "good.json", json.dumps(document()))
    raw = json.loads(path.read_text())
    record = raw["imports"]["mine"]
    if corruption == "shape":
        del record["recommendations"][0]["id"]
    else:
        record["recommendations"][0]["target_value"] = 999
    path.write_text(json.dumps(raw))
    with pytest.raises(AdvisorError) as caught:
        advisor.snapshot("mine")
    assert caught.value.field == "store"


@pytest.mark.parametrize("profile", ["", "../escape", "has space", "x" * 65])
def test_profile_names_are_safe_before_becoming_store_keys(profile: str) -> None:
    with pytest.raises(AdvisorError) as caught:
        store().snapshot(profile)
    assert caught.value.field == "profile"
