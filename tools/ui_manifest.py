"""The Python half of the UI build manifest.

Must stay byte-for-byte equivalent to web/ui/scripts/manifest.mjs: same roots,
same sort, same feed order. If you change one, change both, and the test in
tests/test_web_build.py will tell you the moment they diverge.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_ROOTS = ("app", "components", "lib", "public")
SOURCE_FILES = (
    "package.json",
    "package-lock.json",
    "next.config.ts",
    "tsconfig.json",
    "postcss.config.mjs",
)


def ui_hash(ui_root: Path) -> str:
    """SHA-256 over every source file that changes what the build contains."""
    paths: list[Path] = []
    for root in SOURCE_ROOTS:
        directory = ui_root / root
        if directory.is_dir():
            paths.extend(p for p in directory.rglob("*") if p.is_file())
    for name in SOURCE_FILES:
        candidate = ui_root / name
        if candidate.is_file():
            paths.append(candidate)

    digest = hashlib.sha256()
    for relative in sorted(p.relative_to(ui_root).as_posix() for p in paths):
        digest.update((relative + "\n").encode("utf-8"))
        digest.update((ui_root / relative).read_bytes())
    return digest.hexdigest()
