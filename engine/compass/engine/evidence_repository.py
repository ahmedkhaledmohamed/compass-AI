"""Atomic canonical evidence snapshots; vector indexes are disposable caches."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock

from compass.models.sources import Evidence


class EvidenceStorageError(RuntimeError):
    """Canonical evidence could not be loaded safely."""


class EvidenceRepository:
    """Retain immutable snapshots and atomically switch the active snapshot.

    Clearing removes active evidence, retaining history for existing citations.
    It is not a physical user-data purge.
    """

    FILE = "evidence.sqlite3"

    def __init__(self, directory: Path | None = None):
        self._lock = RLock()
        if directory:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._db = sqlite3.connect(
            str(directory / self.FILE) if directory else ":memory:",
            isolation_level=None, check_same_thread=False, timeout=30,
        )
        self._db.execute("PRAGMA foreign_keys = ON")
        try:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise EvidenceStorageError(f"Unsupported evidence database version: {version}")
                if version == 0:
                    items = self._legacy_items(directory)
                    self._db.execute(
                        "CREATE TABLE snapshots (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                    )
                    self._db.execute(
                        "CREATE TABLE active_snapshot (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                        "snapshot_id TEXT NOT NULL REFERENCES snapshots(id))"
                    )
                    self._write(items)
                    self._db.execute("PRAGMA user_version = 1")
                self._db.execute("COMMIT")
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            self._db.close()
            raise

    @staticmethod
    def _legacy_items(directory: Path | None) -> list[Evidence]:
        legacy = directory / "evidence_store.json" if directory else None
        if not legacy or not legacy.exists():
            return []
        try:
            data = json.loads(legacy.read_text())
            if not isinstance(data, list):
                raise TypeError("Expected an evidence list")
            items = []
            for raw in data:
                raw = dict(raw)
                if not raw.get("id"):
                    raise ValueError("Legacy evidence ID is missing")
                # Missing historical dates must not become "freshly ingested".
                for field in ("timestamp", "ingested_at"):
                    if not raw.get(field):
                        raw[field] = datetime.min.isoformat()
                        raw["metadata"] = dict(raw.get("metadata", {}))
                        raw["metadata"][f"legacy_{field}_unknown"] = True
                items.append(Evidence.model_validate(raw))
            if len({item.id for item in items}) != len(items):
                raise ValueError("Duplicate evidence IDs")
            return items
        except Exception as exc:
            raise EvidenceStorageError(
                "Legacy evidence could not be migrated. Restore or repair evidence_store.json; "
                "the original file has been preserved."
            ) from exc

    def _read(self, snapshot_id: str | None = None) -> tuple[str, list[Evidence]]:
        if snapshot_id is None:
            row = self._db.execute(
                "SELECT s.id, s.payload FROM snapshots s JOIN active_snapshot a "
                "ON s.id=a.snapshot_id WHERE a.singleton=1"
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT id, payload FROM snapshots WHERE id=?", (snapshot_id,)
            ).fetchone()
        if row is None:
            raise EvidenceStorageError("Evidence snapshot is missing")
        try:
            if hashlib.sha256(row[1].encode()).hexdigest() != row[0]:
                raise ValueError("Snapshot checksum mismatch")
            return row[0], [Evidence.model_validate(item) for item in json.loads(row[1])]
        except Exception as exc:
            raise EvidenceStorageError("Evidence snapshot is corrupt") from exc

    def read(self, snapshot_id: str | None = None) -> tuple[str, list[Evidence]]:
        with self._lock:
            return self._read(snapshot_id)

    def _write(self, items: list[Evidence]) -> str:
        if len({item.id for item in items}) != len(items):
            raise ValueError("Duplicate evidence IDs")
        payload = json.dumps(
            [item.model_dump(mode="json") for item in items],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        snapshot_id = hashlib.sha256(payload.encode()).hexdigest()
        self._db.execute(
            "INSERT OR IGNORE INTO snapshots(id, payload) VALUES (?, ?)", (snapshot_id, payload)
        )
        self._db.execute(
            "INSERT INTO active_snapshot VALUES (1, ?) ON CONFLICT(singleton) "
            "DO UPDATE SET snapshot_id=excluded.snapshot_id", (snapshot_id,),
        )
        return snapshot_id

    def update(self, change: Callable[[list[Evidence]], list[Evidence]]) -> None:
        """Read the latest state under the write lock, avoiding lost updates."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                _, items = self._read()
                self._write(change(items))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def upsert(self, items: list[Evidence]) -> None:
        if len({item.id for item in items}) != len(items):
            raise ValueError("Duplicate incoming evidence IDs")

        def change(current: list[Evidence]) -> list[Evidence]:
            by_id = {item.id: item for item in current}
            by_id.update({item.id: item for item in items})
            return list(by_id.values())

        self.update(change)

    def replace_source(self, source_name: str, items: list[Evidence], source_id: str = "", legacy_names: list[str] | None = None) -> None:
        """Commit a completed source import without touching another source."""
        if not source_name:
            raise ValueError("A source name is required")

        names = set(legacy_names or []) if source_id else {source_name}

        def belongs(item: Evidence) -> bool:
            if source_id and item.source_id:
                return item.source_id == source_id
            return item.source_name in names

        def change(current: list[Evidence]) -> list[Evidence]:
            retained = [item for item in current if not belongs(item)]
            retained_ids = {item.id for item in retained}
            if any(item.id in retained_ids for item in items):
                raise ValueError("Evidence ID belongs to another source")
            return retained + [item.model_copy(update={"source_name": source_name, **({"source_id": source_id} if source_id else {})}) for item in items]

        self.update(change)
