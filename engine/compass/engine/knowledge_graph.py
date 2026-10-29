"""Canonical SQLite evidence with a rebuildable, snapshot-specific vector cache."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

import chromadb
from chromadb.config import Settings

from compass.engine.evidence_repository import EvidenceRepository
from compass.models.sources import Evidence, EvidenceStore, SourceType


class EvidenceIndexUnavailable(RuntimeError):
    """Search failed; canonical evidence remains available for index recovery."""


class KnowledgeGraph:
    """Stores and queries evidence without making vector storage authoritative."""

    def __init__(self, persist_dir: Path | None = None):
        self._persist_dir = persist_dir
        self._repository = EvidenceRepository(persist_dir)
        self._client = None
        self._index_lock = RLock()

    @property
    def store(self) -> EvidenceStore:
        return EvidenceStore(items=self._repository.read()[1])

    def add(self, evidence: Evidence) -> None:
        self.add_many([evidence])

    def add_many(self, items: list[Evidence]) -> None:
        if items:
            self._repository.upsert(items)

    def replace_source(self, source_name: str, items: list[Evidence]) -> None:
        self._repository.replace_source(source_name, items)

    def _index(self, snapshot_id: str, items: list[Evidence]):
        """Retry incomplete builds against the immutable canonical snapshot.

        Each snapshot has its own collection, so concurrent readers/builders of
        different evidence generations cannot overwrite one another's results.
        """
        with self._index_lock:
            if self._client is None:
                settings = Settings(anonymized_telemetry=False)
                if self._persist_dir:
                    self._client = chromadb.PersistentClient(
                        path=str(self._persist_dir / "vectors"), settings=settings,
                    )
                else:
                    self._client = chromadb.EphemeralClient(settings=settings)
            collection = self._client.get_or_create_collection(
                name=f"evidence_{snapshot_id[:48]}",
                metadata={"hnsw:space": "cosine", "snapshot": snapshot_id},
            )
            if collection.count() != len(items):
                for start in range(0, len(items), 128):
                    batch = items[start:start + 128]
                    collection.upsert(
                        ids=[item.id for item in batch],
                        documents=[item.content for item in batch],
                        metadatas=[{
                            "source_type": item.source_type.value,
                            "connector": item.connector,
                            "title": item.title,
                        } for item in batch],
                    )
                if collection.count() != len(items):
                    raise RuntimeError("Evidence index did not finish building")
            return collection

    def query(self, query: str, n_results: int = 10, source_type: SourceType | None = None) -> list[Evidence]:
        snapshot_id, items = self._repository.read()
        eligible = [item for item in items if source_type is None or item.source_type == source_type]
        effective_n = min(n_results, len(eligible))
        if effective_n <= 0:
            return []
        try:
            collection = self._index(snapshot_id, items)
            results = collection.query(
                query_texts=[query], n_results=effective_n,
                where={"source_type": source_type.value} if source_type else None,
            )
        except Exception as exc:
            raise EvidenceIndexUnavailable(
                "Evidence search is unavailable. Saved evidence is intact; the search index needs recovery."
            ) from exc
        by_id = {item.id: item for item in eligible}
        ids = results["ids"][0] if results["ids"] else []
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def get_by_id(self, evidence_id: str) -> Evidence | None:
        return next((item for item in self.store.items if item.id == evidence_id), None)

    def get_cross_source_evidence(self, query: str, n_per_source: int = 5) -> dict[SourceType, list[Evidence]]:
        result: dict[SourceType, list[Evidence]] = {}
        for source_type in SourceType:
            evidence = self.query(query, n_results=n_per_source, source_type=source_type)
            if evidence:
                result[source_type] = evidence
        return result

    def remove_by_connector(self, connector_name: str) -> int:
        removed = 0

        def change(items: list[Evidence]) -> list[Evidence]:
            nonlocal removed
            matching = [item for item in items if item.connector == connector_name]
            if not matching:
                matching = [item for item in items if item.source_name == connector_name]
            ids = {item.id for item in matching}
            removed = len(ids)
            return [item for item in items if item.id not in ids]

        self._repository.update(change)
        return removed

    def clear(self) -> None:
        """Empty the active snapshot, retaining history; not a physical data purge."""
        self._repository.update(lambda _: [])

    def __len__(self) -> int:
        return len(self.store)
