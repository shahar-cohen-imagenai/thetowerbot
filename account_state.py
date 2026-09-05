"""Acknowledged permanent account facts, isolated from transient battle observations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import logging
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any

import db
import ultimate_weapons
from concepts import REGISTRY
from perception import Observation
from account_screens import ScreenReadings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evidence:
    observed_at: float
    confidence: float
    raw_name: str
    raw_value: str | None
    rect: tuple[int, int, int, int]
    frame_width: int
    frame_height: int
    frame_digest: str
    frame_ref: str | None = None


@dataclass(frozen=True)
class Fact:
    concept_id: str
    value: float | int | str | bool | None
    status: str
    evidence: Evidence


@dataclass(frozen=True)
class AccountRevision:
    revision_id: int | None = None
    parent_revision_id: int | None = None
    created_at: float | None = None
    account_id: str | None = None
    game_version: str | None = None
    registry_version: str = REGISTRY.registry_version
    workshop_stats: tuple[Fact, ...] = ()
    workshop_levels: tuple[Fact, ...] | None = None
    lab_levels: tuple[Fact, ...] | None = None
    effective_account_stats: tuple[Fact, ...] | None = None
    inventory: tuple[Fact, ...] | None = None
    unlocks: tuple[Fact, ...] | None = None
    settings: tuple[Fact, ...] | None = None
    # Kept out of the Fact sections above on purpose. A UW reading holds raw
    # stone quantities and lab-adjusted ones in separately typed collections,
    # and a flat Fact - one concept_id, one value - has nowhere to carry that
    # difference, so storing UWs there would erase it.
    ultimate_weapons: ultimate_weapons.UltimateWeaponsRecord | None = None


@dataclass(frozen=True)
class RunObservation:
    run_id: int
    observed_at: float
    effective_stats: tuple[Fact, ...]
    purchased_levels: tuple[Fact, ...] | None = None
    registry_version: str = REGISTRY.registry_version
    game_version: str | None = None


def _encoded(value: Any) -> str:
    return json.dumps(asdict(value), allow_nan=False, sort_keys=True)


class AccountRepository:
    """Short transactions on the telemetry database, independent of its lossy queue."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        conn = db.connect(self.path)
        conn.close()

    def latest(self) -> AccountRevision | None:
        with db.reader(self.path) as conn:
            row = conn.execute('SELECT id, detail FROM account_revisions ORDER BY id DESC LIMIT 1').fetchone()
        if row is None:
            return None
        value = json.loads(row['detail'])
        value['revision_id'] = row['id']
        for section in ('workshop_stats', 'workshop_levels', 'lab_levels', 'effective_account_stats',
                        'inventory', 'unlocks', 'settings'):
            if value.get(section) is not None:
                value[section] = tuple(Fact(f['concept_id'], f['value'], f['status'],
                    Evidence(**{**f['evidence'], 'rect': tuple(f['evidence']['rect'])})) for f in value[section])
        if value.get('ultimate_weapons') is not None:
            # Revalidated on the way out, so a row edited in the database
            # cannot smuggle a lab-adjusted value into a raw stone collection.
            value['ultimate_weapons'] = ultimate_weapons.decode(value['ultimate_weapons'])
        return AccountRevision(**value)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=1.)
        conn.execute('PRAGMA busy_timeout=1000')
        return conn

    def save_account(self, revision: AccountRevision, facts: tuple[Fact, ...]) -> AccountRevision:
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute('INSERT INTO account_revisions(detail) VALUES (?)', (_encoded(revision),))
                revision_id = cursor.lastrowid
                conn.execute('INSERT INTO account_observations(revision_id, detail) VALUES (?, ?)',
                             (revision_id, json.dumps([asdict(f) for f in facts], allow_nan=False)))
            return replace(revision, revision_id=revision_id)
        finally:
            conn.close()

    def save_run(self, observation: RunObservation) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute('INSERT INTO run_observations(run_id, observed_at, detail) VALUES (?, ?, ?)',
                             (observation.run_id, observation.observed_at, _encoded(observation)))
        finally:
            conn.close()


