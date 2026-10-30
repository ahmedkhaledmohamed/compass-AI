"""Fetch before replacing a source's active evidence snapshot."""

import hashlib
import json
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
    prepared = []
    occurrences: dict[str, int] = {}
    for item in items:
        locator = next((str(item.metadata[key]) for key in ("file", "path", "url", "id", "issue_id", "ticket_id")
                        if item.metadata.get(key)), json.dumps({"connector": item.connector, "title": item.title, "metadata": item.metadata}, sort_keys=True, default=str))
        occurrence = occurrences.get(locator, 0)
        occurrences[locator] = occurrence + 1
        document_id = "doc_" + hashlib.sha256(f"{source.id}\n{locator}\n{occurrence}".encode()).hexdigest()
        content_hash = hashlib.sha256(item.content.encode()).hexdigest()
        evidence_id = "ev_" + hashlib.sha256(f"{document_id}:{content_hash}".encode()).hexdigest()
        prepared.append(Evidence.model_validate({
            **item.model_dump(), "id": evidence_id, "source_id": source.id, "document_id": document_id,
            "content_hash": content_hash, "source_name": source.name, "ingested_at": now,
        }))
    if any(not item.content.strip() for item in prepared):
        raise ValueError("Source returned empty evidence content; previous evidence was retained")
    kg.replace_source(source.name, prepared, source_id=source.id, legacy_names=source.legacy_names)
    return prepared


def import_status(results: list[dict]) -> str:
    failures = sum("error" in result for result in results)
    if failures == len(results) and failures:
        return "error"
    return "partial" if failures else "ok"
