"""Pytest configuration for thetowerbot tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Add the repo root to the path so tests can import modules from it
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
