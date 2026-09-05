"""Read-only identity checks for the pinned public Effective Paths/IDS exports.

This contract recognizes an exact formula/constant layout, not a recalculated
player workbook. Formula caches are deliberately excluded from identity: neither
their presence nor a matching fingerprint is evidence of calculation freshness.
No spreadsheet code is evaluated and no external references are fetched.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


CONTRACT_PATH = Path(__file__).with_name("contracts") / "effective-paths-v5.10.03.00-ids-v4.2.3.json"
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class WorkbookContractError(ValueError):
    """A source cannot be inspected as a bounded, native XLSX workbook."""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _xml(archive: zipfile.ZipFile, path: str) -> ET.Element:
    raw = archive.read(path)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise WorkbookContractError("XLSX XML declarations are unsupported")
    return ET.fromstring(raw)


def _tree(node: ET.Element) -> list[Any]:
    return [node.tag, sorted(node.attrib.items()), node.text or "", [_tree(child) for child in node]]


def inspect_workbook(content: bytes) -> dict[str, Any]:
    """Fingerprint sheet topology, constants, formulas, names and validation.

    Values in formula cells are cached calculations and do not enter the hash.
    Constants (including template inputs) do: personalized sources are rejected
    conservatively until a separately qualified mutable-input adapter exists.
    """
    if not isinstance(content, bytes) or not content or len(content) > MAX_ARCHIVE_BYTES:
        raise WorkbookContractError("XLSX content must be nonempty bytes below 25 MiB")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise WorkbookContractError("XLSX expanded size exceeds inspection limits")
            if len({entry.filename for entry in entries}) != len(entries):
                raise WorkbookContractError("XLSX contains duplicate archive members")
            book = _xml(archive, "xl/workbook.xml")
            rels = _xml(archive, "xl/_rels/workbook.xml.rels")
            targets = {node.attrib["Id"]: node.attrib.get("Target", "") for node in rels if node.attrib.get("TargetMode") != "External"}
            strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = ["".join(node.itertext()) for node in _xml(archive, "xl/sharedStrings.xml")]
            sheets = []
            seen: set[str] = set()
            for sheet in book.findall(f"{_NS}sheets/{_NS}sheet"):
                name = sheet.attrib["name"]
                if name in seen:
                    raise WorkbookContractError("XLSX contains duplicate sheet names")
                seen.add(name)
                target = targets[sheet.attrib[f"{_REL}id"]]
                path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
                if not path.startswith("xl/"):
                    raise WorkbookContractError("XLSX worksheet target is invalid")
                root = _xml(archive, path)
                cells = []
                for cell in root.findall(f"{_NS}sheetData/{_NS}row/{_NS}c"):
                    formula = cell.find(f"{_NS}f")
                    attrs = dict(cell.attrib)
                    attrs.pop("s", None)  # Formatting indexes vary between exports.
                    if formula is not None:
                        attrs.pop("t", None)  # Cached result type is also not identity.
                        value: object = _tree(formula)
                    elif attrs.get("t") == "s":
                        index = int(cell.findtext(f"{_NS}v", "-1"))
                        if index < 0 or index >= len(strings):
                            raise WorkbookContractError("XLSX shared string index is invalid")
                        attrs["t"] = "text"
                        value = strings[index]
                    elif attrs.get("t") == "inlineStr":
                        attrs["t"] = "text"
                        value = "".join(cell.find(f"{_NS}is").itertext())
                    else:
                        value = cell.findtext(f"{_NS}v")
                    cells.append([attrs, value])
                structure = [_tree(node) for node in root if node.tag in {f"{_NS}dimension", f"{_NS}mergeCells", f"{_NS}dataValidations"}]
                sheets.append({"name": name, "state": sheet.attrib.get("state", "visible"), "cell_count": len(cells), "cells_sha256": _digest(cells), "structure_sha256": _digest(structure)})
            if not sheets:
                raise WorkbookContractError("XLSX has no worksheets")
            names = book.find(f"{_NS}definedNames")
            return {"sheets": sheets, "defined_names_sha256": _digest(_tree(names) if names is not None else []), "defined_name_count": len(names) if names is not None else 0}
    except WorkbookContractError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, AttributeError, RuntimeError, OSError) as exc:
        raise WorkbookContractError(f"Invalid native XLSX structure: {type(exc).__name__}") from exc


def load_contract() -> dict[str, Any]:
    """Return a fresh machine-readable copy of the reviewed source contract."""
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def validate_native_workbooks(effective_paths: bytes, ids_master: bytes) -> dict[str, Any]:
    """Identify both revisions; report mismatches without enabling execution."""
    pinned = load_contract()
    mismatches: list[str] = []
    revisions = {}
    for kind, content in (("effective_paths", effective_paths), ("ids_master", ids_master)):
        expected = pinned["workbooks"][kind]
        actual = inspect_workbook(content)
        if actual != expected["fingerprint"]:
            mismatches.append(f"{kind}: unknown or changed workbook layout, constants or formulas")
        else:
            revisions[kind] = expected["version"]
    return {
        "contract_id": pinned["contract_id"],
        "contract_known": not mismatches,
        "revisions": revisions,
        "can_stage": False,
        "blockers": mismatches + list(pinned["execution_blockers"]),
    }
