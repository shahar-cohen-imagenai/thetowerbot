"""Is the served dashboard bundle current for this checkout?

The question `run.sh` asks before it decides whether to invoke npm at all, and
again after, so a mismatch is a terminal failure with both hashes printed
rather than the runtime-gate banner discovered later in a browser.

Every case here builds its own manifest in tmp_path. Nothing reads a local
`web/static/.build-manifest.json`: whether the FastAPI app handles a missing
or generated bundle is tested independently in tests/test_web_build.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.freshness import Freshness, verdict


# Hash-shaped, and distinct in their first 8 characters: the reason strings
# abbreviate to 8 the way every other hash in this repo is reported, so test
# values that differed only later would make the assertions below vacuous.
FE = 'aaaaaaaa' + '0' * 56
FE2 = 'bbbbbbbb' + '0' * 56
BE = 'cccccccc' + '0' * 56
BE2 = 'dddddddd' + '0' * 56


def manifest(path: Path, *, frontend: str, backend: str) -> Path:
    written = path / ".build-manifest.json"
    written.write_text(json.dumps(
        {"hash": frontend, "backend_hash": backend, "built_at": 1.0}), encoding="utf-8")
    return written


def test_matching_hashes_are_fresh(tmp_path: Path) -> None:
    """The skip case: npm is never invoked, so the tree stays clean and a
    machine with no node can still start the bot."""
    written = manifest(tmp_path, frontend=FE, backend=BE)
    assert verdict(written, frontend=FE, backend=BE) == Freshness(
        fresh=True, reason=None)


def test_an_edited_dashboard_is_stale(tmp_path: Path) -> None:
    written = manifest(tmp_path, frontend=FE, backend=BE)
    result = verdict(written, frontend=FE2, backend=BE)
    assert result.fresh is False
    assert 'frontend' in result.reason
    assert FE2[:8] in result.reason and FE[:8] in result.reason
    assert 'backend' not in result.reason


def test_an_edited_backend_is_stale(tmp_path: Path) -> None:
    """The case that produced the mismatch banner: the dashboard's own sources
    are untouched, so a frontend-only check would call this fresh and launch
    straight into locked controls."""
    written = manifest(tmp_path, frontend=FE, backend=BE)
    result = verdict(written, frontend=FE, backend=BE2)
    assert result.fresh is False
    assert 'backend' in result.reason
    assert BE2[:8] in result.reason and BE[:8] in result.reason
    assert 'frontend' not in result.reason


def test_both_stale_names_both(tmp_path: Path) -> None:
    """Reporting only the first mismatch would send someone chasing one hash
    while the other was also wrong."""
    written = manifest(tmp_path, frontend=FE, backend=BE)
    result = verdict(written, frontend=FE2, backend=BE2)
    assert result.fresh is False
    assert 'frontend' in result.reason and 'backend' in result.reason


def test_a_missing_manifest_is_stale_not_an_error(tmp_path: Path) -> None:
    """A fresh clone with no build yet, and the `rm -rf web/static` case.
    Stale is the useful answer: it makes run.sh build, where raising would
    make it abort on the one situation building fixes."""
    result = verdict(tmp_path / "nope.json", frontend=FE, backend=BE)
    assert result.fresh is False
    assert 'no build manifest' in result.reason


def test_a_corrupt_manifest_is_stale_not_an_error(tmp_path: Path) -> None:
    """A half-written manifest from an interrupted build. Same reasoning as a
    missing one: rebuild, do not abort."""
    written = tmp_path / ".build-manifest.json"
    written.write_text('{"hash": "aaaaaaaa", trunca', encoding="utf-8")
    result = verdict(written, frontend=FE, backend=BE)
    assert result.fresh is False
    assert 'unreadable' in result.reason


def test_a_manifest_missing_the_backend_key_is_stale(tmp_path: Path) -> None:
    """A bundle built by a pipeline that predates the backend hash. It cannot
    be proven current, so it is not treated as current."""
    written = tmp_path / ".build-manifest.json"
    written.write_text(json.dumps({"hash": FE, "built_at": 1.0}), encoding="utf-8")
    result = verdict(written, frontend=FE, backend=BE)
    assert result.fresh is False
    assert 'backend' in result.reason


def test_an_unknown_source_hash_is_never_fresh(tmp_path: Path) -> None:
    """`runtime_identity.source_hash` returns None on an unreadable tree. None
    == None must not read as "the hashes agree" - that would call an
    unmeasurable checkout fresh and skip the build."""
    written = manifest(tmp_path, frontend=FE, backend=BE)
    assert verdict(written, frontend=None, backend=BE).fresh is False
    assert verdict(written, frontend=FE, backend=None).fresh is False
