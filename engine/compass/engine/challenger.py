"""Challenger Engine — structured devil's advocate for product opportunities.

Goes beyond a simple system prompt by systematically finding contradicting
evidence, identifying assumptions, and scoring evidence quality.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.challenges import Challenge
from compass.prompts import DEFAULT_VERSION, get_prompts


class Challenger:
    """Stress-tests product opportunities against real evidence."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        model: str = "claude-sonnet-4-20250514",
        prompt_version: str = DEFAULT_VERSION,
    ):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def challenge(
        self,
        opportunity_title: str,
        description: str = "",
        evidence_summary: str = "",
        compass_dir: Path | None = None,
    ) -> Challenge:
        """Challenge an opportunity with structured devil's advocate analysis."""
        console = Console()

        # Query KG for ALL evidence (both supporting and potentially contradicting)
        related = self.kg.query(opportunity_title + " " + description, n_results=20)
        context_lines = []
        for ev in related:
            preview = ev.content[:300] + "..." if len(ev.content) > 300 else ev.content
            context_lines.append(
                f"- [{ev.source_type.value}:{ev.connector}] {ev.title}: {preview}"
            )
        context = "\n".join(context_lines) if context_lines else "(no evidence available)"

        # Load existing conflict report if available
        conflicts_text = "(no conflicts detected)"
        if compass_dir:
            conflicts_path = compass_dir / "conflict_report.json"
            if conflicts_path.exists():
                try:
                    conflicts_data = json.loads(conflicts_path.read_text())
                    conflicts = conflicts_data.get("conflicts", [])
                    if conflicts:
                        conflict_lines = []
                        for c in conflicts[:10]:
                            conflict_lines.append(
                                f"- [{c.get('severity', '?')}] {c.get('title', 'Unknown')}: "
                                f"{c.get('description', '')[:150]}"
                            )
                        conflicts_text = "\n".join(conflict_lines)
                except (json.JSONDecodeError, OSError):
                    pass

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"Challenging: {opportunity_title}...", total=1
            )

            prompts = get_prompts("challenge", self.prompt_version)
            prompt = prompts["prompt"].format(
                title=opportunity_title,
                description=description or "(no description provided)",
                evidence_summary=evidence_summary or "(no evidence summary)",
                context=context,
                conflicts=conflicts_text,
            )

            try:
                result = ask_json(prompt, system=prompts["system"], model=self.model)
            except Exception as e:
                console.print(f"[red]Challenge failed: {e}[/red]")
                raise

            progress.advance(task)

        return Challenge(
            title=result.get("title", opportunity_title),
            weaknesses=result.get("weaknesses", []),
            missing_evidence=result.get("missing_evidence", []),
            assumptions=result.get("assumptions", []),
            risks=result.get("risks", []),
            contradicting_evidence=result.get("contradicting_evidence", []),
            evidence_quality_score=float(result.get("evidence_quality_score", 0)),
            overall_assessment=result.get("overall_assessment", ""),
        )
