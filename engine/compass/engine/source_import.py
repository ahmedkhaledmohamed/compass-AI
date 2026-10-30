"""Fetch before replacing a source's active evidence snapshot."""

from datetime import datetime

from compass.config import SourceConfig
from compass.connectors import get_connector
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.models.sources import Evidence


def import_source(kg: KnowledgeGraph, source: SourceConfig) -> list[Evidence]:
    """Preserve the prior source on access, fetch, validation or commit failure.

    Empty imports fail closed: unsupported files or lost access must not look
    like a successful deletion. Explicit source deletion is a separate action.
    """
    connector = get_connector(source.type)(source)
    if not connector.validate():
        raise ValueError("Source is inaccessible; previous evidence was retained")
    items = connector.ingest()
    if not items:
        raise ValueError("Source returned no evidence; previous evidence was retained")
    now = datetime.now()
    prepared = [Evidence.model_validate({
        **item.model_dump(), "source_name": source.name, "ingested_at": now,
    }) for item in items]
    if any(not item.content.strip() for item in prepared):
        raise ValueError("Source returned empty evidence content; previous evidence was retained")
    kg.replace_source(source.name, prepared)
    return prepared


def import_status(results: list[dict]) -> str:
    failures = sum("error" in result for result in results)
    if failures == len(results) and failures:
        return "error"
    return "partial" if failures else "ok"
