"""Task and human-reviewed artifact contracts for the desktop workflow."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


def now() -> str:
    return datetime.now(UTC).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Citation(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=4000)

    # Quotes preserve exact whitespace to permit exact passage verification.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    kind: Literal["supported", "inference", "proposal", "unknown"]
    citations: list[Citation] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def supported_requires_citation(self):
        if self.kind in ("supported", "inference") and not self.citations:
            raise ValueError("Supported claims and inferences require passage citations")
        return self


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DraftBody(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=6000)
    claims: list[Claim] = Field(min_length=1, max_length=60)
    proposed_action: str = Field(min_length=1, max_length=6000)
    alternatives: list[ShortText] = Field(min_length=1, max_length=10)
    unknowns: list[ShortText] = Field(default_factory=list, max_length=30)


Review = Literal["unreviewed", "accepted", "rejected"]
ArtifactKind = Literal["brief", "handoff"]


class Artifact(StrictModel):
    body: DraftBody
    reviews: list[Review]
    state: Literal["draft", "accepted"] = "draft"
    decision: str = ""
    model: str
    provider: str
    workflow_version: str = "decision-task-v1"
    created_at: str = Field(default_factory=now)
    edited_by_user: bool = False

    @model_validator(mode="after")
    def reviews_match_claims(self):
        if len(self.reviews) != len(self.body.claims):
            raise ValueError("Each claim must have a review state")
        return self


class Task(StrictModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    revision: int = 1
    question: str = Field(min_length=1, max_length=4000)
    reader: str = Field(min_length=1, max_length=1000)
    completion_condition: str = Field(min_length=1, max_length=2000)
    source_names: list[str] = Field(min_length=1, max_length=50)
    evidence_snapshot: str
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    brief: Artifact | None = None
    handoff: Artifact | None = None
