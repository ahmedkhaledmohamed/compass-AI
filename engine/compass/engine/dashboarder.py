"""Dashboard Engine — generates chart specifications from evidence.

Takes a natural language question and evidence from the knowledge graph,
then uses LLM to extract numeric data and generate chart specs.
"""

from __future__ import annotations

import logging

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.dashboards import ChartSpec, DashboardSpec
from compass.models.sources import Evidence
from compass.prompts import DEFAULT_VERSION, get_prompts

logger = logging.getLogger(__name__)


def _format_evidence(items: list[Evidence], max_items: int = 20) -> str:
    """Format evidence for the dashboard prompt."""
    if not items:
        return "(no evidence available)"
    lines = []
    for item in items[:max_items]:
        preview = item.content[:500] + "..." if len(item.content) > 500 else item.content
        meta_str = ""
        if item.metadata:
            meta_str = f" | metadata: {item.metadata}"
        lines.append(f"- [{item.source_type.value}] **{item.title}**{meta_str}: {preview}")
    return "\n".join(lines)


class Dashboarder:
    """Generates chart specifications from evidence and NL queries."""

    def __init__(self, kg: KnowledgeGraph, model: str = "claude-sonnet-4-20250514", prompt_version: str = DEFAULT_VERSION):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def generate(self, question: str) -> DashboardSpec:
        """Generate a dashboard spec from a natural language question.

        Args:
            question: The user's question about their product data.

        Returns:
            DashboardSpec with title and chart specifications.
        """
        # Gather all evidence
        all_evidence = self.kg.all()

        prompts = get_prompts("dashboard", self.prompt_version)

        prompt = prompts["prompt"].format(
            evidence=_format_evidence(all_evidence),
            question=question,
        )

        raw = ask_json(prompt, system=prompts["system"], model=self.model, max_tokens=4096)

        return self._parse_response(raw)

    def _parse_response(self, raw: dict | list) -> DashboardSpec:
        """Parse LLM response into DashboardSpec."""
        if isinstance(raw, list):
            raw = {"title": "Dashboard", "charts": raw}

        title = raw.get("title", "Dashboard")
        charts = []

        for chart_data in raw.get("charts", []):
            if isinstance(chart_data, dict):
                chart = ChartSpec(
                    type=chart_data.get("type", "bar"),
                    title=chart_data.get("title", ""),
                    data=chart_data.get("data", []),
                    x_key=chart_data.get("x_key", "label"),
                    y_keys=chart_data.get("y_keys", ["value"]),
                )
                charts.append(chart)

        return DashboardSpec(title=title, charts=charts)
