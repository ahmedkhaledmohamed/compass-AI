"""Prototyper engine — generates self-contained HTML prototypes from evidence.

Takes a description and evidence context, uses LLM to create a Prototype
with self-contained HTML using Tailwind CSS CDN.
"""

from __future__ import annotations

import logging
import re

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask
from compass.models.prototypes import Prototype, PrototypeIteration
from compass.models.sources import Evidence
from compass.prompts import DEFAULT_VERSION, get_prompts

logger = logging.getLogger(__name__)

VALID_TYPES = {"landing-page", "dashboard", "form", "pricing-page", "onboarding-flow"}


def _format_evidence(items: list[Evidence], max_items: int = 20) -> str:
    """Format evidence for the prototype prompt."""
    if not items:
        return "(no evidence available)"
    lines = []
    for item in items[:max_items]:
        preview = item.content[:300] + "..." if len(item.content) > 300 else item.content
        lines.append(f"- [{item.source_type.value}] **{item.title}**: {preview}")
    return "\n".join(lines)


def _extract_content_signals(items: list[Evidence]) -> str:
    """Extract product names, metrics, and quotes from evidence for realistic content."""
    if not items:
        return ""

    names: list[str] = []
    metrics: list[str] = []
    quotes: list[str] = []

    for item in items:
        content = item.content

        # Extract numeric metrics (e.g., "95% uptime", "$1.2M ARR", "10,000 users")
        for match in re.finditer(
            r"(?:\$[\d,.]+[KMB]?|\d[\d,.]*%|\d[\d,.]+\s*(?:users?|customers?|DAU|MAU|ARR|MRR|NPS|sessions?))",
            content,
            re.IGNORECASE,
        ):
            metric = match.group().strip()
            if metric not in metrics:
                metrics.append(metric)

        # Extract quoted text (potential testimonials/user quotes)
        for match in re.finditer(r'"([^"]{20,200})"', content):
            quote = match.group(1).strip()
            if quote not in quotes:
                quotes.append(quote)

        # Use evidence titles as potential product/feature names
        if item.title and item.title not in names:
            names.append(item.title)

    sections = []
    if names[:10]:
        sections.append("Product/feature names from evidence:\n" + "\n".join(f"  - {n}" for n in names[:10]))
    if metrics[:10]:
        sections.append("Real metrics from evidence:\n" + "\n".join(f"  - {m}" for m in metrics[:10]))
    if quotes[:5]:
        sections.append("User quotes from evidence:\n" + "\n".join(f'  - "{q}"' for q in quotes[:5]))

    if not sections:
        return ""
    return "\n\n".join(sections)


