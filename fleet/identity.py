"""Immutable attempt generations and evidence-backed account bindings."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class IdentityEvidence:
    account_id: str
    observed_at: float
    evidence_ref: str

    def __post_init__(self) -> None:
        if (not self.account_id.strip() or not math.isfinite(self.observed_at)
                or self.observed_at <= 0 or not self.evidence_ref.strip()):
            raise ValueError("identity evidence needs an account, observation time, and reference")


@dataclass(frozen=True)
class Attempt:
    worker_id: str
    endpoint: str
    lease_id: str
    attempt_id: str
    generation: str = field(default_factory=lambda: uuid4().hex)
    created_at: float = field(default_factory=time.time)

    @classmethod
    def new(cls, worker_id: str, endpoint: str, lease_id: str, attempt_id: str) -> Attempt:
        if not all(value.strip() for value in (worker_id, endpoint, lease_id, attempt_id)):
            raise ValueError("worker, endpoint, lease, and attempt identities are required")
        return cls(worker_id, endpoint, lease_id, attempt_id)

    def persist(self, path: Path, evidence: IdentityEvidence | None) -> None:
        """Create a binding once, after an account observation is available."""
        if evidence is None:
            raise ValueError("identity evidence is required before persisting an attempt")
        if evidence.observed_at < self.created_at:
            raise ValueError("identity evidence was observed before this attempt generation")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**asdict(self), **asdict(evidence)}
        with path.open("x", encoding="utf-8") as file:
            json.dump(payload, file, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
