"""Compass MCP Server — expose Compass tools via Model Context Protocol.

Enables Compass tools inside Claude Code, Cursor, and other MCP-compatible
AI assistants. PMs can run product discovery from within their AI tool.

Usage:
    compass mcp-serve          # start via CLI
    python -m compass.mcp_server  # direct
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "Compass",
    instructions="AI-native product discovery — find where your sources of truth disagree",
)


def _get_workspace() -> Path:
    """Resolve the workspace path from env or cwd.

    Priority: COMPASS_WORKSPACE env var → walk up from cwd looking for .compass/.
    Raises FileNotFoundError if no workspace found.
    """
    ws = os.environ.get("COMPASS_WORKSPACE", "")
    if ws:
        ws_path = Path(ws)
        if (ws_path / ".compass").is_dir():
            return ws_path
        # COMPASS_WORKSPACE set but not initialized — still use it (init will create .compass/)
        return ws_path
    # Walk up from cwd looking for .compass/
    cwd = Path.cwd()
    for p in [cwd, *cwd.parents]:
        if (p / ".compass").is_dir():
            return p
    raise FileNotFoundError(
        f"No Compass workspace found (checked {cwd} and parents). "
        "Set COMPASS_WORKSPACE env var or run `compass init` in your project directory."
    )


def _get_kg(workspace: Path):
    """Load KnowledgeGraph from workspace persistence."""
    from compass.config import get_compass_dir
    from compass.engine.knowledge_graph import KnowledgeGraph

    compass_dir = get_compass_dir(workspace)
    kg = KnowledgeGraph(persist_dir=compass_dir / "knowledge")
    return kg


def _get_config(workspace: Path):
    from compass.config import load_config
    return load_config(workspace)


@mcp.tool()
def compass_status() -> str:
    """Show workspace health: connected sources, evidence counts, freshness."""
    try:
        workspace = _get_workspace()
        config = _get_config(workspace)
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."

    kg = _get_kg(workspace)
    lines = [
        f"# {config.name}",
        f"*{config.description}*" if config.description else "",
        "",
        f"**Evidence:** {len(kg)} items",
        "",
    ]

    if config.sources:
        lines.append("## Connected Sources")
        for s in config.sources:
            lines.append(f"- **{s.name}** ({s.type}): {s.path or s.url or 'configured'}")
    else:
        lines.append("No sources connected. Run `compass connect` first.")

    if len(kg) > 0:
        lines.append("")
        lines.append("## Evidence by Source")
        summary = kg.store.summary
        for source_type, count in summary.items():
            lines.append(f"- {source_type.upper()}: {count} items")

        # Freshness
        from datetime import timedelta
        lines.append("")
        lines.append("## Freshness")
        source_groups: dict[str, list] = {}
        for ev in kg.store.items:
            key = ev.source_name or ev.connector
            source_groups.setdefault(key, []).append(ev)
        stale = datetime.now() - timedelta(days=7)
        for name, items in sorted(source_groups.items()):
            latest = max(ev.ingested_at for ev in items)
            age = datetime.now() - latest
            if age.days > 0:
                age_str = f"{age.days} days ago"
            else:
                age_str = f"{int(age.total_seconds() // 3600)} hours ago"
            warning = " ⚠️ STALE" if latest < stale else ""
            lines.append(f"- {name}: {len(items)} items, last ingested {age_str}{warning}")

    return "\n".join(lines)


@mcp.tool()
def compass_ingest() -> str:
    """Ingest evidence from all connected sources into the knowledge graph."""
    try:
        workspace = _get_workspace()
        config = _get_config(workspace)
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."

    if not config.sources:
        return "No sources connected. Run `compass connect` first."

    from compass.config import get_compass_dir
    from compass.connectors import get_connector
    from compass.engine.knowledge_graph import KnowledgeGraph

    compass_dir = get_compass_dir(workspace)
    kg = KnowledgeGraph(persist_dir=compass_dir / "knowledge")
    kg.clear()

    results = []
    total = 0
    now = datetime.now()
    for source in config.sources:
        try:
            connector_cls = get_connector(source.type)
            connector = connector_cls(source)
            evidence = connector.ingest()
            for ev in evidence:
                ev.source_name = source.name
                ev.ingested_at = now
            kg.add_many(evidence)
            total += len(evidence)
            results.append(f"- {source.name}: {len(evidence)} items")
        except Exception as e:
            logger.debug("Source ingestion failed; retaining results from other sources.", exc_info=True)
            results.append(f"- {source.name}: ERROR — {e}")

    return f"# Ingestion Complete\n\n**{total} evidence items** from {len(config.sources)} sources:\n\n" + "\n".join(results)


@mcp.tool()
def compass_reconcile() -> str:
    """Find conflicts between sources of truth (Code vs Docs vs Data vs Judgment)."""
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    from compass.engine.reconciler import Reconciler

    try:
        reconciler = Reconciler(kg, model=config.model)
        report = reconciler.reconcile()
    except ValueError as e:
        if "API_KEY" in str(e).upper():
            return "ANTHROPIC_API_KEY not set. Set it in your environment or run `compass configure`."
        raise

    if not report.conflicts:
        return "No conflicts detected between sources. Your sources of truth are aligned."

    high = report.high
    high_count = len(high)
    lines = [
        "# Conflict Report",
        "",
    ]

    # Lead with summary paragraph
    if high_count:
        top = high[0]
        lines.append(
            f"**Most critical finding:** {top.title} — {top.description[:200]}{'...' if len(top.description) > 200 else ''}"
        )
        lines.append("")
    lines.append(
        f"Found **{len(report)} conflicts** ({high_count} high severity) across your sources of truth."
    )
    lines.append("")

    for conflict in report.conflicts:
        severity = conflict.severity.value.upper()
        lines.extend([
            f"## [{severity}] {conflict.title}",
            "",
            conflict.description,
            "",
            f"**Type:** {conflict.conflict_type.description}",
            f"**Why it matters:** {conflict.recommendation}",
        ])
        if conflict.signal_strength > 1:
            lines.append(f"**Signal:** Supported by {conflict.signal_strength} evidence items")
        lines.append("")

    lines.append("---")
    lines.append("*Use `compass_discover` to turn these conflicts into actionable product opportunities.*")
    return "\n".join(lines)


@mcp.tool()
def compass_discover() -> str:
    """Synthesize evidence into ranked product opportunities. Answers: 'What should we build next?'"""
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    from compass.engine.discoverer import Discoverer
    from compass.engine.reconciler import Reconciler

    reconciler = Reconciler(kg, model=config.model)
    report = reconciler.reconcile()

    discoverer = Discoverer(kg, model=config.model)
    opportunities = discoverer.discover(report)

    if not opportunities:
        return "No clear opportunities found. Try adding more evidence sources."

    # Summary paragraph
    high_conf = [o for o in opportunities if o.confidence.value == "high"]
    conflict_count = len(report.conflicts) if report else 0
    summary = (
        f"Analyzed **{len(kg)} evidence items** across {len(kg.store.summary)} source types"
        f" and found **{conflict_count} conflicts**. "
        f"Synthesized into **{len(opportunities)} opportunities** "
        f"({len(high_conf)} high confidence)."
    )

    lines = [
        "# Product Opportunities",
        "",
        summary,
        "",
    ]

    for opp in opportunities:
        lines.extend([
            f"## #{opp.rank}: {opp.title}",
            f"**Confidence:** {opp.confidence.value.upper()} | **Impact:** {opp.estimated_impact}",
            "",
            opp.description,
            "",
            f"**Evidence:** {opp.evidence_summary}",
            "",
        ])

    lines.append("---")
    lines.append(f'*To generate an implementation spec: use `compass_specify` with the title, e.g., `compass_specify("{opportunities[0].title}")`*')
    return "\n".join(lines)


@mcp.tool()
def compass_specify(opportunity_title: str) -> str:
    """Generate an agent-ready feature spec for a product opportunity.

    Args:
        opportunity_title: Title of the opportunity to specify (from compass_discover output)
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    import json

    # Try to find cached opportunity
    from compass.config import get_compass_dir
    from compass.engine.specifier import Specifier
    from compass.models.specs import Confidence, Opportunity
    compass_dir = get_compass_dir(workspace)
    cache_path = compass_dir / "opportunities_cache.json"
    opportunity = None

    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            for opp_data in cache:
                if opportunity_title.lower() in opp_data["title"].lower():
                    opportunity = Opportunity(**opp_data)
                    break
        except Exception:
            logger.debug("Could not attach a generated specification to the workspace.", exc_info=True)

    if not opportunity:
        opportunity = Opportunity(
            rank=1,
            title=opportunity_title,
            description=opportunity_title,
            confidence=Confidence.MEDIUM,
            evidence_summary="User-specified opportunity",
        )

    specifier = Specifier(kg, model=config.model)
    spec = specifier.specify(opportunity)
    return spec.to_claude_code_markdown()


