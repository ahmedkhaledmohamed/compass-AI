"""Full product discovery workflow — the core Compass loop.

Orchestrates: ingest → reconcile → discover → specify
This is the "ask what should I build next and get an answer" experience.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from compass.config import get_compass_dir, get_output_dir, load_config
from compass.engine.discoverer import Discoverer
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.reconciler import Reconciler
from compass.engine.source_import import import_source
from compass.engine.specifier import Specifier
from compass.models.conflicts import ConflictReport
from compass.models.specs import FeatureSpec, Opportunity

logger = logging.getLogger(__name__)


class DiscoveryWorkflow:
    """The full product discovery loop."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path.cwd()
        self.console = Console()
        self.config = load_config(self.base_dir)
        self.compass_dir = get_compass_dir(self.base_dir)
        self.output_dir = get_output_dir(self.base_dir)
        self.kg = KnowledgeGraph(persist_dir=self.compass_dir / "knowledge")

    def run(self, auto_specify: bool = True) -> dict:
        """Run the full discovery loop. Returns results dict."""
        results = {
            "status": "ok",
            "evidence_count": 0,
            "conflicts": ConflictReport(),
            "opportunities": [],
            "specs": [],
        }

        # Step 1: Ingest
        self.console.print(Panel("[bold]Step 1/4: Ingesting evidence[/bold]", style="blue"))
        results["evidence_count"] = self._ingest()

        if self._import_failed:
            results["status"] = "error"
            self.console.print("[red]Import failed. Previous evidence was retained; generation stopped.[/red]")
            return results

        if results["evidence_count"] == 0:
            results["status"] = "error"
            self.console.print("[red]No evidence ingested. Check your source connections.[/red]")
            return results

        # Step 2: Reconcile
        self.console.print(Panel("[bold]Step 2/4: Reconciling sources[/bold]", style="blue"))
        results["conflicts"] = self._reconcile()

        # Step 3: Discover
        self.console.print(Panel("[bold]Step 3/4: Discovering opportunities[/bold]", style="blue"))
        results["opportunities"] = self._discover(results["conflicts"])

        # Step 4: Specify (top opportunity)
        if auto_specify and results["opportunities"]:
            self.console.print(Panel("[bold]Step 4/4: Generating spec[/bold]", style="blue"))
            top = results["opportunities"][0]
            spec = self._specify(top)
            if spec:
                results["specs"].append(spec)

        # Summary
        self._print_summary(results)
        return results

    def _ingest(self) -> int:
        self._import_failed = False
        total = 0
        for source in self.config.sources:
            try:
                evidence = import_source(self.kg, source)
                total += len(evidence)
                self.console.print(f"  {source.name}: {len(evidence)} items")
            except Exception as e:
                self._import_failed = True
                logger.debug("Source ingestion failed; retaining other source results.", exc_info=True)
                self.console.print(f"  [red]{source.name}: Error — {e}[/red]")
        return total

    def _reconcile(self) -> ConflictReport:
        reconciler = Reconciler(self.kg, model=self.config.model)
        return reconciler.reconcile()

    def _discover(self, conflicts: ConflictReport) -> list[Opportunity]:
        discoverer = Discoverer(self.kg, model=self.config.model)
        return discoverer.discover(conflicts)

    def _specify(self, opportunity: Opportunity) -> FeatureSpec | None:
        try:
            specifier = Specifier(self.kg, model=self.config.model)
            return specifier.specify(opportunity)
        except Exception as e:
            logger.debug("Specification generation failed; keeping the existing error response.", exc_info=True)
            self.console.print(f"[red]Specification failed: {e}[/red]")
            return None

    def _print_summary(self, results: dict):
        self.console.print("\n")
        self.console.print(Panel(
            f"[bold green]Discovery Complete[/bold green]\n\n"
            f"Evidence ingested: {results['evidence_count']}\n"
            f"Conflicts found: {len(results['conflicts'])}\n"
            f"Opportunities: {len(results['opportunities'])}\n"
            f"Specs generated: {len(results['specs'])}",
            title="compass",
        ))
