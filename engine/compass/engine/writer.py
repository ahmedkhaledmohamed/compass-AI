"""Writer Engine — generate evidence-grounded product documents.

Turns opportunities into product briefs and synthesizes evidence into
stakeholder updates. Every claim cites specific evidence from the knowledge graph.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from compass.engine.history import get_history_summary
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.documents import (
    ProductBrief,
    Requirement,
    SourceChanges,
    StakeholderUpdate,
)
from compass.models.sources import SourceType
from compass.prompts import DEFAULT_VERSION, get_prompts


class Writer:
    """Generates evidence-grounded product documents."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        model: str = "claude-sonnet-4-20250514",
        prompt_version: str = DEFAULT_VERSION,
    ):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def write_brief(self, opportunity_title: str, description: str = "", evidence_summary: str = "") -> ProductBrief:
        """Generate a product brief for an opportunity."""
        console = Console()

        # Query KG for related evidence
        related = self.kg.query(opportunity_title + " " + description, n_results=15)
        context_lines = []
        for ev in related:
            preview = ev.content[:300] + "..." if len(ev.content) > 300 else ev.content
            context_lines.append(f"- [{ev.source_type.value}:{ev.connector}] {ev.title}: {preview}")
        context = "\n".join(context_lines) if context_lines else "(no additional context)"

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Writing brief: {opportunity_title}...", total=1)

            prompts = get_prompts("write_brief", self.prompt_version)
            prompt = prompts["prompt"].format(
                title=opportunity_title,
                description=description or "(no description provided)",
                evidence_summary=evidence_summary or "(no evidence summary)",
                context=context,
            )

            try:
                result = ask_json(prompt, system=prompts["system"], model=self.model)
            except Exception as e:
                console.print(f"[red]Brief generation failed: {e}[/red]")
                raise

            progress.advance(task)

        requirements = []
        for r in result.get("requirements", []):
            requirements.append(Requirement(
                description=r.get("description", ""),
                priority=r.get("priority", "P1"),
            ))

        return ProductBrief(
            title=result.get("title", opportunity_title),
            problem_statement=result.get("problem_statement", ""),
            target_audience=result.get("target_audience", ""),
            proposed_solution=result.get("proposed_solution", ""),
            requirements=requirements,
            success_metrics=result.get("success_metrics", []),
            risks=result.get("risks", []),
            evidence_citations=result.get("evidence_citations", []),
        )

    def write_update(
        self,
        compass_dir: Path,
        product_name: str = "Product",
        days: int = 7,
    ) -> StakeholderUpdate:
        """Generate a stakeholder update from current evidence state."""
        console = Console()

        # Build evidence summary by source type
        evidence_by_source_lines = []
        for source_type in SourceType:
            items = self.kg.store.by_source(source_type)
            if items:
                evidence_by_source_lines.append(f"### {source_type.value.title()} ({len(items)} items)")
                for item in items[:5]:
                    preview = item.content[:150] + "..." if len(item.content) > 150 else item.content
                    evidence_by_source_lines.append(f"- {item.title}: {preview}")
                if len(items) > 5:
                    evidence_by_source_lines.append(f"- ... and {len(items) - 5} more")
        evidence_by_source = "\n".join(evidence_by_source_lines) if evidence_by_source_lines else "(no evidence ingested)"

        # Get conflicts summary
        conflicts_path = compass_dir / "conflict_report.json"
        conflicts_summary = "(no conflicts detected)"
        if conflicts_path.exists():
            try:
                conflicts_data = json.loads(conflicts_path.read_text())
                conflicts = conflicts_data.get("conflicts", [])
                if conflicts:
                    conflict_lines = []
                    for c in conflicts[:5]:
                        conflict_lines.append(f"- [{c.get('severity', '?')}] {c.get('title', 'Unknown')}: {c.get('description', '')[:100]}")
                    conflicts_summary = "\n".join(conflict_lines)
            except (json.JSONDecodeError, OSError):
                pass

        # Get opportunities summary
        opps_path = compass_dir / "opportunities.json"
        opportunities_summary = "(no opportunities discovered)"
        if opps_path.exists():
            try:
                opps_data = json.loads(opps_path.read_text())
                opps = opps_data if isinstance(opps_data, list) else opps_data.get("opportunities", [])
                if opps:
                    opp_lines = []
                    for o in opps[:5]:
                        opp_lines.append(f"- [{o.get('confidence', '?')}] {o.get('title', 'Unknown')}: {o.get('description', '')[:100]}")
                    opportunities_summary = "\n".join(opp_lines)
            except (json.JSONDecodeError, OSError):
                pass

        # Get history summary
        history_summary_data = get_history_summary(compass_dir)
        history_summary = f"Total discovery runs: {history_summary_data.get('total_runs', 0)}"
        if history_summary_data.get("recurring_opportunities"):
            history_summary += f"\nRecurring opportunities: {', '.join(history_summary_data['recurring_opportunities'].keys())}"
        if history_summary_data.get("persistent_conflicts"):
            history_summary += f"\nPersistent conflicts: {', '.join(history_summary_data['persistent_conflicts'].keys())}"

        # Calculate period
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        period = f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')}"

        # Calculate evidence freshness
        freshness_parts = []
        for source_type in SourceType:
            items = self.kg.store.by_source(source_type)
            if items:
                newest = max(items, key=lambda x: x.ingested_at)
                age = (datetime.now() - newest.ingested_at).days
                freshness_parts.append(f"{source_type.value}: last refreshed {age}d ago")
        evidence_freshness = "; ".join(freshness_parts) if freshness_parts else "No evidence ingested"

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Writing stakeholder update...", total=1)

            prompts = get_prompts("write_update", self.prompt_version)
            prompt = prompts["prompt"].format(
                product_name=product_name,
                evidence_by_source=evidence_by_source,
                conflicts_summary=conflicts_summary,
                opportunities_summary=opportunities_summary,
                history_summary=history_summary,
                period=period,
            )

            try:
                result = ask_json(prompt, system=prompts["system"], model=self.model)
            except Exception as e:
                console.print(f"[red]Update generation failed: {e}[/red]")
                raise

            progress.advance(task)

        changes = []
        for c in result.get("changes_by_source", []):
            changes.append(SourceChanges(
                source_type=c.get("source_type", ""),
                summary=c.get("summary", ""),
                items=c.get("items", []),
            ))

        return StakeholderUpdate(
            title=result.get("title", f"Stakeholder Update: {product_name}"),
            period=result.get("period", period),
            summary=result.get("summary", ""),
            changes_by_source=changes,
            new_signals=result.get("new_signals", []),
            risks=result.get("risks", []),
            next_steps=result.get("next_steps", []),
            evidence_freshness=result.get("evidence_freshness", evidence_freshness),
        )