@mcp.tool()
def compass_ask(question: str) -> str:
    """Ask a question about your product, grounded in evidence from all sources.

    Args:
        question: Your question about the product (e.g., "What frustrates users the most?")
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    from compass.engine.llm import ask

    related = kg.query(question, n_results=10)
    evidence_lines = []
    for ev in related:
        preview = ev.content[:300] + "..." if len(ev.content) > 300 else ev.content
        evidence_lines.append(f"- [{ev.source_type.value}:{ev.connector}] **{ev.title}**: {preview}")
    evidence_text = "\n".join(evidence_lines) if evidence_lines else "(no relevant evidence found)"

    system = (
        "You are Compass, an AI product discovery assistant. Answer the user's question "
        "based on the evidence provided. Cite evidence naturally inline — weave titles "
        "into your response rather than listing them separately. For example, write "
        "'According to [Sync failure rate — last 30 days], latency increased 5x' "
        "rather than a separate citations block. If evidence is insufficient, say what's missing."
    )
    prompt = f"## Question\n{question}\n\n## Evidence\n{evidence_text}\n\nAnswer grounded in the evidence above."

    response = ask(prompt, system=system, model=config.model)
    return response


@mcp.tool()
def compass_search(query: str, source_type: str = "") -> str:
    """Semantic search across all ingested evidence.

    Args:
        query: Search query (e.g., "sync failures", "user onboarding")
        source_type: Optional filter: "code", "docs", "data", or "judgment"
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    from compass.models.sources import SourceType
    st = None
    if source_type:
        try:
            st = SourceType(source_type.lower())
        except ValueError:
            return f"Invalid source_type '{source_type}'. Use: code, docs, data, or judgment."

    results = kg.query(query, n_results=10, source_type=st)
    if not results:
        return f"No evidence found matching '{query}'."

    lines = [f"# Search: {query}", f"Found **{len(results)} results**:", ""]
    for ev in results:
        preview = ev.content[:200] + "..." if len(ev.content) > 200 else ev.content
        lines.extend([
            f"### [{ev.source_type.value.upper()}] {ev.title}",
            f"*Source: {ev.connector}*",
            "",
            preview,
            "",
        ])

    return "\n".join(lines)


