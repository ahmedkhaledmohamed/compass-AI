"""The Four Sources of Truth — core data model.

PM work is reconciling four sources of truth:
  Code     → What CAN happen (technical reality)
  Docs     → What's EXPECTED (strategy, specs, plans)
  Data     → What IS happening (metrics, usage, analytics)
  Judgment → What SHOULD happen (interviews, support, human insight)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    """The four sources of product truth."""

    CODE = "code"
    DOCS = "docs"
    DATA = "data"
    JUDGMENT = "judgment"

    @property
    def question(self) -> str:
        return {
            SourceType.CODE: "What CAN happen?",
            SourceType.DOCS: "What's EXPECTED to happen?",
            SourceType.DATA: "What IS happening?",
            SourceType.JUDGMENT: "What SHOULD happen?",
        }[self]

    @property
    def description(self) -> str:
        return {
            SourceType.CODE: "Technical reality — codebase, APIs, architecture",
            SourceType.DOCS: "Strategy and specs — roadmaps, PRDs, design docs",
            SourceType.DATA: "Empirical signal — usage metrics, analytics, experiments",
            SourceType.JUDGMENT: "Human insight — interviews, support tickets, feedback",
        }[self]


# Maps connector types to source types
CONNECTOR_SOURCE_MAP = {
    "code": SourceType.CODE,
    "github": SourceType.CODE,
    "docs": SourceType.DOCS,
    "google_docs": SourceType.DOCS,
    "analytics": SourceType.DATA,
    "data": SourceType.DATA,
    "interviews": SourceType.JUDGMENT,
    "support": SourceType.JUDGMENT,
}


class Evidence(BaseModel):
    """A single piece of evidence from any source."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    source_type: SourceType
    connector: str  # which connector produced this (github, interviews, etc.)
    title: str
    content: str
    metadata: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)
    ingested_at: datetime = Field(default_factory=datetime.now)
    source_id: str = ""
    document_id: str = ""
    content_hash: str = ""
    source_name: str = ""  # which SourceConfig.name produced this

    @property
    def short(self) -> str:
        """Brief representation for citations."""
        return f"[{self.source_type.value}:{self.connector}] {self.title}"


class EvidenceStore(BaseModel):
    """In-memory collection of all ingested evidence."""

    items: list[Evidence] = Field(default_factory=list)

    def add(self, evidence: Evidence) -> None:
        self.items.append(evidence)

    def add_many(self, items: list[Evidence]) -> None:
        self.items.extend(items)

    def by_source(self, source_type: SourceType) -> list[Evidence]:
        return [e for e in self.items if e.source_type == source_type]

    def by_connector(self, connector: str) -> list[Evidence]:
        return [e for e in self.items if e.connector == connector]

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            key = item.source_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def __len__(self) -> int:
        return len(self.items)
