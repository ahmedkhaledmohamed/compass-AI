"""Presenter engine — generates structured slide decks from evidence.

Takes a topic and evidence context, uses LLM to create a Presentation
with slides, layouts, and content blocks.
"""

from __future__ import annotations

import logging

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.presentations import ContentBlock, Presentation, Slide
from compass.models.sources import Evidence
from compass.prompts import DEFAULT_VERSION, get_prompts

logger = logging.getLogger(__name__)


def _format_evidence(items: list[Evidence], max_items: int = 30) -> str:
    """Format evidence for the presentation prompt."""
    if not items:
        return "(no evidence available)"
    lines = []
    for item in items[:max_items]:
        preview = item.content[:400] + "..." if len(item.content) > 400 else item.content
        lines.append(f"- [ID:{item.id}] [{item.source_type.value}] **{item.title}**: {preview}")
    return "\n".join(lines)


class Presenter:
    """Generates structured slide decks from evidence."""

    def __init__(self, kg: KnowledgeGraph, model: str = "claude-sonnet-4-20250514", prompt_version: str = DEFAULT_VERSION):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def generate(
        self,
        topic: str,
        description: str = "",
        audience: str = "cross-functional",
        slide_count: int = 8,
        evidence_ids: list[str] | None = None,
    ) -> Presentation:
        """Generate a presentation from evidence.

        Args:
            topic: The presentation topic.
            description: Additional context or instructions.
            audience: Target audience type.
            slide_count: Desired number of slides.
            evidence_ids: Specific evidence IDs to focus on (optional).

        Returns:
            Structured Presentation object.
        """
        # Gather evidence
        if evidence_ids:
            evidence = [self.kg.get_by_id(eid) for eid in evidence_ids]
            evidence = [e for e in evidence if e is not None]
        else:
            evidence = self.kg.query(topic, n_results=30)

        prompts = get_prompts("present", self.prompt_version)

        prompt = prompts["prompt"].format(
            topic=topic,
            description=description or "No additional description.",
            evidence_context=_format_evidence(evidence),
            audience=audience,
            slide_count=slide_count,
        )

        raw = ask_json(prompt, system=prompts["system"], model=self.model, max_tokens=8192)

        return self._parse_response(raw, audience)

    def _parse_response(self, raw: dict | list, audience: str = "") -> Presentation:
        """Parse LLM response into Presentation."""
        if isinstance(raw, list):
            raw = {"title": "Presentation", "slides": raw}

        title = raw.get("title", "Presentation")
        subtitle = raw.get("subtitle", "")
        slides = []

        for slide_data in raw.get("slides", []):
            if isinstance(slide_data, dict):
                blocks = []
                for block_data in slide_data.get("content_blocks", []):
                    if isinstance(block_data, dict):
                        blocks.append(ContentBlock(
                            type=block_data.get("type", "text"),
                            content=block_data.get("content", ""),
                            items=block_data.get("items", []),
                            attrs=block_data.get("attrs", {}),
                        ))

                slides.append(Slide(
                    title=slide_data.get("title", ""),
                    layout=slide_data.get("layout", "content"),
                    content_blocks=blocks,
                    speaker_notes=slide_data.get("speaker_notes", ""),
                ))

        evidence_citations = raw.get("evidence_citations", [])

        return Presentation(
            title=title,
            subtitle=subtitle,
            audience=audience,
            slides=slides,
            evidence_citations=evidence_citations,
        )
