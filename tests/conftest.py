"""Pytest configuration for thetowerbot tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Add the repo root to the path so tests can import modules from it
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest  # noqa: E402 - after the sys.path fix-up above

import config  # noqa: E402 - after the sys.path fix-up above

# Captured at import time, before the autouse fixture below (or anything
# else) can ever repoint config.STRATEGY_DIR. Exactly one test needs the
# real, committed directory rather than the session's fenced stand-in - see
# test_strategy_store.py::test_the_committed_default_matches_config_actions.
REAL_STRATEGY_DIR = config.STRATEGY_DIR


@pytest.fixture(scope="session", autouse=True)
def fenced_strategy_dir(tmp_path_factory: pytest.TempPathFactory):
    """Point every default-constructed StrategyStore at a throwaway
    directory for the whole test session.

    strategy.StrategyStore(), built with no directory - which is what
    tower_bot.main() does, and what any future call site defaulting one
    will do too - resolves to config.STRATEGY_DIR: the repo's real, tracked
    strategies/. tower_bot.apply_cli_overrides() PERSISTS by design (see its
    own docstring), so any test that drives main() with one of the
    overlapping flags (--interval, --auto-navigate, --max-runs,
    --affordability) writes straight into the committed profile. No test
    happens to pass one of those flags to main() today, which is why this
    has not been *observed* doing damage - but "no test happens to" is luck,
    not a guarantee, and a suite that CAN rewrite a tracked file will
    eventually do so as more tests are added. Fencing the whole session off
    the real directory turns that into a structural guarantee instead of a
    habit to remember.

    Session-scoped and autouse so it holds for every test - present and
    future - without each one opting in. The function-scoped `monkeypatch`
    fixture cannot be used at session scope, so this uses
    pytest.MonkeyPatch's own context manager instead, and stays open for the
    whole session via `yield` inside the `with`.
    """
    fenced = tmp_path_factory.mktemp("strategy_dir_fence")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "STRATEGY_DIR", fenced)
        yield fenced
