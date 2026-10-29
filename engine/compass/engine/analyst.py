"""Analyst Engine — data analysis and metric interpretation for product questions.

Queries the knowledge graph for data-type evidence, interprets metrics,
and suggests investigative queries for deeper analysis.
"""

from __future__ import annotations

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.analysis import DataAnalysis, SuggestedQuery
from compass.prompts import DEFAULT_VERSION, get_prompts


class Analyst:
    """Interprets product data and suggests investigative queries."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        model: str = "claude-sonnet-4-20250514",
        prompt_version: str = DEFAULT_VERSION,
    ):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def analyze(self, question: str) -> DataAnalysis:
        """Analyze a product question using evidence from the knowledge graph."""
        console = Console()

        # Query KG — get all evidence, then separate data-type
        related = self.kg.query(question, n_results=15)
        context_lines = []
        data_lines = []
        for ev in related:
            preview = ev.content[:400] + "..." if len(ev.content) > 400 else ev.content
            line = f"- [{ev.source_type.value}:{ev.connector}] {ev.title}: {preview}"
            context_lines.append(line)
            if ev.source_type.value == "data":
                # Give data evidence more room
                full = ev.content[:800] + "..." if len(ev.content) > 800 else ev.content
                data_lines.append(f"- [{ev.connector}] {ev.title}: {full}")

        context = "\n".join(context_lines) if context_lines else "(no evidence available)"
        data_evidence = (
            "\n".join(data_lines)
            if data_lines
            else "(no analytics/data evidence available — analysis will be based on other sources)"
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Analyzing: {question[:50]}...", total=1)

            prompts = get_prompts("analyze_data", self.prompt_version)
            prompt = prompts["prompt"].format(
                question=question,
                data_evidence=data_evidence,
                context=context,
            )

            try:
                result = ask_json(prompt, system=prompts["system"], model=self.model)
            except Exception as e:
                console.print(f"[red]Analysis failed: {e}[/red]")
                raise

            progress.advance(task)

        suggested_queries = []
        for sq in result.get("suggested_queries", []):
            suggested_queries.append(SuggestedQuery(
                description=sq.get("description", ""),
                query=sq.get("query", ""),
                data_source=sq.get("data_source", ""),
            ))

        return DataAnalysis(
            key_finding=result.get("key_finding", ""),
            interpretation=result.get("interpretation", ""),
            data_gaps=result.get("data_gaps", []),
            suggested_queries=suggested_queries,
            product_implications=result.get("product_implications", ""),
            evidence_citations=result.get("evidence_citations", []),
        )
