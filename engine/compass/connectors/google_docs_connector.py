"""Google Drive connector — the DOCS source via Google Drive API.

Ingests: Google Docs, Sheets (metadata), and text files from Drive.
Answers: "What do our strategy docs, PRDs, and roadmaps say?"

Dual-mode:
  - Live API: Fetches via Google Drive API when OAuth credentials are available
  - File import: Reads from local directory path (fallback)
"""

from __future__ import annotations

import logging
from pathlib import Path

from compass.connectors.live_base import LiveConnector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

GDRIVE_API = "https://www.googleapis.com/drive/v3"

MAX_FILES = 50
MAX_DOC_SIZE = 30_000  # chars

# Google Drive MIME types we care about
DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SLIDE_MIME = "application/vnd.google-apps.presentation"

# File extensions for local fallback
LOCAL_EXTENSIONS = {".md", ".txt", ".rst", ".html", ".htm"}
LOCAL_MAX_FILE_SIZE = 30_000
LOCAL_MAX_FILES = 50


class GoogleDocsConnector(LiveConnector):
    """Ingests docs evidence from Google Drive or a local directory."""

    connector_type = "google_docs"
    provider_id = "google"
    rate_limit_rpm = 60

    def validate(self) -> bool:
        path = self.config.path
        if path and Path(path).exists():
            return True
        return bool(self.has_credentials())

    # ------------------------------------------------------------------
    # Live API ingestion
    # ------------------------------------------------------------------

    def ingest_live(self) -> list[Evidence]:
        """Fetch evidence from Google Drive API."""
        evidence: list[Evidence] = []

        # Query for docs, sheets, slides, and text files (exclude trashed)
        mime_filter = " or ".join([
            f"mimeType='{DOC_MIME}'",
            f"mimeType='{SHEET_MIME}'",
            f"mimeType='{SLIDE_MIME}'",
            "mimeType='text/plain'",
            "mimeType='text/markdown'",
        ])
        query = f"({mime_filter}) and trashed=false"

        try:
            resp = self._api_get(
                f"{GDRIVE_API}/files",
                params={
                    "pageSize": str(MAX_FILES),
                    "q": query,
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                    "orderBy": "modifiedTime desc",
                },
            )
            files = resp.json().get("files", [])
        except Exception as e:
            logger.warning("Failed to list Drive files: %s", e, exc_info=True)
            return evidence

        for f in files:
            file_id = f["id"]
            name = f.get("name", "Untitled")
            mime = f.get("mimeType", "")
            link = f.get("webViewLink", "")
            modified = f.get("modifiedTime", "")[:10]

            content = self._export_file(file_id, mime)
            if not content:
                continue

            evidence.append(Evidence(
                source_type=SourceType.DOCS,
                connector="google_docs",
                title=name,
                content=content[:MAX_DOC_SIZE],
                metadata={
                    "type": "google_drive",
                    "mime_type": mime,
                    "file_id": file_id,
                    "modified": modified,
                    "link": link,
                    "source": "api",
                },
            ))

        logger.info("Google Drive live: fetched %d evidence items", len(evidence))
        return evidence

    def _export_file(self, file_id: str, mime: str) -> str | None:
        """Export a Google Drive file as plain text or CSV."""
        try:
            if mime == DOC_MIME:
                resp = self._api_get(
                    f"{GDRIVE_API}/files/{file_id}/export",
                    params={"mimeType": "text/plain"},
                )
                return resp.text
            elif mime == SHEET_MIME:
                resp = self._api_get(
                    f"{GDRIVE_API}/files/{file_id}/export",
                    params={"mimeType": "text/csv"},
                )
                return resp.text
            elif mime == SLIDE_MIME:
                resp = self._api_get(
                    f"{GDRIVE_API}/files/{file_id}/export",
                    params={"mimeType": "text/plain"},
                )
                return resp.text
            elif mime in ("text/plain", "text/markdown"):
                resp = self._api_get(
                    f"{GDRIVE_API}/files/{file_id}",
                    params={"alt": "media"},
                )
                return resp.text
        except Exception as e:
            logger.warning("Failed to export file %s: %s", file_id, e, exc_info=True)
        return None

    # ------------------------------------------------------------------
    # File-based ingestion (fallback)
    # ------------------------------------------------------------------

    def ingest_file(self) -> list[Evidence]:
        """Read docs from a local directory."""
        evidence: list[Evidence] = []
        path = self.config.path
        if not path:
            return evidence

        doc_path = Path(path).expanduser().resolve()
        if not doc_path.exists():
            return evidence

        files = sorted(
            (f for f in doc_path.rglob("*") if f.is_file() and f.suffix in LOCAL_EXTENSIONS),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        for fpath in files[:LOCAL_MAX_FILES]:
            try:
                content = fpath.read_text(errors="ignore")
                if not content.strip():
                    continue

                # Extract title from first H1 heading or use filename
                title = fpath.stem
                for line in content.split("\n"):
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break

                evidence.append(Evidence(
                    source_type=SourceType.DOCS,
                    connector="google_docs",
                    title=title,
                    content=content[:LOCAL_MAX_FILE_SIZE],
                    metadata={
                        "file": str(fpath.relative_to(doc_path)),
                        "type": "local_file",
                        "extension": fpath.suffix,
                    },
                ))
            except Exception as e:
                logger.warning("Skipping %s: %s", fpath, e, exc_info=True)

        return evidence