def _clean_html(raw: str) -> str:
    """Strip markdown code fences if LLM wraps output."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:html)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
    return raw.strip()


class Prototyper:
    """Generates self-contained HTML prototypes from evidence."""

    def __init__(self, kg: KnowledgeGraph, model: str = "claude-sonnet-4-20250514", prompt_version: str = DEFAULT_VERSION):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def generate(
        self,
        description: str,
        prototype_type: str = "landing-page",
        evidence_ids: list[str] | None = None,
    ) -> Prototype:
        """Generate a prototype from evidence.

        Args:
            description: What the prototype should show.
            prototype_type: Type of prototype to generate.
            evidence_ids: Specific evidence IDs to use (optional).

        Returns:
            Prototype with self-contained HTML.
        """
        if prototype_type not in VALID_TYPES:
            prototype_type = "landing-page"

        # Gather evidence
        if evidence_ids:
            evidence = [self.kg.get_by_id(eid) for eid in evidence_ids]
            evidence = [e for e in evidence if e is not None]
        else:
            evidence = self.kg.query(description, n_results=20)

        evidence_ids_used = [e.id for e in evidence]

        prompts = get_prompts("prototype", self.prompt_version)

        # Build enriched evidence context with extracted content signals
        evidence_context = _format_evidence(evidence)
        content_signals = _extract_content_signals(evidence)
        if content_signals:
            evidence_context += "\n\n--- Extracted content for realistic prototype ---\n" + content_signals

        prompt = prompts["prompt"].format(
            description=description,
            prototype_type=prototype_type,
            evidence_context=evidence_context,
        )

        raw_html = ask(prompt, system=prompts["system"], model=self.model, max_tokens=16384)
        html = _clean_html(raw_html)

        title = self._extract_title(html, description)

        return Prototype(
            title=title,
            type=prototype_type,
            html=html,
            description=description,
            iterations=[PrototypeIteration(prompt=description, html=html)],
            evidence_ids=evidence_ids_used,
        )

    def iterate(
        self,
        prototype: Prototype,
        iteration_prompt: str,
    ) -> Prototype:
        """Iterate on an existing prototype.

        Args:
            prototype: The current prototype to improve.
            iteration_prompt: What to change.

        Returns:
            Updated Prototype with new iteration appended.
        """
        prompts = get_prompts("prototype", self.prompt_version)

        prompt = (
            f"Here is the current HTML prototype:\n\n{prototype.html}\n\n"
            f"Please modify it according to this request:\n{iteration_prompt}\n\n"
            f"Output ONLY the complete updated HTML document."
        )

        raw_html = ask(prompt, system=prompts["system"], model=self.model, max_tokens=16384)
        html = _clean_html(raw_html)

        prototype.html = html
        prototype.iterations.append(PrototypeIteration(prompt=iteration_prompt, html=html))

        return prototype

    def generate_variants(
        self,
        description: str,
        prototype_type: str = "landing-page",
        num_variants: int = 3,
        evidence_ids: list[str] | None = None,
    ) -> list[Prototype]:
        """Generate multiple variant prototypes for A/B comparison.

        Args:
            description: What the prototype should show.
            prototype_type: Type of prototype to generate.
            num_variants: Number of variants (2-3).
            evidence_ids: Specific evidence IDs to use (optional).

        Returns:
            List of Prototype variants with distinct design approaches.
        """
        num_variants = max(2, min(num_variants, 3))

        if prototype_type not in VALID_TYPES:
            prototype_type = "landing-page"

        # Gather evidence once
        if evidence_ids:
            evidence = [self.kg.get_by_id(eid) for eid in evidence_ids]
            evidence = [e for e in evidence if e is not None]
        else:
            evidence = self.kg.query(description, n_results=20)

        evidence_ids_used = [e.id for e in evidence]

        prompts = get_prompts("prototype", self.prompt_version)

        evidence_context = _format_evidence(evidence)
        content_signals = _extract_content_signals(evidence)
        if content_signals:
            evidence_context += "\n\n--- Extracted content for realistic prototype ---\n" + content_signals

        variant_styles = [
            "Variant A: Clean and minimal — generous whitespace, muted colors, subtle typography. Focus on simplicity.",
            "Variant B: Bold and vibrant — strong colors, large typography, prominent CTAs. Focus on energy and conversion.",
            "Variant C: Professional and data-rich — structured layout, detailed information, trust signals. Focus on credibility.",
        ]

        variants = []
        for i in range(num_variants):
            variant_prompt = prompts["prompt"].format(
                description=f"{description}\n\nDesign direction: {variant_styles[i]}",
                prototype_type=prototype_type,
                evidence_context=evidence_context,
            )

            raw_html = ask(variant_prompt, system=prompts["system"], model=self.model, max_tokens=16384)
            html = _clean_html(raw_html)
            title = self._extract_title(html, f"{description} — Variant {chr(65 + i)}")

            variants.append(Prototype(
                title=title,
                type=prototype_type,
                html=html,
                description=f"{description} (Variant {chr(65 + i)})",
                iterations=[PrototypeIteration(prompt=description, html=html)],
                evidence_ids=evidence_ids_used,
            ))

        return variants

    def _extract_title(self, html: str, fallback: str) -> str:
        """Extract title from HTML <title> tag or use fallback."""
        match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return fallback[:80]
