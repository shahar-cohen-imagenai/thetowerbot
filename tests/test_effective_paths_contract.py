"""Native source identity is separate from calculation and spending authority."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

import effective_paths_contract as contract


def workbook(*, formula: str = "A1+1", label: str = "Upgrade", sheet: str = "Path", cache: str = "2", version: str = "v1") -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="VERSION">{version}</definedName></definedNames></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:B2"/><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{label}</t></is></c></row><row r="2"><c r="B2"><f>{formula}</f><v>{cache}</v></c></row></sheetData></worksheet>')
    return stream.getvalue()


def test_formula_cache_does_not_change_structural_revision() -> None:
    assert contract.inspect_workbook(workbook(cache="2")) == contract.inspect_workbook(workbook(cache="999"))


@pytest.mark.parametrize("change", [{"formula": "A1+2"}, {"label": "Other"}, {"sheet": "Renamed"}, {"version": "v2"}])
def test_source_changes_change_fingerprint(change: dict[str, str]) -> None:
    assert contract.inspect_workbook(workbook()) != contract.inspect_workbook(workbook(**change))


def test_unknown_native_workbook_cannot_stage() -> None:
    from advisor import inspect_native_workbooks

    result = inspect_native_workbooks(workbook(), workbook())
    assert result["contract_known"] is False
    assert result["can_stage"] is False
    assert result["blockers"]


@pytest.mark.parametrize("content", [b"", b"{}", b"PKnot-a-workbook"])
def test_malformed_sources_have_actionable_error(content: bytes) -> None:
    with pytest.raises(contract.WorkbookContractError, match="XLSX"):
        contract.inspect_workbook(content)


def test_manifest_has_actual_native_boundaries() -> None:
    pinned = contract.load_contract()
    assert pinned["workbooks"]["effective_paths"]["version"] == "v5.10.03.00"
    assert pinned["workbooks"]["ids_master"]["version"] == "v4.2.3"
    assert len(pinned["workbooks"]["effective_paths"]["fingerprint"]["sheets"]) == 31
    assert pinned["ids_import"]["range"] == "_IDS!A1:DN250"
    assert {path["currency"] for path in pinned["paths"]} == {"time", "stones", "coins", "keys"}
    assert pinned["unsupported_paths"][0]["system"] == "workshop"
    assert all("system" not in path for path in pinned["paths"])


def test_known_structure_does_not_grant_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    from advisor import inspect_native_workbooks

    pinned = contract.load_contract()
    for item in pinned["workbooks"].values():
        item["fingerprint"] = contract.inspect_workbook(workbook())
    monkeypatch.setattr(contract, "load_contract", lambda: pinned)
    result = inspect_native_workbooks(workbook(), workbook())
    assert result["contract_known"] is True
    assert result["can_stage"] is False
    assert any("calculation" in blocker for blocker in result["blockers"])


def test_changed_ids_blocks_an_otherwise_known_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = contract.load_contract()
    for item in pinned["workbooks"].values():
        item["fingerprint"] = contract.inspect_workbook(workbook())
    monkeypatch.setattr(contract, "load_contract", lambda: pinned)
    result = contract.validate_native_workbooks(workbook(), workbook(formula="A1+99"))
    assert result["contract_known"] is False
    assert result["can_stage"] is False
    assert any("ids_master" in blocker for blocker in result["blockers"])


def test_expanded_archive_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contract, "MAX_EXPANDED_BYTES", 10)
    with pytest.raises(contract.WorkbookContractError, match="expanded size"):
        contract.inspect_workbook(workbook())


def test_validation_range_change_is_detected() -> None:
    original = workbook()
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(result, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                data = data.replace(b"</worksheet>", b'<dataValidations count="1"><dataValidation sqref="A1:A99" type="whole"><formula1>0</formula1></dataValidation></dataValidations></worksheet>')
            target.writestr(name, data)
    assert contract.inspect_workbook(original) != contract.inspect_workbook(result.getvalue())


def test_pinned_contract_changes_runtime_identity(tmp_path: Path) -> None:
    from runtime_identity import source_hash

    target = tmp_path / "contracts" / contract.CONTRACT_PATH.name
    target.parent.mkdir()
    target.write_text('{"contract_schema_version": 1}')
    before = source_hash(tmp_path)
    assert before is not None
    target.write_text('{"contract_schema_version": 2}')
    assert source_hash(tmp_path) != before
