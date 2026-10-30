"""Document storage for Compass workspaces.

Documents are stored as individual JSON files in .compass/documents/.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from compass.models.documents import StoredDocument

logger = logging.getLogger(__name__)


class InvalidDocumentPath(ValueError):
    """A document identifier or storage path is outside its allowed boundary."""


def _docs_dir(workspace_path: Path) -> Path:
    """Get the documents directory for a workspace."""
    root = workspace_path.resolve()
    d = (root / ".compass" / "documents").resolve()
    if not d.is_relative_to(root):
        raise InvalidDocumentPath("Document storage must stay inside the workspace")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _document_path(workspace_path: Path, doc_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", doc_id):
        raise InvalidDocumentPath("Document ID must be an opaque alphanumeric identifier")
    docs_dir = _docs_dir(workspace_path)
    candidate = docs_dir / f"{doc_id}.json"
    if candidate.is_symlink() or candidate.resolve().parent != docs_dir:
        raise InvalidDocumentPath("Document path must stay inside document storage")
    return candidate


def save_document(workspace_path: Path, doc: StoredDocument) -> StoredDocument:
    """Save a document to disk."""
    doc_path = _document_path(workspace_path, doc.id)
    doc.updated_at = datetime.now().isoformat()
    doc_path.write_text(json.dumps(doc.model_dump(), indent=2))
    logger.info("Saved document %s: %s", doc.id, doc.title)
    return doc


def get_document(workspace_path: Path, doc_id: str) -> StoredDocument | None:
    """Get a document by ID."""
    doc_path = _document_path(workspace_path, doc_id)
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
    for f in docs_dir.glob("*.json"):
        try:
            f = _document_path(workspace_path, f.stem)
            data = json.loads(f.read_text())
            docs.append((f.stat().st_mtime, StoredDocument(**data)))
        except (json.JSONDecodeError, Exception):
            logger.debug("Could not load a cached document; skipping the document.", exc_info=True)
            continue
    return [doc for _, doc in sorted(docs, key=lambda item: item[0], reverse=True)]


def delete_document(workspace_path: Path, doc_id: str) -> bool:
    """Delete a document by ID."""
    doc_path = _document_path(workspace_path, doc_id)
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
