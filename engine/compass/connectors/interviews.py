"""Interview transcript connector — part of the JUDGMENT source of truth.

Ingests: Interview transcripts, user research notes, feedback summaries.
Answers: "What do users WANT?"
"""

from __future__ import annotations

import logging
from pathlib import Path

from compass.connectors.base import Connector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

TRANSCRIPT_EXTENSIONS = {".md", ".txt", ".rst"}
# Binary formats we recognize but can't parse yet
BINARY_EXTENSIONS = {".docx", ".doc", ".pdf"}
MAX_TRANSCRIPT_SIZE = 20_000


class InterviewConnector(Connector):
    """Ingests interview transcripts and user research."""

    connector_type = "interviews"

    def validate(self) -> bool:
        path = self.config.path
        if not path:
            return False
        return Path(path).expanduser().exists()

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
                if suffix in BINARY_EXTENSIONS:
                    logger.info("Skipping binary file %s (not yet supported)", fpath.name)
                    continue
                if suffix in TRANSCRIPT_EXTENSIONS:
                    ev = self._ingest_file(fpath)
                    if ev:
                        evidence.append(ev)

        return evidence

    def _ingest_file(self, fpath: Path) -> Evidence | None:
        try:
            content = fpath.read_text(errors="ignore")
            if not content.strip():
                return None
            if len(content) > MAX_TRANSCRIPT_SIZE:
                content = content[:MAX_TRANSCRIPT_SIZE] + "\n... (truncated)"

            title = self._extract_title(content, fpath)
            return Evidence(
                source_type=SourceType.JUDGMENT,
                connector="interviews",
                title=title,
                content=content,
                metadata={"file": str(fpath), "type": "interview"},
            )
        except Exception as e:
            logger.warning("Failed to ingest interview %s: %s", fpath, e, exc_info=True)
            return None

    def _extract_title(self, content: str, fpath: Path) -> str:
        for line in content.split("\n")[:5]:
            line = line.strip()
            if line.startswith("# "):
                return f"Interview: {line[2:].strip()}"
        return f"Interview: {fpath.stem.replace('-', ' ').replace('_', ' ').title()}"
