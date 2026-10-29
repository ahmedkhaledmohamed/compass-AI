"""Planner Engine — weekly planning synthesized from product evidence.

Combines cross-run tracking, evidence freshness, open opportunities,
and conflict state into an actionable weekly plan.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.llm import ask_json
from compass.models.planning import ConfidenceChange, FocusArea, WeeklyPlan
from compass.prompts import DEFAULT_VERSION, get_prompts

logger = logging.getLogger(__name__)


class Planner:
    """Synthesizes product state into actionable weekly plans."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        model: str = "claude-sonnet-4-20250514",
        prompt_version: str = DEFAULT_VERSION,
    ):
        self.kg = kg
        self.model = model
        self.prompt_version = prompt_version

    def plan_week(
        self,
        compass_dir: Path,
        product_name: str = "Product",
    ) -> WeeklyPlan:
        """Generate a weekly plan from current product state."""
        console = Console()

        # Evidence summary by source type
        summary = self.kg.store.summary
        evidence_lines = []
        for source_type, count in summary.items():
            evidence_lines.append(f"- {source_type.upper()}: {count} items")
        evidence_summary = "\n".join(evidence_lines) if evidence_lines else "(no evidence)"

        # Evidence freshness
        freshness_lines = []
        source_groups: dict[str, list] = {}
        for ev in self.kg.store.items:
            key = ev.source_name or ev.connector
            source_groups.setdefault(key, []).append(ev)

        stale_threshold = datetime.now() - timedelta(days=7)
        for name, items in sorted(source_groups.items()):
            latest = max(ev.ingested_at for ev in items)
            age = datetime.now() - latest
            if age.days > 0:
                age_str = f"{age.days} days ago"
            else:
                age_str = f"{int(age.total_seconds() // 3600)} hours ago"
            stale_marker = " [STALE]" if latest < stale_threshold else ""
            freshness_lines.append(f"- {name}: {len(items)} items, last ingested {age_str}{stale_marker}")
        freshness_summary = "\n".join(freshness_lines) if freshness_lines else "(no freshness data)"

        # Opportunities
        opportunities_summary = "(no opportunities discovered yet)"
        cache_path = compass_dir / "opportunities_cache.json"
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text())
                if cache:
                    opp_lines = []
                    for opp in cache:
                        opp_lines.append(
                            f"- #{opp.get('rank', '?')} {opp.get('title', '?')} "
                            f"[{opp.get('confidence', '?')}]: {opp.get('description', '')[:100]}"
                        )
                    opportunities_summary = "\n".join(opp_lines)
            except (json.JSONDecodeError, OSError):
                pass

        # Conflicts
        conflicts_summary = "(no conflicts detected)"
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
                            f"{c.get('description', '')[:100]}"
                        )
                    conflicts_summary = "\n".join(conflict_lines)
            except (json.JSONDecodeError, OSError):
                pass

        # History
        history_summary = "(no discovery history)"
        try:
            from compass.engine.history import get_history_summary
            hist = get_history_summary(compass_dir)
            if hist.get("total_runs", 0) > 0:
                history_summary = (
                    f"Total runs: {hist.get('total_runs', 0)}, "
                    f"Last run: {hist.get('last_run', 'unknown')}, "
                    f"Average opportunities: {hist.get('avg_opportunities', 0):.1f}"
                )
        except Exception:
            logger.debug("Could not load optional workflow data for the weekly plan.", exc_info=True)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Planning your week...", total=1)

            prompts = get_prompts("plan_week", self.prompt_version)
            prompt = prompts["prompt"].format(
                product_name=product_name,
                evidence_summary=evidence_summary,
                opportunities_summary=opportunities_summary,
                conflicts_summary=conflicts_summary,
                history_summary=history_summary,
                freshness_summary=freshness_summary,
            )

            try:
                result = ask_json(prompt, system=prompts["system"], model=self.model)
            except Exception as e:
                console.print(f"[red]Planning failed: {e}[/red]")
                raise

            progress.advance(task)

        focus_areas = []
        for fa in result.get("focus_areas", []):
            focus_areas.append(FocusArea(
                title=fa.get("title", ""),
                reason=fa.get("reason", ""),
                priority=fa.get("priority", "medium"),
                related_opportunities=fa.get("related_opportunities", []),
            ))

        confidence_changes = []
        for cc in result.get("confidence_changes", []):
            confidence_changes.append(ConfidenceChange(
                opportunity=cc.get("opportunity", ""),
                direction=cc.get("direction", "stable"),
                reason=cc.get("reason", ""),
            ))

        return WeeklyPlan(
            summary=result.get("summary", ""),
            focus_areas=focus_areas,
            stale_sources=result.get("stale_sources", []),
            new_signals=result.get("new_signals", []),
            confidence_changes=confidence_changes,
            suggested_actions=result.get("suggested_actions", []),
            evidence_freshness=result.get("evidence_freshness", ""),
        )
