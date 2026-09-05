"""Battle Report parser contract.

The field spelling and section shape are adapted from a real player clipboard
report published at https://gall.dcinside.com/mgallery/board/view/?id=thetower&no=10472
(accessed 2026-09-05). Values are deliberately shortened and changed. The
source page is public evidence of the game-produced format, not copied code.
"""
from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
import subprocess
import sys

import pytest

from battle_history import BattleReportError, main, parse_battle_report


REPORT = """Battle Report
Battle Date Oct 26, 2025 10:27
Game Time 1d 2h 7m 52s
Real Time 5h 19m 56s
Tier 14
Wave 4124
Killed By Scatter
Coins earned 1.02q
Combat
Damage dealt 4.28D
Damage Gain From Berserk x8.00
Utility
Waves Skipped 1018
Future Metric opaque text
Future Section
Future Count 17
"""


def test_preserves_sections_fields_order_labels_and_raw_values() -> None:
    report = parse_battle_report(REPORT)

    assert report.title == "Battle Report"
    assert [(field.label, field.raw_value) for field in report.fields] == [
        ("Battle Date", "Oct 26, 2025 10:27"),
        ("Game Time", "1d 2h 7m 52s"),
        ("Real Time", "5h 19m 56s"),
        ("Tier", "14"),
        ("Wave", "4124"),
        ("Killed By", "Scatter"),
        ("Coins earned", "1.02q"),
    ]
    assert [section.title for section in report.sections] == ["Combat", "Utility"]
    assert [(field.label, field.raw_value) for field in report.sections[0].entries] == [
        ("Damage dealt", "4.28D"),
        ("Damage Gain From Berserk", "x8.00"),
    ]
    assert report.sections[1].entries[-3].raw_line == "Future Metric opaque text"
    assert report.sections[1].entries[-3].label is None
    assert report.sections[1].entries[-2].raw_line == "Future Section"
    assert report.sections[1].entries[-2].label is None


@pytest.mark.parametrize("text", ["", "Tier 4\nWave 100", "Battle Report\nCombat"])
def test_malformed_reports_raise_a_specific_error(text: str) -> None:
    with pytest.raises(BattleReportError):
        parse_battle_report(text)


def test_module_cli_emits_the_preserved_structure() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "battle_history", "-"],
        input=REPORT,
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["fields"][4] == {
        "label": "Wave", "raw_value": "4124", "raw_line": "Wave 4124"
    }
    assert payload["sections"][-1]["entries"][-1] == {
        "label": "Future Count", "raw_value": "17", "raw_line": "Future Count 17"
    }


@pytest.mark.parametrize("extra", [
    "\nTier 15",
    "\nBattle Report\nTier 2\nWave 3",
])
def test_duplicate_identity_and_multiple_reports_are_rejected(extra: str) -> None:
    with pytest.raises(BattleReportError):
        parse_battle_report(REPORT + extra)


@pytest.mark.parametrize("text", [
    "Battle Report\nTier 1\nWave 2\n" + "x" * 4097,
    "Battle Report\nTier 1\nWave 2\n" + "\n".join("x" for _ in range(2049)),
    "Battle Report\nTier 1\nWave 2\n" + "x" * (256 * 1024),
])
def test_input_bounds_fail_closed(text: str) -> None:
    with pytest.raises(BattleReportError):
        parse_battle_report(text)


def test_tab_fields_accept_text_and_timestamps_without_guessing_semantics() -> None:
    report = parse_battle_report(
        "Battle Report\nTier\t4\nWave\t500\nCaptured At\t2025-10-26T10:27:00Z"
    )

    assert [(entry.label, entry.raw_value) for entry in report.fields] == [
        ("Tier", "4"),
        ("Wave", "500"),
        ("Captured At", "2025-10-26T10:27:00Z"),
    ]


def test_header_prefix_lookalike_is_not_the_tier_identity() -> None:
    report = parse_battle_report("Battle Report\nTier 4\nTier Bonus 7\nWave 500")

    assert [(entry.label, entry.raw_value) for entry in report.fields][-2] == (
        "Tier Bonus", "7"
    )


def test_raw_lines_keep_whitespace_while_blank_separators_are_omitted() -> None:
    report = parse_battle_report(
        " Battle Report \r\nTier 4\r\nWave 500\r\nUtility\r\n\r\n  Future prose  \r\n"
    )

    assert report.sections[0].entries[0].raw_line == "  Future prose  "
    assert len(report.sections[0].entries) == 1


@pytest.mark.parametrize("stdin_text", [
    "not a report",
    "Battle Report\nTier 1\nWave 2\n" + "x" * (256 * 1024),
])
def test_cli_rejects_bad_stdin_without_json(
    stdin_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    stdout = StringIO()

    assert main(["-"], stdin=StringIO(stdin_text), stdout=stdout) == 2
    assert stdout.getvalue() == ""
    assert capsys.readouterr().err.startswith("battle-report:")


@pytest.mark.parametrize("kind", ["missing", "oversize", "non_utf8"])
def test_cli_rejects_bad_files_without_json(
    kind: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "report.txt"
    if kind == "oversize":
        source.write_bytes(b"x" * (256 * 1024 + 1))
    elif kind == "non_utf8":
        source.write_bytes(b"\xff\xfe")
    stdout = StringIO()

    assert main([str(source)], stdout=stdout) == 2
    assert stdout.getvalue() == ""
    assert capsys.readouterr().err.startswith("battle-report:")
