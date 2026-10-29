"""Document storage for Compass workspaces.

Documents are stored as individual JSON files in .compass/documents/.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from compass.models.documents import StoredDocument

logger = logging.getLogger(__name__)


def _docs_dir(workspace_path: Path) -> Path:
    """Get the documents directory for a workspace."""
    d = workspace_path / ".compass" / "documents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_document(workspace_path: Path, doc: StoredDocument) -> StoredDocument:
    """Save a document to disk."""
    doc.updated_at = datetime.now().isoformat()
    doc_path = _docs_dir(workspace_path) / f"{doc.id}.json"
    doc_path.write_text(json.dumps(doc.model_dump(), indent=2))
    logger.info("Saved document %s: %s", doc.id, doc.title)
    return doc


def get_document(workspace_path: Path, doc_id: str) -> StoredDocument | None:
    """Get a document by ID."""
    doc_path = _docs_dir(workspace_path) / f"{doc_id}.json"
    if not doc_path.exists():
        return None
    try:
        data = json.loads(doc_path.read_text())
        return StoredDocument(**data)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Failed to load document %s: %s", doc_id, e, exc_info=True)
        return None


def list_documents(workspace_path: Path) -> list[StoredDocument]:
    """List all documents in a workspace."""
    docs_dir = _docs_dir(workspace_path)
    docs = []
    for f in sorted(docs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            docs.append(StoredDocument(**data))
        except (json.JSONDecodeError, Exception):
            logger.debug("Could not load a cached document; skipping the document.", exc_info=True)
            continue
    return docs


def delete_document(workspace_path: Path, doc_id: str) -> bool:
    """Delete a document by ID."""
    doc_path = _docs_dir(workspace_path) / f"{doc_id}.json"
    if doc_path.exists():
        doc_path.unlink()
        logger.info("Deleted document %s", doc_id)
        return True
    return False


def create_document(
    workspace_path: Path,
    title: str,
    doc_type: str = "custom",
    content_json: dict | None = None,
    content_markdown: str = "",
    tags: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> StoredDocument:
    """Create a new document."""
    doc = StoredDocument(
        id=str(uuid.uuid4())[:8],
        title=title,
        doc_type=doc_type,
        content_json=content_json or {},
        content_markdown=content_markdown,
        tags=tags or [],
        evidence_ids=evidence_ids or [],
    )
    return save_document(workspace_path, doc)
