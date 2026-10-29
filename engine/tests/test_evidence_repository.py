"""Recovery, migration and isolation checks for canonical evidence."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import Mock

import pytest

from compass.engine.evidence_repository import EvidenceRepository, EvidenceStorageError
from compass.engine.knowledge_graph import EvidenceIndexUnavailable, KnowledgeGraph
from compass.models.sources import Evidence, SourceType


def evidence(id="one", source="docs", content="original"):
    return Evidence(id=id, source_type=SourceType.DOCS, connector="docs",
                    source_name=source, title=id, content=content)


def test_snapshot_history_and_restart(tmp_path):
    repo = EvidenceRepository(tmp_path)
    repo.upsert([evidence()])
    original = repo.read()[0]
    repo.upsert([evidence(content="revised")])
    repo.update(lambda _: [])
    restarted = EvidenceRepository(tmp_path)
    assert restarted.read()[1] == []
    assert restarted.read(original)[1][0].content == "original"


def test_failed_commit_rolls_back_snapshot_and_active_pointer(tmp_path):
    repo = EvidenceRepository(tmp_path)
    repo.upsert([evidence()])
    before = repo.read()
    db = sqlite3.connect(tmp_path / repo.FILE)
    db.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON active_snapshot "
               "BEGIN SELECT RAISE(ABORT, 'simulated disk write failure'); END")
    db.close()
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert([evidence(content="lost write")])
    assert repo.read() == before
    assert EvidenceRepository(tmp_path).read() == before


def test_concurrent_writers_keep_both_sources(tmp_path):
    repositories = [EvidenceRepository(tmp_path), EvidenceRepository(tmp_path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(repo.replace_source, str(i), [evidence(str(i))])
                   for i, repo in enumerate(repositories)]
        for future in futures:
            future.result()
    assert {item.source_name for item in repositories[0].read()[1]} == {"0", "1"}


def test_source_replacement_does_not_remove_other_same_type_source(tmp_path):
    repo = EvidenceRepository(tmp_path)
    repo.replace_source("one", [evidence("a")])
    repo.replace_source("two", [evidence("b")])
    repo.replace_source("one", [evidence("c")])
    assert {item.id for item in repo.read()[1]} == {"b", "c"}
    with pytest.raises(ValueError, match="another source"):
        repo.replace_source("one", [evidence("b")])
    with pytest.raises(ValueError, match="Duplicate"):
        repo.replace_source("one", [evidence("x"), evidence("x")])
    assert {item.id for item in repo.read()[1]} == {"b", "c"}


def test_legacy_migration_is_lossless_and_not_replayed(tmp_path):
    old = evidence().model_dump(mode="json")
    old.pop("ingested_at")
    legacy = tmp_path / "evidence_store.json"
    original_bytes = json.dumps([old]).encode()
    legacy.write_bytes(original_bytes)
    repo = EvidenceRepository(tmp_path)
    migrated = repo.read()[1][0]
    assert migrated.id == old["id"]
    assert migrated.timestamp.isoformat() == old["timestamp"]
    assert migrated.ingested_at == datetime.min
    assert migrated.metadata["legacy_ingested_at_unknown"]
    assert legacy.read_bytes() == original_bytes
    repo.update(lambda _: [])
    assert EvidenceRepository(tmp_path).read()[1] == []


@pytest.mark.parametrize("payload", ['{broken', '{}', '[{"id":"bad"}]'])
def test_corrupt_legacy_fails_and_can_retry_after_repair(tmp_path, payload):
    legacy = tmp_path / "evidence_store.json"
    legacy.write_text(payload)
    with pytest.raises(EvidenceStorageError, match="preserved"):
        EvidenceRepository(tmp_path)
    assert legacy.read_text() == payload
    legacy.write_text(json.dumps([evidence().model_dump(mode="json")]))
    assert len(EvidenceRepository(tmp_path).read()[1]) == 1


def test_future_schema_is_not_overwritten(tmp_path):
    db = sqlite3.connect(tmp_path / EvidenceRepository.FILE)
    db.execute("PRAGMA user_version=2")
    db.close()
    with pytest.raises(EvidenceStorageError, match="Unsupported"):
        EvidenceRepository(tmp_path)


def test_index_failure_preserves_evidence_and_retry_can_recover(tmp_path, monkeypatch):
    kg = KnowledgeGraph(tmp_path)
    kg.add(evidence())
    monkeypatch.setattr(kg, "_index", Mock(side_effect=RuntimeError("broken vectors")))
    with pytest.raises(EvidenceIndexUnavailable, match="Saved evidence is intact"):
        kg.query("original")
    assert KnowledgeGraph(tmp_path).get_by_id("one").content == "original"
    collection = Mock()
    collection.query.return_value = {"ids": [["one"]]}
    monkeypatch.setattr(kg, "_index", Mock(return_value=collection))
    assert kg.query("original")[0].id == "one"


def test_search_preserves_rank_and_snapshot_isolation(tmp_path, monkeypatch):
    kg = KnowledgeGraph(tmp_path)
    kg.add_many([evidence("a"), evidence("b")])
    snapshots = []

    def index(snapshot_id, items):
        snapshots.append(snapshot_id)
        collection = Mock()
        collection.query.return_value = {"ids": [[item.id for item in reversed(items)]]}
        return collection

    monkeypatch.setattr(kg, "_index", index)
    assert [item.id for item in kg.query("query")] == ["b", "a"]
    kg.replace_source("docs", [evidence("c")])
    assert [item.id for item in kg.query("query")] == ["c"]
    assert snapshots[0] != snapshots[1]


def test_canonical_corruption_is_not_silently_treated_as_empty(tmp_path):
    repo = EvidenceRepository(tmp_path)
    repo.upsert([evidence()])
    db = sqlite3.connect(tmp_path / repo.FILE)
    db.execute("UPDATE snapshots SET payload='[]'")
    db.commit()
    db.close()
    with pytest.raises(EvidenceStorageError, match="corrupt"):
        repo.read()


def test_readers_see_new_committed_state_without_reopening(tmp_path):
    reader = KnowledgeGraph(tmp_path)
    writer = KnowledgeGraph(tmp_path)
    writer.add(evidence())
    assert reader.get_by_id("one").content == "original"
    # Mutating an evidence object returned to a caller cannot mutate storage.
    reader.store.items[0].content = "uncommitted"
    assert writer.get_by_id("one").content == "original"


def test_missing_legacy_ids_are_not_regenerated(tmp_path):
    raw = evidence().model_dump(mode="json")
    raw.pop("id")
    (tmp_path / "evidence_store.json").write_text(json.dumps([raw]))
    with pytest.raises(EvidenceStorageError):
        EvidenceRepository(tmp_path)
