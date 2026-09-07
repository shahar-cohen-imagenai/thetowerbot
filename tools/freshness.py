"""Is the built dashboard in web/static/ current for this checkout?

`run.sh` asks this before deciding whether to invoke npm, and again after
building. Two hashes have to agree, not one:

* the dashboard's own sources (`tools.ui_manifest.ui_hash`), and
* the Python tree the running backend will report
  (`runtime_identity.source_hash`), which the bundle records as the backend it
  was built against.

Checking only the first is the trap: editing Python leaves the dashboard's own
sources untouched, so a frontend-only check calls the bundle fresh and the bot
launches straight into "Runtime mismatch - controls locked".

Exit code is the interface `run.sh` reads: 0 fresh, 1 stale, with the reason on
stdout either way.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import runtime_identity
from tools.ui_manifest import ui_hash

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Freshness:
    fresh: bool
    reason: str | None


def _short(value: str | None) -> str:
    return 'unknown' if value is None else value[:8]


def verdict(manifest_path: Path, *, frontend: str | None,
            backend: str | None) -> Freshness:
    """Compare a built bundle's recorded hashes against measured ones.

    Every failure to read the manifest answers STALE rather than raising: a
    missing manifest is a fresh clone, and a truncated one is an interrupted
    build. Both are fixed by building, so the useful answer is the one that
    makes `run.sh` build - aborting would fail on exactly the situations
    building resolves.

    An unmeasurable hash (`None`, which `source_hash` returns for an
    unreadable tree) is never fresh. Without that guard `None == None` would
    read as "the hashes agree" and skip the build on the one checkout nobody
    can vouch for.
    """
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Freshness(False, f'no build manifest at {manifest_path}')
    except (OSError, ValueError, json.JSONDecodeError):
        return Freshness(False, f'unreadable build manifest at {manifest_path}')
    if not isinstance(raw, dict):
        return Freshness(False, f'unreadable build manifest at {manifest_path}')

    recorded_frontend = raw.get('hash')
    recorded_backend = raw.get('backend_hash')

    # Both mismatches are reported, never just the first: naming one while the
    # other is also wrong sends someone chasing half the problem.
    stale: list[str] = []
    if not isinstance(recorded_frontend, str) or frontend is None \
            or recorded_frontend != frontend:
        stale.append(f'frontend sources {_short(frontend)} != bundle '
                     f'{_short(recorded_frontend if isinstance(recorded_frontend, str) else None)}')
    if not isinstance(recorded_backend, str) or backend is None \
            or recorded_backend != backend:
        stale.append(f'backend tree {_short(backend)} != bundle expects '
                     f'{_short(recorded_backend if isinstance(recorded_backend, str) else None)}')
    if stale:
        return Freshness(False, '; '.join(stale))
    return Freshness(True, None)


def measure(root: Path = PROJECT_ROOT) -> Freshness:
    """The verdict for this checkout, measuring both hashes off the tree."""
    return verdict(
        root / 'web' / 'static' / '.build-manifest.json',
        frontend=ui_hash(root / 'web' / 'ui'),
        backend=runtime_identity.source_hash(root),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    result = measure(args.root)
    print('fresh' if result.fresh else f'stale: {result.reason}')
    raise SystemExit(0 if result.fresh else 1)
