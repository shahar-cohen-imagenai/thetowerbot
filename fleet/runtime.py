"""Per-worker writable roots and collision validation."""

from __future__ import annotations

import re
import fcntl
import hashlib
import os
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


class RuntimeIsolationError(ValueError):
    """Another live worker owns the requested root or web port."""


@dataclass(frozen=True)
class WorkerRuntime:
    worker_id: str
    root: Path
    db_path: Path
    strategy_root: Path
    evidence_root: Path
    checkpoint_root: Path
    web_port: int

    @classmethod
    def for_worker(cls, fleet_root: Path, worker_id: str, web_port: int) -> WorkerRuntime:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", worker_id):
            raise ValueError("worker id must be a simple path component")
        if not 1 <= web_port <= 65535:
            raise ValueError("web port is out of range")
        root = (Path(fleet_root).resolve() / worker_id).resolve()
        return cls(
            worker_id, root, root / "tower_bot.db", root / "strategies",
            root / "evidence", root / "checkpoints", web_port,
        )

    def writable_paths(self) -> set[Path]:
        return {
            self.db_path.resolve(), self.strategy_root.resolve(),
            self.evidence_root.resolve(), self.checkpoint_root.resolve(),
        }

    def ensure_directories(self) -> None:
        for directory in (self.root, self.strategy_root, self.evidence_root,
                          self.checkpoint_root):
            directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def reserve(self, endpoint: str | None = None) -> Iterator[None]:
        """Hold process locks for this worker root, dashboard port, and ADB endpoint."""
        self.root.mkdir(parents=True, exist_ok=True)
        port_dir = Path(tempfile.gettempdir()) / f"thetowerbot-fleet-locks-{os.getuid()}"
        port_dir.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            locks = [
                (self.root / ".worker.lock", "runtime path"),
                (port_dir / f"{self.web_port}.lock", "web port"),
            ]
            if endpoint is not None:
                host, separator, port = endpoint.rpartition(":")
                if separator and host in {"localhost", "::1"}:
                    endpoint = f"127.0.0.1:{port}"
                digest = hashlib.sha256(endpoint.encode()).hexdigest()
                locks.append((port_dir / f"adb-{digest}.lock", "ADB endpoint"))
            for path, label in locks:
                handle = stack.enter_context(path.open("a+"))
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeIsolationError(
                        f"identity incident: {label} already reserved"
                    ) from None
            yield


def validate_isolation(runtimes: Iterable[WorkerRuntime]) -> None:
    seen_ports: set[int] = set()
    seen_roots: list[Path] = []
    for runtime in runtimes:
        if runtime.web_port in seen_ports:
            raise ValueError(f"web port {runtime.web_port} is shared")
        seen_ports.add(runtime.web_port)
        root = runtime.root.resolve()
        if any(root == other or root.is_relative_to(other) or other.is_relative_to(root)
               for other in seen_roots):
            raise ValueError(f"runtime path {root} is shared")
        seen_roots.append(root)
