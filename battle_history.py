"""Read-only structural parsing for English clipboard Battle Reports.

The supported format is headed ``Battle Report`` and contains the observed
English ``label value`` rows. Numeric rows are recognized generically; tabs
can delimit arbitrary text values, while other unfamiliar lines remain raw.
Line endings are normalized for parsing and blank separator lines are omitted;
all retained entries keep their original line content. No values are converted
or mapped to account concepts.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Sequence, TextIO


class BattleReportError(ValueError):
    """The supplied text is not a supported Battle Report."""


@dataclass(frozen=True)
class BattleReportField:
    label: str | None
    raw_value: str | None
    raw_line: str


@dataclass(frozen=True)
class BattleReportSection:
    title: str
    entries: tuple[BattleReportField, ...]


@dataclass(frozen=True)
class BattleReport:
    title: str
    fields: tuple[BattleReportField, ...]
    sections: tuple[BattleReportSection, ...]


_TEXT_HEADER_LABELS = ("Battle Date", "Game Time", "Real Time", "Killed By")
_SECTION_TITLES = {"Combat", "Utility", "Enemies Destroyed", "Bots", "Guardian"}
_SINGLETON_LABELS = {"Battle Date", "Game Time", "Real Time", "Tier", "Wave", "Killed By"}
_MAX_BYTES = 256 * 1024
_MAX_LINES = 2048
_MAX_LINE_LENGTH = 4096
_TRAILING_VALUE = re.compile(
    r"^(?P<label>.+?)\s+(?P<value>\$?[+-]?(?:\d[\d,]*)(?:\.\d+)?[A-Za-z]*|x[+-]?(?:\d[\d,]*)(?:\.\d+)?|[+-]?(?:\d[\d,]*)(?:\.\d+)?%)$"
)


def _field(line: str, raw_line: str) -> BattleReportField | None:
    for label in _TEXT_HEADER_LABELS:
        prefix = f"{label} "
        if line.startswith(prefix) and line[len(prefix):].strip():
            return BattleReportField(label, line[len(prefix):].strip(), raw_line)
    if "\t" in line:
        label, raw_value = line.split("\t", 1)
        if label.strip() and raw_value.strip():
            return BattleReportField(label.strip(), raw_value.strip(), raw_line)
    match = _TRAILING_VALUE.fullmatch(line)
    if match is None:
        return None
    return BattleReportField(match.group("label"), match.group("value"), raw_line)


def parse_battle_report(text: str) -> BattleReport:
    """Parse one report while preserving observed labels, values and order."""
    if not isinstance(text, str):
        raise BattleReportError("Battle Report input must be text")
    if len(text.encode("utf-8")) > _MAX_BYTES:
        raise BattleReportError("Battle Report exceeds 256 KiB")
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(raw_lines) > _MAX_LINES:
        raise BattleReportError("Battle Report exceeds 2048 lines")
    if any(len(line) > _MAX_LINE_LENGTH for line in raw_lines):
        raise BattleReportError("Battle Report line exceeds 4096 characters")
    lines = [(line, line.strip()) for line in raw_lines if line.strip()]
    if not lines or lines[0][1] != "Battle Report":
        raise BattleReportError("Battle Report heading is missing")
    if any(line == "Battle Report" for _, line in lines[1:]):
        raise BattleReportError("Multiple Battle Reports are unsupported")

    top_fields: list[BattleReportField] = []
    section_rows: list[tuple[str, list[BattleReportField]]] = []
    current: list[BattleReportField] | None = None
    for raw_line, line in lines[1:]:
        if line in _SECTION_TITLES:
            current = []
            section_rows.append((line, current))
            continue
        field = _field(line, raw_line)
        entry = field or BattleReportField(None, None, raw_line)
        (top_fields if current is None else current).append(entry)

    labels = {field.label for field in top_fields if field.label is not None}
    if not {"Tier", "Wave"} <= labels:
        raise BattleReportError("Battle Report requires top-level Tier and Wave fields")
    all_entries = top_fields + [entry for _, entries in section_rows for entry in entries]
    for label in _SINGLETON_LABELS:
        if sum(entry.label == label for entry in all_entries) > 1:
            raise BattleReportError(f"Battle Report repeats singleton field: {label}")
    empty = [title for title, fields in section_rows if not fields]
    if empty:
        raise BattleReportError(f"Battle Report section has no fields: {empty[0]}")
    return BattleReport(
        "Battle Report",
        tuple(top_fields),
        tuple(BattleReportSection(title, tuple(fields)) for title, fields in section_rows),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect one clipboard Battle Report as structured JSON")
    parser.add_argument("path", nargs="?", default="-", help="UTF-8 report file, or - for standard input")
    return parser


def _read_input(path: str, stdin: TextIO) -> str:
    if path == "-":
        return stdin.read(_MAX_BYTES + 1)
    source = Path(path)
    if source.stat().st_size > _MAX_BYTES:
        raise BattleReportError("Battle Report exceeds 256 KiB")
    with source.open(encoding="utf-8") as handle:
        return handle.read(_MAX_BYTES + 1)


def main(argv: Sequence[str] | None = None, *, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Run the read-only JSON inspection CLI."""
    args = _parser().parse_args(argv)
    try:
        text = _read_input(args.path, stdin)
        report = parse_battle_report(text)
    except (OSError, UnicodeError, BattleReportError) as exc:
        print(f"battle-report: {exc}", file=sys.stderr)
        return 2
    json.dump(asdict(report), stdout, ensure_ascii=False, indent=2)
    stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
