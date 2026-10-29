"""Specification Engine — turn opportunities into agent-ready feature specs.

The last mile: from "we should build X" to a spec that Cursor or Claude Code
can execute.
"""

from __future__ import annotations

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.specs import AgentTask, FeatureSpec, Opportunity
from compass.prompts import DEFAULT_VERSION, get_prompts


class Specifier:
    """Generates feature specs from product opportunities."""

    def __init__(self, kg: KnowledgeGraph, model: str = "claude-sonnet-4-20250514", prompt_version: str = DEFAULT_VERSION):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def specify(self, opportunity: Opportunity) -> FeatureSpec:
        """Generate a full feature spec for an opportunity."""
        console = Console()

        related = self.kg.query(opportunity.title + " " + opportunity.description, n_results=10)
        context_lines = []
        for ev in related:
            preview = ev.content[:200] + "..." if len(ev.content) > 200 else ev.content
            context_lines.append(f"- [{ev.source_type.value}:{ev.connector}] {ev.title}: {preview}")
        context = "\n".join(context_lines) if context_lines else "(no additional context)"

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Specifying: {opportunity.title}...", total=1)

            prompts = get_prompts("specify", self.prompt_version)
            prompt = prompts["prompt"].format(
                title=opportunity.title,
                description=opportunity.description,
                evidence_summary=opportunity.evidence_summary,
                context=context,
            )

            try:
                result = ask_json(prompt, system=prompts["system"], model=self.model)
            except Exception as e:
                console.print(f"[red]Specification failed: {e}[/red]")
                raise

            progress.advance(task)

        tasks = []
        for i, t in enumerate(result.get("tasks", []), 1):
            tasks.append(AgentTask(
                number=i,
                title=t["title"],
                context=t.get("context", ""),
                acceptance_criteria=t.get("acceptance_criteria", []),
                files_to_modify=t.get("files_to_modify", []),
                tests=t.get("tests", ""),
            ))

        return FeatureSpec(
            title=opportunity.title,
            opportunity=opportunity,
            problem_statement=result.get("problem_statement", ""),
            proposed_solution=result.get("proposed_solution", ""),
            ui_changes=result.get("ui_changes", ""),
            data_model_changes=result.get("data_model_changes", ""),
            tasks=tasks,
            success_metrics=result.get("success_metrics", []),
            evidence_citations=result.get("evidence_citations", []),
        )
