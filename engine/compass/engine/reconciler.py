"""Reconciliation Engine — surface conflicts across the four sources of truth.

This is Compass's core differentiator. Most PM tools help you write docs.
Compass helps you find where your sources of truth disagree.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.conflicts import Conflict, ConflictReport, ConflictSeverity, ConflictType
from compass.models.sources import Evidence, SourceType
from compass.prompts import DEFAULT_VERSION, get_prompts

logger = logging.getLogger(__name__)


def _format_evidence(items: list[Evidence], max_items: int = 15) -> str:
    """Format evidence items for the LLM prompt, including freshness."""
    from datetime import datetime, timedelta

    lines = []
    for item in items[:max_items]:
        content_preview = item.content[:500] + "..." if len(item.content) > 500 else item.content
        freshness = ""
        if hasattr(item, "ingested_at") and item.ingested_at:
            age = datetime.now() - item.ingested_at
            if age > timedelta(days=7):
                freshness = f" ⚠️ STALE ({age.days} days old)"
            elif age > timedelta(days=1):
                freshness = f" ({age.days}d ago)"
        lines.append(f"- [{item.title}]{freshness}: {content_preview}")
    return "\n".join(lines) if lines else "(no evidence from this source)"


SOURCE_PAIRS: list[tuple[SourceType, SourceType, ConflictType]] = [
    (SourceType.CODE, SourceType.DOCS, ConflictType.CODE_VS_DOCS),
    (SourceType.CODE, SourceType.DATA, ConflictType.CODE_VS_DATA),
    (SourceType.CODE, SourceType.JUDGMENT, ConflictType.CODE_VS_JUDGMENT),
    (SourceType.DOCS, SourceType.DATA, ConflictType.DOCS_VS_DATA),
    (SourceType.DOCS, SourceType.JUDGMENT, ConflictType.DOCS_VS_JUDGMENT),
    (SourceType.DATA, SourceType.JUDGMENT, ConflictType.DATA_VS_JUDGMENT),
]


class Reconciler:
    """Finds conflicts between sources of truth."""

    def __init__(self, kg: KnowledgeGraph, model: str = "claude-sonnet-4-20250514", prompt_version: str = DEFAULT_VERSION):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def reconcile(self) -> ConflictReport:
        """Analyze all source pairs for conflicts."""
        console = Console()
        all_conflicts: list[Conflict] = []

        available_sources = set()
        for item in self.kg.store.items:
            available_sources.add(item.source_type)

        pairs_to_check = [
            (sa, sb, ct) for sa, sb, ct in SOURCE_PAIRS
            if sa in available_sources and sb in available_sources
        ]

        if not pairs_to_check:
            console.print("[yellow]Need evidence from at least 2 source types to reconcile.[/yellow]")
            return ConflictReport()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Reconciling sources...", total=len(pairs_to_check))

            for source_a, source_b, conflict_type in pairs_to_check:
                progress.update(
                    task,
                    description=f"Comparing {source_a.value} vs {source_b.value}...",
                )

                evidence_a = self.kg.store.by_source(source_a)
                evidence_b = self.kg.store.by_source(source_b)

                if not evidence_a or not evidence_b:
                    progress.advance(task)
                    continue

                prompts = get_prompts("reconcile", self.prompt_version)
                prompt = prompts["prompt"].format(
                    source_a=source_a.value.upper(),
                    source_a_desc=source_a.description,
                    evidence_a=_format_evidence(evidence_a),
                    source_b=source_b.value.upper(),
                    source_b_desc=source_b.description,
                    evidence_b=_format_evidence(evidence_b),
                )

                try:
                    result = ask_json(prompt, system=prompts["system"], model=self.model)
                    for c in result.get("conflicts", []):
                        all_conflicts.append(Conflict(
                            conflict_type=conflict_type,
                            severity=ConflictSeverity(c.get("severity", "medium")),
                            title=c["title"],
                            description=c["description"],
                            source_a_evidence=c.get("source_a_evidence", []),
                            source_b_evidence=c.get("source_b_evidence", []),
                            recommendation=c.get("recommendation", ""),
                            signal_strength=c.get("signal_strength", 1),
                        ))
                except Exception as e:
                    logger.debug("Could not reconcile evidence sources; retaining other comparisons.", exc_info=True)
                    console.print(f"[dim]Warning: Could not reconcile {source_a.value} vs {source_b.value}: {e}[/dim]")

                progress.advance(task)

        all_conflicts.sort(
            key=lambda c: {"high": 0, "medium": 1, "low": 2}[c.severity.value]
        )
        return ConflictReport(conflicts=all_conflicts)
