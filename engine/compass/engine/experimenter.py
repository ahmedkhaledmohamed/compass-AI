"""Experimenter Engine — design validation experiments for product opportunities.

Uses evidence from the knowledge graph to ground experiment designs in real data:
baseline metrics, traffic estimates, and measurable success criteria.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.experiments import ExperimentDesign
from compass.prompts import DEFAULT_VERSION, get_prompts


class Experimenter:
    """Designs validation experiments grounded in evidence."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        model: str = "claude-sonnet-4-20250514",
        prompt_version: str = DEFAULT_VERSION,
    ):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def design_experiment(
        self,
        opportunity_title: str,
        description: str = "",
        evidence_summary: str = "",
        compass_dir: Path | None = None,
    ) -> ExperimentDesign:
        """Design a validation experiment for a product opportunity."""
        console = Console()

        # Query KG for evidence — prioritize data-type evidence for metrics
        related = self.kg.query(opportunity_title + " " + description, n_results=15)
        context_lines = []
        data_context_lines = []
        for ev in related:
            preview = ev.content[:300] + "..." if len(ev.content) > 300 else ev.content
            line = f"- [{ev.source_type.value}:{ev.connector}] {ev.title}: {preview}"
            context_lines.append(line)
            if ev.source_type.value == "data":
                data_context_lines.append(line)

        context = "\n".join(context_lines) if context_lines else "(no evidence available)"
        data_context = (
            "\n".join(data_context_lines)
            if data_context_lines
            else "(no analytics/data evidence available — estimates will be needed)"
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"Designing experiment: {opportunity_title}...", total=1
            )

            prompts = get_prompts("experiment", self.prompt_version)
            prompt = prompts["prompt"].format(
                title=opportunity_title,
                description=description or "(no description provided)",
                evidence_summary=evidence_summary or "(no evidence summary)",
                data_context=data_context,
                context=context,
            )

            try:
                result = ask_json(prompt, system=prompts["system"], model=self.model)
            except Exception as e:
                console.print(f"[red]Experiment design failed: {e}[/red]")
                raise

            progress.advance(task)

        return ExperimentDesign(
            title=result.get("title", opportunity_title),
            hypothesis=result.get("hypothesis", ""),
            experiment_type=result.get("experiment_type", ""),
            primary_metric=result.get("primary_metric", ""),
            guardrail_metrics=result.get("guardrail_metrics", []),
            sample_size=result.get("sample_size", ""),
            duration_estimate=result.get("duration_estimate", ""),
            success_criteria=result.get("success_criteria", ""),
            recommended_approach=result.get("recommended_approach", ""),
            risks=result.get("risks", []),
            evidence_citations=result.get("evidence_citations", []),
        )
