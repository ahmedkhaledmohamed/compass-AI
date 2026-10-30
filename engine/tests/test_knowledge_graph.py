"""Tests for KnowledgeGraph: add, persist, reload, query, clear."""

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.models.sources import Evidence, SourceType


def _make_evidence(title="Test", content="test content", source_type=SourceType.CODE):
    return Evidence(source_type=source_type, connector="test", title=title, content=content)


class TestKnowledgeGraphPersistence:
    def test_add_and_persist(self, tmp_path):
        persist_dir = tmp_path / "knowledge"
        kg = KnowledgeGraph(persist_dir=persist_dir)
        kg.add(_make_evidence(title="File A", content="def hello(): pass"))
        assert len(kg) == 1
        assert (persist_dir / "evidence.sqlite3").exists()

    def test_add_many_and_persist(self, tmp_path):
        persist_dir = tmp_path / "knowledge"
        kg = KnowledgeGraph(persist_dir=persist_dir)
        items = [
            _make_evidence(title="File A", content="def hello(): pass"),
            _make_evidence(title="File B", content="class World: pass", source_type=SourceType.DOCS),
        ]
        kg.add_many(items)
        assert len(kg) == 2
        assert (persist_dir / "evidence.sqlite3").exists()

    def test_reload_from_persistence(self, tmp_path):
        persist_dir = tmp_path / "knowledge"

        # Create and populate
        kg1 = KnowledgeGraph(persist_dir=persist_dir)
        kg1.add(_make_evidence(title="Evidence A", content="Content A"))
        kg1.add(_make_evidence(title="Evidence B", content="Content B", source_type=SourceType.DOCS))
        assert len(kg1) == 2

        # Reload from same directory
        kg2 = KnowledgeGraph(persist_dir=persist_dir)
        assert len(kg2) == 2
        titles = {e.title for e in kg2.store.items}
        assert titles == {"Evidence A", "Evidence B"}

    def test_clear_persists_empty_active_snapshot(self, tmp_path):
        persist_dir = tmp_path / "knowledge"
        kg = KnowledgeGraph(persist_dir=persist_dir)
        kg.add(_make_evidence())
        assert (persist_dir / "evidence.sqlite3").exists()

        kg.clear()
        assert len(kg) == 0
        assert len(KnowledgeGraph(persist_dir=persist_dir)) == 0


class TestKnowledgeGraphQuery:
    def test_query_returns_relevant(self, tmp_path):
        persist_dir = tmp_path / "knowledge"
        kg = KnowledgeGraph(persist_dir=persist_dir)
        kg.add_many([
            _make_evidence(title="Sync module", content="The sync engine handles real-time data synchronization"),
            _make_evidence(title="Auth module", content="Authentication and authorization using OAuth2"),
        ])
        results = kg.query("sync data", n_results=5)
        assert len(results) > 0

    def test_query_empty_store(self, tmp_path):
        persist_dir = tmp_path / "knowledge"
        kg = KnowledgeGraph(persist_dir=persist_dir)
        results = kg.query("anything")
        assert results == []

    def test_query_with_source_type_filter(self, tmp_path):
        persist_dir = tmp_path / "knowledge"
        kg = KnowledgeGraph(persist_dir=persist_dir)
        kg.add_many([
            _make_evidence(title="Code file", content="def sync(): pass", source_type=SourceType.CODE),
            _make_evidence(title="Strategy doc", content="Our sync strategy is mobile-first", source_type=SourceType.DOCS),
        ])
        results = kg.query("sync", n_results=5, source_type=SourceType.CODE)
        assert all(e.source_type == SourceType.CODE for e in results)

    def test_get_cross_source_evidence(self, tmp_path):
        persist_dir = tmp_path / "knowledge"
        kg = KnowledgeGraph(persist_dir=persist_dir)
        kg.add_many([
            _make_evidence(title="Code sync", content="sync implementation", source_type=SourceType.CODE),
            _make_evidence(title="Docs sync", content="sync strategy document", source_type=SourceType.DOCS),
            _make_evidence(title="Data sync", content="sync usage metrics", source_type=SourceType.DATA),
        ])
        cross = kg.get_cross_source_evidence("sync")
        assert len(cross) >= 1  # At least some sources should match


class TestKnowledgeGraphEphemeral:
    def test_ephemeral_mode(self):
        kg = KnowledgeGraph(persist_dir=None)
        kg.add(_make_evidence(title="Ephemeral", content="This is ephemeral evidence"))
        assert len(kg) == 1
        results = kg.query("ephemeral")
        assert len(results) == 1


def test_partial_vector_cache_is_rebuilt_from_saved_evidence(tmp_path):
    kg = KnowledgeGraph(tmp_path)
    kg.add_many([
        _make_evidence(title="One", content="mobile sync reliability"),
        _make_evidence(title="Two", content="product sync strategy"),
    ])
    assert len(kg.query("sync", n_results=2)) == 2
    snapshot_id, items = kg._repository.read()
    collection = kg._index(snapshot_id, items)
    collection.delete(ids=[items[0].id])
    assert collection.count() == 1
    restarted = KnowledgeGraph(tmp_path)
    assert {item.id for item in restarted.query("sync", n_results=2)} == {item.id for item in items}
    assert collection.count() == 2
