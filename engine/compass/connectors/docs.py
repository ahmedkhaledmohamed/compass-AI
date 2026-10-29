"""Documents connector — the DOCS source of truth.

Ingests: Strategy docs, PRDs, roadmaps, design docs (markdown, text).
Answers: "What's EXPECTED to happen?"
"""

from __future__ import annotations

import logging
from pathlib import Path

from compass.connectors.base import Connector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

# Text-based formats we can read directly
DOC_EXTENSIONS = {".md", ".txt", ".rst", ".html"}
# Binary formats we skip (would need python-docx or similar)
BINARY_DOC_EXTENSIONS = {".doc", ".docx", ".pdf"}
# All recognized extensions
ALL_DOC_EXTENSIONS = DOC_EXTENSIONS | BINARY_DOC_EXTENSIONS
MAX_DOC_SIZE = 30_000


class DocsConnector(Connector):
    """Ingests strategy documents, specs, and plans."""

    connector_type = "docs"

    def validate(self) -> bool:
        path = self.config.path
        if not path:
            return False
        p = Path(path).expanduser()
        return p.exists()

    def ingest(self) -> list[Evidence]:
        path = self.config.path
        if not path:
            return []

        p = Path(path).expanduser().resolve()
        evidence: list[Evidence] = []

        if p.is_file():
            ev = self._ingest_file(p)
            if ev:
                evidence.append(ev)
        elif p.is_dir():
            for fpath in sorted(p.rglob("*")):
                if not fpath.is_file():
                    continue
                suffix = fpath.suffix.lower()
                if suffix in BINARY_DOC_EXTENSIONS:
                    logger.info("Skipping binary doc %s (not yet supported)", fpath.name)
                    continue
                if suffix in DOC_EXTENSIONS:
                    ev = self._ingest_file(fpath)
                    if ev:
                        evidence.append(ev)

        return evidence

    def _ingest_file(self, fpath: Path) -> Evidence | None:
        try:
            content = fpath.read_text(errors="ignore")
            if not content.strip():
                return None
            if len(content) > MAX_DOC_SIZE:
                content = content[:MAX_DOC_SIZE] + "\n... (truncated)"

            title = self._extract_title(content, fpath)
            return Evidence(
                source_type=SourceType.DOCS,
                connector="docs",
                title=title,
                content=content,
                metadata={"file": str(fpath), "type": fpath.suffix},
            )
        except Exception as e:
            logger.warning("Failed to ingest doc %s: %s", fpath, e, exc_info=True)
            return None

    def _extract_title(self, content: str, fpath: Path) -> str:
        for line in content.split("\n")[:5]:
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        return fpath.stem.replace("-", " ").replace("_", " ").title()
