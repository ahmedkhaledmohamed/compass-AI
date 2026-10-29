"""Product Discovery Engine — synthesize evidence into opportunities.

This answers the question: "What should we build next?"
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.conflicts import ConflictReport
from compass.models.sources import Evidence, SourceType
from compass.models.specs import Confidence, Opportunity
from compass.prompts import DEFAULT_VERSION, get_prompts

logger = logging.getLogger(__name__)


def _format_evidence_list(items: list[Evidence], max_items: int = 10) -> str:
    if not items:
        return "(no evidence from this source)"
    from datetime import datetime, timedelta

    lines = []
    for item in items[:max_items]:
        preview = item.content[:200] + "..." if len(item.content) > 200 else item.content
        freshness = ""
        if hasattr(item, "ingested_at") and item.ingested_at:
            age = datetime.now() - item.ingested_at
            if age > timedelta(days=7):
                freshness = f" ⚠️ STALE ({age.days} days old)"
        lines.append(f"- **{item.title}**{freshness}: {preview}")
    return "\n".join(lines)


def _format_conflicts(report: ConflictReport, max_conflicts: int = 10) -> str:
    if not report.conflicts:
        return "(no conflicts detected)"
    lines = []
    # Prioritize HIGH severity conflicts, cap total count
    sorted_conflicts = sorted(report.conflicts, key=lambda c: c.severity.value, reverse=True)
    for c in sorted_conflicts[:max_conflicts]:
        desc = c.description[:200] + "..." if len(c.description) > 200 else c.description
        lines.append(
            f"- [{c.severity.value.upper()}] **{c.title}** ({c.conflict_type.description}): "
            f"{desc}"
        )
    if len(report.conflicts) > max_conflicts:
        lines.append(f"- ... and {len(report.conflicts) - max_conflicts} more conflicts")
    return "\n".join(lines)


class Discoverer:
    """Synthesizes evidence and conflicts into product opportunities."""

    def __init__(self, kg: KnowledgeGraph, model: str = "claude-sonnet-4-20250514", prompt_version: str = DEFAULT_VERSION):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def discover(self, conflict_report: ConflictReport | None = None) -> list[Opportunity]:
        """Analyze all evidence and conflicts to surface opportunities."""
        console = Console()

        if not self.kg.store.items:
            console.print("[yellow]No evidence ingested. Run 'compass ingest' first.[/yellow]")
            return []

        conflict_report = conflict_report or ConflictReport()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Discovering opportunities...", total=1)

            prompts = get_prompts("discover", self.prompt_version)
            prompt = prompts["prompt"].format(
                code_evidence=_format_evidence_list(self.kg.store.by_source(SourceType.CODE)),
                docs_evidence=_format_evidence_list(self.kg.store.by_source(SourceType.DOCS)),
                data_evidence=_format_evidence_list(self.kg.store.by_source(SourceType.DATA)),
                judgment_evidence=_format_evidence_list(self.kg.store.by_source(SourceType.JUDGMENT)),
                conflicts=_format_conflicts(conflict_report),
            )

            try:
                result = ask_json(prompt, system=prompts["system"], model=self.model, max_tokens=3000)
            except Exception as e:
                logger.debug("Discovery failed; keeping the existing error response.", exc_info=True)
                console.print(f"[red]Discovery failed: {e}[/red]")
                return []

            progress.advance(task)

        # Build lookup maps for conflict_ids and evidence_ids resolution
        conflict_title_to_idx = {}
        for i, c in enumerate(conflict_report.conflicts):
            conflict_title_to_idx[c.title.lower()] = str(i)

        evidence_title_to_id = {}
        for e in self.kg.store.items:
            evidence_title_to_id[e.title.lower()] = e.id

        opportunities = []
        for i, opp in enumerate(result.get("opportunities", []), 1):
            # Resolve cited_evidence_titles → evidence_ids
            evidence_ids = []
            for cited_title in opp.get("cited_evidence_titles", []):
                cited_lower = cited_title.lower()
                # Exact match first
                if cited_lower in evidence_title_to_id:
                    evidence_ids.append(evidence_title_to_id[cited_lower])
                else:
                    # Partial match — find evidence whose title contains the cited text
                    for etitle, eid in evidence_title_to_id.items():
                        if cited_lower in etitle or etitle in cited_lower:
                            evidence_ids.append(eid)
                            break

            # Resolve related_conflict_titles → conflict_ids
            conflict_ids = []
            for conflict_title in opp.get("related_conflict_titles", []):
                ct_lower = conflict_title.lower()
                if ct_lower in conflict_title_to_idx:
                    conflict_ids.append(conflict_title_to_idx[ct_lower])
                else:
                    for ctitle, cidx in conflict_title_to_idx.items():
                        if ct_lower in ctitle or ctitle in ct_lower:
                            conflict_ids.append(cidx)
                            break

            opportunities.append(Opportunity(
                rank=i,
                title=opp["title"],
                description=opp["description"],
                confidence=Confidence(opp.get("confidence", "medium")),
                evidence_summary=opp.get("evidence_summary", ""),
                estimated_impact=opp.get("estimated_impact", ""),
                evidence_ids=evidence_ids,
                conflict_ids=conflict_ids,
            ))

        return opportunities