@mcp.tool()
def compass_refresh(source_name: str = "") -> str:
    """Re-ingest evidence from one source (or all), replacing old data.

    Args:
        source_name: Source to refresh (e.g., "analytics:metrics"). Empty = refresh all.
    """
    try:
        workspace = _get_workspace()
        config = _get_config(workspace)
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."

    if not config.sources:
        return "No sources connected."

    from compass.config import get_compass_dir
    from compass.connectors import get_connector
    from compass.engine.knowledge_graph import KnowledgeGraph

    compass_dir = get_compass_dir(workspace)
    kg = KnowledgeGraph(persist_dir=compass_dir / "knowledge")

    sources = config.sources
    if source_name:
        sources = [s for s in config.sources if s.name == source_name]
        if not sources:
            available = ", ".join(s.name for s in config.sources)
            return f"Source '{source_name}' not found. Available: {available}"

    results = []
    now = datetime.now()
    for source in sources:
        try:
            removed = kg.remove_by_connector(source.name)
            if removed == 0:
                removed = kg.remove_by_connector(source.type)

            connector_cls = get_connector(source.type)
            connector = connector_cls(source)
            evidence = connector.ingest()
            for ev in evidence:
                ev.source_name = source.name
                ev.ingested_at = now
            kg.add_many(evidence)
            results.append(f"- {source.name}: removed {removed}, added {len(evidence)}")
        except Exception as e:
            logger.debug("Source refresh failed; retaining results from other sources.", exc_info=True)
            results.append(f"- {source.name}: ERROR — {e}")

    return f"# Refresh Complete\n\n**Total evidence:** {len(kg)} items\n\n" + "\n".join(results)