class AccountState:
    def __init__(self, repository: AccountRepository | None = None) -> None:
        self.repository = repository
        self.screen_readings = ScreenReadings()
        self._lock = threading.RLock()
        self._revision: AccountRevision | None = None
        self._error: str | None = None
        self._run_error: str | None = None
        self._restored = repository is None
        self._pending: dict[str, Fact] = {}
        self._run_id: int | None = None
        self._run_values: dict[str, float] = {}
        self._run_saved_at: float | None = None
        if repository is not None:
            try:
                self._revision = repository.latest()
                self._restored = True
            except (sqlite3.Error, ValueError, TypeError) as exc:
                self._error = str(exc)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {'persistence_available': self.repository is not None,
                    'screen_readings': self.screen_readings.snapshot(),
                    'error': self._error or self._run_error,
                    'errors': {'account': self._error, 'run': self._run_error},
                    'revision': json.loads(_encoded(self._revision)) if self._revision else None,
                    'unknown_state': json.loads(_encoded(AccountRevision())) if self._revision is None else None}

    @staticmethod
    def _facts(observation: Observation, context: str) -> tuple[Fact, ...]:
        if observation.context != context or any(row.context != context for row in observation.rows):
            raise ValueError(f'Expected {context} observation')
        if (observation.category is None or not observation.frame_digest
            or not math.isfinite(observation.observed_at)):
            return ()
        facts = []
        for row in observation.rows:
            if (row.concept_id is None or row.value is None or not math.isfinite(row.value)
                or not math.isfinite(row.confidence) or not .9 <= row.confidence <= 1
                or row.status not in ('available', 'maxed')
                or row.category != observation.category):
                continue
            rect = row.rect
            facts.append(Fact(row.concept_id, row.value, 'observed', Evidence(
                observation.observed_at, row.confidence, row.raw_name, row.raw_value,
                (rect.x, rect.y, rect.w, rect.h), observation.frame_width,
                observation.frame_height, observation.frame_digest)))
        # Duplicate identities are ambiguous, even when their values agree.
        return tuple(f for f in facts if sum(other.concept_id == f.concept_id for other in facts) == 1)

    def reset_confirmation(self) -> None:
        """Discard candidates when a screen or bot lifetime changes."""
        with self._lock:
            self._pending.clear()

    def observe_account(self, observation: Observation) -> None:
        with self._lock:
            facts = self._facts(observation, 'workshop')
            previous = self._pending
            self._pending = {f.concept_id: f for f in facts}
            if self.repository is None:
                return
            if not self._restored:
                try:
                    self._revision = self.repository.latest()
                    self._restored = True
                    self._error = None
                except (sqlite3.Error, ValueError, TypeError) as exc:
                    self._error = str(exc)
                    return
            known = {f.concept_id: f for f in self._revision.workshop_stats} if self._revision else {}
            changed = tuple(replace(f, status='verified') for f in facts
                if f.concept_id in previous and previous[f.concept_id].value == f.value
                and 0 < f.evidence.observed_at - previous[f.concept_id].evidence.observed_at <= 30
                and (f.concept_id not in known or known[f.concept_id].value != f.value))
            if not changed:
                return
            known.update({f.concept_id: f for f in changed})
            candidate = replace(self._revision or AccountRevision(), revision_id=None,
                parent_revision_id=self._revision.revision_id if self._revision else None,
                created_at=observation.observed_at,
                workshop_stats=tuple(known[k] for k in sorted(known)))
            try:
                self._revision = self.repository.save_account(candidate, changed)
                self._error = None
            except sqlite3.Error as exc:
                self._error = str(exc)
                logger.error('Account persistence failed: %s', exc)

    def observe_run(self, observation: Observation, run_id: int | None) -> None:
        with self._lock:
            facts = self._facts(observation, 'battle')
            self._pending.clear()
            if self.repository is None or run_id is None or not facts:
                return
            if run_id != self._run_id:
                self._run_id, self._run_values, self._run_saved_at = run_id, {}, None
            values = {f.concept_id: f.value for f in facts}
            if all(self._run_values.get(k) == v for k, v in values.items()):
                return
            if self._run_saved_at is not None and observation.observed_at - self._run_saved_at < 10:
                return
            try:
                self.repository.save_run(RunObservation(run_id, observation.observed_at, facts))
                self._run_values.update(values)
                self._run_saved_at = observation.observed_at
                self._run_error = None
            except sqlite3.Error as exc:
                self._run_error = str(exc)
                logger.error('Run persistence failed: %s', exc)