@mcp.tool()
def compass_connect(source_type: str, path: str, name: str = "") -> str:
    """Connect an evidence source to the workspace.

    Args:
        source_type: Source type: github, docs, analytics, interviews, support
        path: Local file or directory path
        name: Optional custom name for this source
    """
    try:
        workspace = _get_workspace()
        config = _get_config(workspace)
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."

    from compass.config import SourceConfig, save_config
    from compass.connectors import get_connector

    source_name = name or f"{source_type}:{Path(path).name}"
    source = SourceConfig(type=source_type, name=source_name, path=path)

    connector_cls = get_connector(source_type)
    connector = connector_cls(source)
    valid = connector.validate()

    if not valid:
        return f"Source '{source_name}' is not accessible at path: {path}"

    config.add_source(source)
    save_config(config, workspace)
    return f"Connected **{source_name}** ({source_type}) — accessible ✓\n\nRun `compass_ingest` to ingest evidence."


@mcp.tool()
def compass_challenge(opportunity_title: str) -> str:
    """Challenge a product opportunity — structured devil's advocate analysis.

    Finds contradicting evidence, identifies unsupported assumptions, surfaces risks,
    and scores evidence quality. Returns a structured challenge report.

    Args:
        opportunity_title: Title of the opportunity to challenge (from compass_discover output)
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    import json

    from compass.config import get_compass_dir
    from compass.engine.challenger import Challenger
    compass_dir = get_compass_dir(workspace)

    # Try to find cached opportunity
    cache_path = compass_dir / "opportunities_cache.json"
    description = ""
    evidence_summary = ""
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            for opp_data in cache:
                if opportunity_title.lower() in opp_data.get("title", "").lower():
                    description = opp_data.get("description", "")
                    evidence_summary = opp_data.get("evidence_summary", "")
                    break
        except Exception:
            logger.debug("Could not attach challenge evidence to the workspace.", exc_info=True)

    challenger = Challenger(kg, model=config.model)
    result = challenger.challenge(
        opportunity_title,
        description=description,
        evidence_summary=evidence_summary,
        compass_dir=compass_dir,
    )
    return result.to_markdown()


@mcp.tool()
def compass_analyze(question: str) -> str:
    """Analyze a product question using data evidence from the knowledge graph.

    Interprets metrics, identifies trends, suggests investigative SQL queries,
    and connects data findings to product implications.

    Args:
        question: Product question to analyze (e.g., "Why is retention dropping?")
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    from compass.engine.analyst import Analyst

    analyst = Analyst(kg, model=config.model)
    result = analyst.analyze(question)
    return result.to_markdown()


@mcp.tool()
def compass_plan_week() -> str:
    """Generate a weekly plan synthesized from all product evidence.

    Combines open opportunities, conflicts, evidence freshness, and discovery history
    into an actionable weekly plan with focus areas and suggested next steps.
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    from compass.config import get_compass_dir
    from compass.engine.planner import Planner

    compass_dir = get_compass_dir(workspace)
    planner = Planner(kg, model=config.model)
    result = planner.plan_week(
        compass_dir=compass_dir,
        product_name=config.name,
    )
    return result.to_markdown()


@mcp.tool()
def compass_experiment(opportunity_title: str) -> str:
    """Design a validation experiment for a product opportunity.

    Uses evidence from the knowledge graph to ground experiment designs in real data:
    baseline metrics, traffic estimates, sample sizes, and measurable success criteria.

    Args:
        opportunity_title: Title of the opportunity to design an experiment for (from compass_discover output)
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    import json

    from compass.config import get_compass_dir
    from compass.engine.experimenter import Experimenter
    compass_dir = get_compass_dir(workspace)

    # Try to find cached opportunity
    cache_path = compass_dir / "opportunities_cache.json"
    description = ""
    evidence_summary = ""
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            for opp_data in cache:
                if opportunity_title.lower() in opp_data.get("title", "").lower():
                    description = opp_data.get("description", "")
                    evidence_summary = opp_data.get("evidence_summary", "")
                    break
        except Exception:
            logger.debug("Could not attach the experiment to the workspace.", exc_info=True)

    experimenter = Experimenter(kg, model=config.model)
    result = experimenter.design_experiment(
        opportunity_title,
        description=description,
        evidence_summary=evidence_summary,
        compass_dir=compass_dir,
    )
    return result.to_markdown()


@mcp.tool()
def compass_write_brief(opportunity_title: str) -> str:
    """Generate an evidence-grounded product brief for an opportunity.

    Creates a structured brief with problem statement, requirements (P0/P1/P2),
    success metrics, and risks — all grounded in evidence from your knowledge graph.

    Args:
        opportunity_title: Title of the opportunity (from compass_discover output)
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    import json

    # Try to find cached opportunity for richer context
    from compass.config import get_compass_dir
    from compass.engine.writer import Writer
    compass_dir = get_compass_dir(workspace)
    cache_path = compass_dir / "opportunities_cache.json"
    description = ""
    evidence_summary = ""

    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            for opp_data in cache:
                if opportunity_title.lower() in opp_data.get("title", "").lower():
                    description = opp_data.get("description", "")
                    evidence_summary = opp_data.get("evidence_summary", "")
                    break
        except Exception:
            logger.debug("Could not attach the brief to the workspace.", exc_info=True)

    writer = Writer(kg, model=config.model)
    brief = writer.write_brief(
        opportunity_title,
        description=description,
        evidence_summary=evidence_summary,
    )
    return brief.to_markdown()


@mcp.tool()
def compass_write_update(days: int = 7) -> str:
    """Generate an evidence-grounded stakeholder update.

    Summarizes what changed across your sources of truth (Code, Docs, Data, Judgment),
    highlights new signals and risks, and recommends next steps.

    Args:
        days: Number of days to cover in the update (default: 7)
    """
    try:
        workspace = _get_workspace()
    except FileNotFoundError:
        return "No Compass workspace found. Run `compass init` first, or set COMPASS_WORKSPACE."
    kg = _get_kg(workspace)
    if len(kg) == 0:
        return "No evidence ingested. Run `compass ingest` first."

    config = _get_config(workspace)
    from compass.config import get_compass_dir
    from compass.engine.writer import Writer

    compass_dir = get_compass_dir(workspace)
    writer = Writer(kg, model=config.model)
    update = writer.write_update(
        compass_dir=compass_dir,
        product_name=config.name,
        days=days,
    )
    return update.to_markdown()


def main():
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
