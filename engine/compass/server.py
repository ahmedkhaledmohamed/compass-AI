"""Compass Engine HTTP Server — FastAPI bridge for the Electron app.

Exposes the engine as local HTTP endpoints so the Electron main process
can call it via the engine-bridge module.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from compass.config import (
    CONFIG_FILE,
    ProductConfig,
    SourceConfig,
    get_compass_dir,
    get_output_dir,
    load_config,
    save_config,
)
from compass.connectors import get_connector
from compass.engine.discoverer import Discoverer
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.orchestrator import (
    configure_orchestrator,
    disconnect_orchestrator,
    get_orchestrator,
    has_configured_provider,
)
from compass.engine.provider_transport import ProviderError
from compass.engine.reconciler import Reconciler
from compass.engine.source_import import import_source, import_status
from compass.engine.specifier import Specifier
from compass.engine.task_jobs import close_job_manager, get_job_manager
from compass.task_api import router as task_router

logger = logging.getLogger(__name__)

_kg: KnowledgeGraph | None = None
_kg_workspace_path: str | None = None

# In-memory credential store — tokens injected by Electron, never persisted to disk
_credentials: dict[str, dict] = {}
_engine_token = os.environ.get("COMPASS_ENGINE_TOKEN", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        close_job_manager()


app = FastAPI(title="Compass Engine", version="0.1.0", lifespan=lifespan)
app.include_router(task_router)

@app.middleware("http")
async def authenticate_engine_request(request: Request, call_next):
    if not _engine_token:
        return JSONResponse(status_code=503, content={"detail": "Engine authentication is not configured"})
    authorization = request.headers.get("authorization", "").encode()
    expected = f"Bearer {_engine_token}".encode()
    if not secrets.compare_digest(authorization, expected):
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized engine request"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": str(exc), "detail": traceback.format_exc()},
    )


# ---------- Request / Response models ----------

class InitRequest(BaseModel):
    name: str
    description: str = ""
    workspace_path: str


class ConnectRequest(BaseModel):
    source_id: str | None = Field(default=None, pattern=r"^src_[a-f0-9]{32}$")
    new_source: bool = False
    workspace_path: str
    source_type: str
    name: str
    path: str | None = None
    url: str | None = None


class WorkspaceRequest(BaseModel):
    workspace_path: str


class SpecifyRequest(BaseModel):
    workspace_path: str
    opportunity_title: str


class ChatRequest(BaseModel):
    workspace_path: str
    message: str
    history: list[dict] = Field(default_factory=list)
    agent_mode: str = "default"


class SearchRequest(BaseModel):
    workspace_path: str
    query: str
    source_type: str | None = None
    limit: int = 20


class ConfigureRequest(BaseModel):
    api_key: str = ""
    model: str = ""
    provider: str = "anthropic"
    inference_provider: str = ""


class InjectCredentialRequest(BaseModel):
    provider: str
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


# ---------- Health ----------

@app.get("/health")
def health():
    return {"status": "ready", "version": "0.1.0"}


# ---------- Usage ----------

@app.get("/usage")
def usage():
    orch = get_orchestrator()
    u = orch.usage
    return {
        "status": "ok",
        "session_tokens": {"input": u.input_tokens, "output": u.output_tokens},
        "total_tokens": u.total,
        "total_cost_estimate": "Not estimated",
        "usage_complete": u.usage_complete,
    }


# ---------- Configure ----------

@app.post("/configure")
def configure(req: ConfigureRequest):
    """Reconfigure the LLM provider at runtime (e.g. from the Settings page)."""
    try:
        orch = configure_orchestrator(
            api_key=req.api_key,
            model=req.model,
            provider=req.provider,
            inference_provider=req.inference_provider,
        )
        return {
            "status": "ok",
            "provider": req.provider,
            "model": req.model or orch.default_model,
        }
    except ProviderError as exc:
        return JSONResponse(status_code=422, content={"status": "error", "code": exc.code, "message": str(exc)})
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- Credential Injection ----------

@app.post("/credentials/inject")
def inject_credential(req: InjectCredentialRequest):
    """Inject a decrypted credential at runtime. Held in memory only."""
    _credentials[req.provider] = {
        "provider": req.provider,
        "access_token": req.access_token,
        "refresh_token": req.refresh_token,
        "expires_at": req.expires_at,
        "metadata": req.metadata,
    }
    return {"status": "ok", "provider": req.provider}


@app.post("/credentials/revoke")
def revoke_credential(req: dict):
    """Remove an injected credential from memory."""
    provider = req.get("provider", "")
    _credentials.pop(provider, None)
    return {"status": "ok", "provider": provider}


@app.get("/credentials/status")
def credentials_status():
    """List injected credentials (without token values)."""
    result = []
    for provider, cred in _credentials.items():
        result.append({
            "provider": provider,
            "has_token": bool(cred.get("access_token")),
            "expires_at": cred.get("expires_at"),
            "metadata": cred.get("metadata", {}),
        })
    return {"status": "ok", "credentials": result}


def get_credential(provider: str) -> dict | None:
    """Get an injected credential by provider name. Used by live connectors."""
    return _credentials.get(provider)


# ---------- Init ----------

@app.post("/init")
def init_workspace(req: InitRequest):
    base = Path(req.workspace_path)
    base.mkdir(parents=True, exist_ok=True)
    config = ProductConfig(name=req.name, description=req.description)
    config_path = get_compass_dir(base) / CONFIG_FILE
    try:
        with config_path.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(config.model_dump(), handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise HTTPException(409, "This folder already has a Compass workspace. Open it instead of creating another.") from None

    return {"status": "ok", "workspace": req.workspace_path, "name": req.name}


# ---------- Git ----------


class GitPushRequest(BaseModel):
    workspace_path: str
    repo_name: str
    private: bool = True


@app.post("/git/push")
def git_push(req: GitPushRequest):
    """Create a GitHub repo and push the workspace to it."""
    cred = _credentials.get("github")
    if not cred:
        raise HTTPException(401, "GitHub not connected. Please connect GitHub in Settings first.")

    token = cred.get("access_token")
    if not token:
        raise HTTPException(401, "No GitHub access token available.")

    from compass.git_utils import push_to_github
    result = push_to_github(Path(req.workspace_path), req.repo_name, token, req.private)

    if result["status"] == "error":
        raise HTTPException(400, result["error"])

    return result


# ---------- Workspace Info ----------

@app.post("/workspace/info")
def workspace_info(req: WorkspaceRequest):
    """Return workspace state: product config, sources, evidence count."""
    base = Path(req.workspace_path)
    try:
        config = load_config(base)
    except FileNotFoundError:
        raise HTTPException(404, "No Compass workspace found at this path.")

    # Count evidence items
    evidence_count = 0
    evidence_summary = {}
    kg = _try_get_kg(req.workspace_path)
    if kg:
        evidence_count = len(kg)
        evidence_summary = kg.store.summary

    return {
        "status": "ok",
        "name": config.name,
        "description": config.description,
        "sources": [s.model_dump() for s in config.sources],
        "evidence_count": evidence_count,
        "evidence_summary": evidence_summary,
        "model": config.model,
    }


# ---------- Connect ----------

@app.post("/connect")
def connect_source(req: ConnectRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    existing = next((item for item in config.sources if item.id == req.source_id), None) if req.source_id else None
    if req.source_id and existing is None:
        raise HTTPException(404, "Source identity not found")
    if existing and existing.type != req.source_type:
        raise HTTPException(409, "Reconnect a source with its original type")
    named = next((item for item in config.sources if item.name == req.name), None)
    if named and ((req.new_source and not existing) or (existing and named.id != existing.id)):
        raise HTTPException(409, "Another source already uses this name. Choose a distinct name.")
    if not existing and not req.new_source:
        existing = named  # Compatibility for the trusted CLI and older clients.
    source = SourceConfig(id=existing.id if existing else "src_" + uuid4().hex,
                          type=req.source_type, name=req.name, path=req.path, url=req.url,
                          legacy_names=existing.legacy_names if existing else [])
    connector = get_connector(req.source_type)(source)
    if not connector.validate():
        raise HTTPException(422, "Source is inaccessible; its previous configuration was retained")
    config.add_source(source)
    save_config(config, base)
    return {"status": "ok", "source_id": source.id, "name": req.name, "type": req.source_type, "accessible": True}


# ---------- Ingest ----------

@app.post("/ingest")
def ingest(req: WorkspaceRequest):
    global _kg, _kg_workspace_path
    base = Path(req.workspace_path)
    config = load_config(base)

    if not config.sources:
        raise HTTPException(400, "No sources connected")

    compass_dir = get_compass_dir(base)
    kg = KnowledgeGraph(persist_dir=compass_dir / "knowledge")
    _kg = kg
    _kg_workspace_path = req.workspace_path

    results = []
    total = 0
    for source in config.sources:
        try:
            evidence = import_source(kg, source)
            count = len(evidence)
            total += count
            results.append({"name": source.name, "type": source.type, "items": count, "source_id": source.id})
        except Exception as e:
            logger.debug("Source ingestion failed; returning the existing per-source error.", exc_info=True)
            results.append({"name": source.name, "type": source.type, "items": 0, "source_id": source.id, "error": str(e)})

    summary = kg.store.summary

    from compass import activity as act
    act.record(req.workspace_path, "ingest", f"Ingested {total} evidence items",
               metadata={"total": total, "sources": len(results)})

    return {"status": import_status(results), "total": len(kg), "imported": total, "sources": results, "summary": summary}


# ---------- Refresh ----------

class RefreshRequest(BaseModel):
    source_id: str = Field(default="", pattern=r"^(src_[a-f0-9]{32})?$")
    workspace_path: str
    source_name: str = ""  # empty = refresh all


@app.post("/refresh")
def refresh(req: RefreshRequest):
    global _kg, _kg_workspace_path
    base = Path(req.workspace_path)
    config = load_config(base)
    compass_dir = get_compass_dir(base)

    kg = KnowledgeGraph(persist_dir=compass_dir / "knowledge")
    _kg = kg
    _kg_workspace_path = req.workspace_path

    sources_to_refresh = config.sources
    if req.source_id or req.source_name:
        sources_to_refresh = [s for s in config.sources if (s.id == req.source_id if req.source_id else s.name == req.source_name)]
        if not sources_to_refresh:
            raise HTTPException(404, f"Source '{req.source_name}' not found")

    results = []
    for source in sources_to_refresh:
        try:
            removed = sum(item.source_name == source.name for item in kg.store.items)
            evidence = import_source(kg, source)
            results.append({"name": source.name, "removed": removed, "added": len(evidence)})
        except Exception as e:
            logger.debug("Source refresh failed; previous evidence retained.", exc_info=True)
            results.append({"name": source.name, "removed": 0, "added": 0, "error": str(e)})

    return {"status": import_status(results), "total": len(kg), "sources": results}


# ---------- Evidence ----------

@app.post("/evidence")
def get_evidence(req: WorkspaceRequest):
    kg = _get_kg(req.workspace_path)
    items = []
    for e in kg.store.items:
        items.append({
            "id": e.id,
            "source_type": e.source_type.value,
            "connector": e.connector,
            "title": e.title,
            "content": e.content,
            "metadata": e.metadata,
            "timestamp": str(e.timestamp),
        })
    return {
        "status": "ok",
        "count": len(items),
        "summary": kg.store.summary,
        "items": items,
    }


class EvidenceByIdRequest(BaseModel):
    workspace_path: str
    evidence_id: str


@app.post("/evidence/by-id")
def get_evidence_by_id(req: EvidenceByIdRequest):
    """Look up a single evidence item by ID with full metadata."""
    kg = _get_kg(req.workspace_path)
    item = kg.get_by_id(req.evidence_id)
    if not item:
        raise HTTPException(404, f"Evidence item '{req.evidence_id}' not found")

    return {
        "status": "ok",
        "item": {
            "id": item.id,
            "source_type": item.source_type.value,
            "connector": item.connector,
            "title": item.title,
            "content": item.content,
            "metadata": item.metadata,
            "timestamp": str(item.timestamp),
            "ingested_at": str(item.ingested_at) if item.ingested_at else None,
            "source_name": item.source_name or "",
        },
    }


# ---------- Reconcile ----------

@app.post("/reconcile")
def reconcile(req: WorkspaceRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)

    reconciler = Reconciler(kg, model=config.model)
    report = reconciler.reconcile()

    conflicts = []
    for c in report.conflicts:
        conflicts.append({
            "conflict_type": c.conflict_type.value,
            "severity": c.severity.value,
            "title": c.title,
            "description": c.description,
            "source_a_evidence": c.source_a_evidence,
            "source_b_evidence": c.source_b_evidence,
            "recommendation": c.recommendation,
            "signal_strength": c.signal_strength,
        })

    output_dir = get_output_dir(base)
    report_lines = ["# Conflict Report\n"]
    for c in report.conflicts:
        report_lines.append(f"## [{c.severity.value.upper()}] {c.title}\n")
        report_lines.append(f"**Type:** {c.conflict_type.description}\n")
        report_lines.append(f"{c.description}\n")
        report_lines.append(f"**Recommendation:** {c.recommendation}\n\n---\n")
    (output_dir / "conflict-report.md").write_text("\n".join(report_lines))

    return {"status": "ok", "count": len(conflicts), "conflicts": conflicts}


# ---------- Discover ----------

@app.post("/discover")
def discover(req: WorkspaceRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)

    reconciler = Reconciler(kg, model=config.model)
    conflict_report = reconciler.reconcile()

    discoverer = Discoverer(kg, model=config.model)
    opportunities = discoverer.discover(conflict_report)

    result = []
    for opp in opportunities:
        result.append({
            "rank": opp.rank,
            "title": opp.title,
            "description": opp.description,
            "confidence": opp.confidence.value,
            "evidence_summary": opp.evidence_summary,
            "evidence_ids": opp.evidence_ids,
            "conflict_ids": opp.conflict_ids,
            "estimated_impact": opp.estimated_impact,
        })

    compass_dir = get_compass_dir(base)
    cache = [opp.model_dump() for opp in opportunities]
    (compass_dir / "opportunities_cache.json").write_text(json.dumps(cache, indent=2, default=str))

    from compass import activity as act
    act.record(req.workspace_path, "discover", f"Discovered {len(result)} opportunities",
               metadata={"count": len(result)})

    return {"status": "ok", "count": len(result), "opportunities": result}


# ---------- History ----------

@app.post("/history")
def get_discovery_history(req: WorkspaceRequest):
    base = Path(req.workspace_path)
    compass_dir = get_compass_dir(base)

    from compass.engine.history import get_history, get_history_summary
    summary = get_history_summary(compass_dir)
    entries = get_history(compass_dir)

    # Only return the last 20 discovery entries
    discovery_runs = [e for e in entries if e.get("type") == "discovery"][-20:]

    return {"status": "ok", "summary": summary, "runs": discovery_runs}


# ---------- Feedback ----------

class FeedbackRequest(BaseModel):
    workspace_path: str
    opportunity_title: str
    rating: str  # "known", "surprise", "wrong"


@app.post("/feedback")
def submit_feedback(req: FeedbackRequest):
    base = Path(req.workspace_path)
    compass_dir = get_compass_dir(base)

    if req.rating not in ("known", "surprise", "wrong"):
        raise HTTPException(400, f"Invalid rating '{req.rating}'. Use: known, surprise, wrong")

    from compass.engine.history import record_feedback
    entry = record_feedback(compass_dir, req.opportunity_title, req.rating)
    return {"status": "ok", "feedback": entry}


@app.post("/quality")
def get_quality(req: WorkspaceRequest):
    base = Path(req.workspace_path)
    compass_dir = get_compass_dir(base)

    from compass.engine.history import get_quality_stats
    stats = get_quality_stats(compass_dir)
    return {"status": "ok", **stats}


# ---------- Report ----------

class ReportRequest(BaseModel):
    workspace_path: str
    format: str = "markdown"  # "markdown" or "html"


@app.post("/report")
def generate_report_endpoint(req: ReportRequest):
    if req.format not in ("markdown", "html"):
        raise HTTPException(400, f"Invalid format '{req.format}'. Use: markdown, html")

    from compass.engine.reporter import generate_report
    content = generate_report(Path(req.workspace_path), format=req.format)
    return {"status": "ok", "format": req.format, "content": content}


# ---------- Write ----------

class WriteBriefRequest(BaseModel):
    workspace_path: str
    opportunity_title: str
    description: str = ""
    evidence_summary: str = ""


class WriteUpdateRequest(BaseModel):
    workspace_path: str
    days: int = 7


@app.post("/write/brief")
def write_brief_endpoint(req: WriteBriefRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)

    from compass.engine.writer import Writer

    # Try to fill in description/evidence from cached opportunities
    description = req.description
    evidence_summary = req.evidence_summary
    if not description:
        compass_dir = get_compass_dir(base)
        cache_path = compass_dir / "opportunities_cache.json"
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text())
                for opp_data in cache:
                    if req.opportunity_title.lower() in opp_data.get("title", "").lower():
                        description = opp_data.get("description", "")
                        evidence_summary = opp_data.get("evidence_summary", "")
                        break
            except (json.JSONDecodeError, OSError):
                pass

    writer = Writer(kg, model=config.model)
    brief = writer.write_brief(
        req.opportunity_title,
        description=description,
        evidence_summary=evidence_summary,
    )

    return {
        "status": "ok",
        "markdown": brief.to_markdown(),
        "brief": brief.model_dump(),
    }


@app.post("/write/update")
def write_update_endpoint(req: WriteUpdateRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)
    compass_dir = get_compass_dir(base)

    from compass.engine.writer import Writer

    writer = Writer(kg, model=config.model)
    update = writer.write_update(
        compass_dir=compass_dir,
        product_name=config.name,
        days=req.days,
    )

    return {
        "status": "ok",
        "markdown": update.to_markdown(),
        "update": update.model_dump(),
    }


# ---------- Challenge ----------

class ChallengeRequest(BaseModel):
    workspace_path: str
    opportunity_title: str
    description: str = ""
    evidence_summary: str = ""


@app.post("/challenge")
def challenge_endpoint(req: ChallengeRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)
    compass_dir = get_compass_dir(base)

    from compass.engine.challenger import Challenger

    # Try to fill in from cached opportunities
    description = req.description
    evidence_summary = req.evidence_summary
    if not description:
        cache_path = compass_dir / "opportunities_cache.json"
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text())
                for opp_data in cache:
                    if req.opportunity_title.lower() in opp_data.get("title", "").lower():
                        description = opp_data.get("description", "")
                        evidence_summary = opp_data.get("evidence_summary", "")
                        break
            except (json.JSONDecodeError, OSError):
                pass

    challenger = Challenger(kg, model=config.model)
    result = challenger.challenge(
        req.opportunity_title,
        description=description,
        evidence_summary=evidence_summary,
        compass_dir=compass_dir,
    )

    return {
        "status": "ok",
        "markdown": result.to_markdown(),
        "challenge": result.model_dump(),
    }


# ---------- Analyze ----------

class AnalyzeRequest(BaseModel):
    workspace_path: str
    question: str


@app.post("/analyze")
def analyze_endpoint(req: AnalyzeRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)

    from compass.engine.analyst import Analyst

    analyst = Analyst(kg, model=config.model)
    result = analyst.analyze(req.question)

    return {
        "status": "ok",
        "markdown": result.to_markdown(),
        "analysis": result.model_dump(),
    }


# ---------- Plan Week ----------

class PlanWeekRequest(BaseModel):
    workspace_path: str


@app.post("/plan/week")
def plan_week_endpoint(req: PlanWeekRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)
    compass_dir = get_compass_dir(base)

    from compass.engine.planner import Planner

    planner = Planner(kg, model=config.model)
    result = planner.plan_week(
        compass_dir=compass_dir,
        product_name=config.name,
    )

    return {
        "status": "ok",
        "markdown": result.to_markdown(),
        "plan": result.model_dump(),
    }


# ---------- Experiment ----------

class ExperimentRequest(BaseModel):
    workspace_path: str
    opportunity_title: str
    description: str = ""
    evidence_summary: str = ""


@app.post("/experiment")
def experiment_endpoint(req: ExperimentRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)
    compass_dir = get_compass_dir(base)

    from compass.engine.experimenter import Experimenter

    # Try to fill in from cached opportunities
    description = req.description
    evidence_summary = req.evidence_summary
    if not description:
        cache_path = compass_dir / "opportunities_cache.json"
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text())
                for opp_data in cache:
                    if req.opportunity_title.lower() in opp_data.get("title", "").lower():
                        description = opp_data.get("description", "")
                        evidence_summary = opp_data.get("evidence_summary", "")
                        break
            except (json.JSONDecodeError, OSError):
                pass

    experimenter = Experimenter(kg, model=config.model)
    result = experimenter.design_experiment(
        req.opportunity_title,
        description=description,
        evidence_summary=evidence_summary,
        compass_dir=compass_dir,
    )

    return {
        "status": "ok",
        "markdown": result.to_markdown(),
        "experiment": result.model_dump(),
    }


# ---------- Specify ----------

@app.post("/specify")
def specify(req: SpecifyRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)

    from compass.models.specs import Confidence, Opportunity

    compass_dir = get_compass_dir(base)
    cache_path = compass_dir / "opportunities_cache.json"
    opportunity = None

    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        for opp_data in cache:
            if req.opportunity_title.lower() in opp_data["title"].lower():
                opportunity = Opportunity(**opp_data)
                break

    if not opportunity:
        opportunity = Opportunity(
            rank=1,
            title=req.opportunity_title,
            description=req.opportunity_title,
            confidence=Confidence.MEDIUM,
            evidence_summary="User-specified opportunity",
        )

    specifier = Specifier(kg, model=config.model)
    spec = specifier.specify(opportunity)

    output_dir = get_output_dir(base)
    safe_name = opportunity.title.lower().replace(" ", "-")[:50]
    spec_path = output_dir / f"spec-{safe_name}.md"
    spec_path.write_text(spec.to_markdown())

    return {
        "status": "ok",
        "title": spec.title,
        "markdown": spec.to_markdown(),
        "cursor_markdown": spec.to_cursor_markdown(),
        "claude_code_markdown": spec.to_claude_code_markdown(),
        "spec": {
            "title": spec.title,
            "problem_statement": spec.problem_statement,
            "proposed_solution": spec.proposed_solution,
            "ui_changes": spec.ui_changes,
            "data_model_changes": spec.data_model_changes,
            "success_metrics": spec.success_metrics,
            "evidence_citations": spec.evidence_citations,
            "tasks": [t.model_dump() for t in spec.tasks],
            "opportunity": {
                "rank": spec.opportunity.rank,
                "title": spec.opportunity.title,
                "confidence": spec.opportunity.confidence.value,
                "estimated_impact": spec.opportunity.estimated_impact,
            },
        },
        "file": str(spec_path),
    }


# ---------- Chat ----------

CHAT_SYSTEM = """You are Compass, an AI product discovery assistant.
Answer the user's question based on the evidence provided below.
When citing evidence, use [source_type:title] format.
If the evidence doesn't contain enough information, say so clearly."""

CHAT_SYSTEM_NO_EVIDENCE = """You are Compass, an AI product discovery assistant.
You help product managers with product strategy, discovery, and decision-making.
The user hasn't ingested any evidence yet, so answer based on your general knowledge.
Suggest they connect sources (code, docs, analytics, interviews) for grounded insights."""

AGENT_MODE_PROMPTS = {
    "default": CHAT_SYSTEM,
    "thought-partner": """You are Compass in Thought Partner mode. Help the PM think through
product decisions by asking probing questions, exploring implications, and connecting dots
across evidence. Don't just answer — help them think deeper. Use Socratic questioning.
Ground everything in the evidence provided.""",
    "technical-analyst": """You are Compass in Technical Analyst mode. Translate technical
evidence into PM-friendly insights. When discussing code evidence, explain the business
implications. Connect technical debt to user impact. Quantify where possible.
Ground everything in the evidence provided.""",
    "devils-advocate": """You are Compass in Devil's Advocate mode. Challenge the PM's
assumptions constructively. Point out what the evidence DOESN'T support. Highlight risks,
contradictions, and alternative interpretations. Be respectful but rigorous.
Ground everything in the evidence provided.""",
    "writer": """You are Compass in Writer mode. Help the PM draft product documents —
briefs, stakeholder updates, emails, strategy docs. Ground every claim in specific evidence.
Use clear structure: lead with the key insight, organize by priority, cite sources.
Write in a professional but direct tone. Avoid fluff. Ground everything in evidence.""",
    "meeting-prep": """You are Compass in Meeting Prep mode. Help the PM prepare for meetings
by pulling together relevant product context. Summarize the current state of open opportunities,
recent conflicts, and evidence that's relevant to the meeting topic. Anticipate questions
stakeholders might ask and suggest talking points grounded in evidence. Structure your
response as: key updates, risks to flag, questions to expect, and recommended framing.""",
    "experiment-designer": """You are Compass in Experiment Designer mode. Help the PM design
and refine validation experiments for product opportunities. Use evidence to suggest
appropriate metrics, estimate baselines, recommend experiment types, and define success
criteria. Challenge weak hypotheses constructively. Ground everything in evidence.""",
    "data-analyst": """You are Compass in Data Analyst mode. Help the PM explore and interpret
product data. When data evidence is available, analyze trends, identify anomalies, and
suggest investigative queries (SQL, BigQuery). Explain statistical concepts in PM-friendly
terms. Connect metric movements to product causes. When data is insufficient, recommend
what to instrument. Ground everything in evidence.""",
}


def _get_chat_system(agent_mode: str, has_evidence: bool = True, workspace_path: str = "") -> str:
    # Load product context if available
    product_context = ""
    if workspace_path:
        try:
            config = load_config(Path(workspace_path))
            product_context = f"\n\nYou are helping with the product \"{config.name}\"."
            if config.description:
                product_context += f" Description: {config.description}"
            product_context += "\n"
        except (FileNotFoundError, Exception):
            logger.debug("Could not add optional workspace context to the chat prompt.", exc_info=True)

    if not has_evidence:
        return CHAT_SYSTEM_NO_EVIDENCE + product_context
    base_prompt = AGENT_MODE_PROMPTS.get(agent_mode, CHAT_SYSTEM)
    return base_prompt + product_context


def _try_get_kg(workspace_path: str) -> KnowledgeGraph | None:
    """Try to load the knowledge graph, returning None if unavailable.

    Falls back to loading from persistence if the in-memory global is empty,
    which handles engine restarts and race conditions during ingestion.
    """
    try:
        return _get_kg(workspace_path)
    except (HTTPException, FileNotFoundError, Exception):
        # _get_kg raises when KG is empty — try loading from disk directly
        logger.debug("Could not open the knowledge graph; trying the existing fallback.", exc_info=True)
        try:
            base = Path(workspace_path)
            kg_dir = get_compass_dir(base) / "knowledge"
            if kg_dir.exists():
                kg = KnowledgeGraph(persist_dir=kg_dir)
                if len(kg) > 0:
                    return kg
        except Exception:
            logger.debug("Could not open the knowledge graph; trying the existing fallback.", exc_info=True)
        return None


def _build_chat_prompt(
    message: str, history: list[dict], evidence_context: str
) -> str:
    history_text = ""
    for msg in history[-6:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        history_text += f"\n{role.upper()}: {content}"

    if evidence_context:
        return f"""Based on the following evidence from the product workspace:

{evidence_context}

{f"Conversation history:{history_text}" if history_text else ""}

USER QUESTION: {message}

Provide a helpful, evidence-grounded answer. Cite specific evidence using [source_type:title] format."""
    else:
        return f"""{f"Conversation history:{history_text}" if history_text else ""}

USER QUESTION: {message}

Provide a helpful answer."""


@app.post("/chat")
def chat(req: ChatRequest):
    orch = get_orchestrator()
    kg = _try_get_kg(req.workspace_path)

    evidence_context = ""
    citations: list[dict] = []

    if kg:
        relevant = kg.query(req.message, n_results=8)
        evidence_context = "\n\n".join(
            f"[{e.source_type.value}:{e.connector}] {e.title}\n{e.content}"
            for e in relevant
        )
        citations = [
            {"id": e.id, "title": e.title, "source_type": e.source_type.value}
            for e in relevant
        ]

    prompt = _build_chat_prompt(req.message, req.history, evidence_context)
    system = _get_chat_system(req.agent_mode, has_evidence=bool(kg), workspace_path=req.workspace_path)
    response = orch.ask(prompt, system=system)

    return {
        "status": "ok",
        "response": response,
        "citations": citations,
    }


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    orch = get_orchestrator()
    kg = _try_get_kg(req.workspace_path)

    evidence_context = ""
    citations: list[dict] = []

    if kg:
        relevant = kg.query(req.message, n_results=8)
        evidence_context = "\n\n".join(
            f"[{e.source_type.value}:{e.connector}] {e.title}\n{e.content}"
            for e in relevant
        )
        citations = [
            {"id": e.id, "title": e.title, "source_type": e.source_type.value}
            for e in relevant
        ]

    prompt = _build_chat_prompt(req.message, req.history, evidence_context)

    def generate():
        yield _sse_event("citations", {"citations": citations})
        system = _get_chat_system(req.agent_mode, has_evidence=bool(kg), workspace_path=req.workspace_path)
        for token in orch.ask_stream(prompt, system=system):
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------- Search ----------

@app.post("/search")
def search(req: SearchRequest):
    kg = _get_kg(req.workspace_path)

    relevant = kg.query(req.query, n_results=req.limit)

    if req.source_type:
        relevant = [e for e in relevant if e.source_type.value == req.source_type]

    items = [
        {
            "id": e.id,
            "source_type": e.source_type.value,
            "connector": e.connector,
            "title": e.title,
            "content": e.content[:300],
            "metadata": e.metadata,
        }
        for e in relevant
    ]

    return {"status": "ok", "count": len(items), "items": items}


# ---------- SSE Streaming variants ----------

def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/reconcile/stream")
def reconcile_stream(req: WorkspaceRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)

    def generate() -> AsyncGenerator[str, None]:
        yield _sse_event("progress", {"step": "Starting reconciliation...", "pct": 0})

        reconciler = Reconciler(kg, model=config.model)
        yield _sse_event("progress", {"step": "Comparing source pairs...", "pct": 20})

        report = reconciler.reconcile()
        yield _sse_event("progress", {"step": "Analysis complete", "pct": 100})

        conflicts = []
        for c in report.conflicts:
            conflicts.append({
                "conflict_type": c.conflict_type.value,
                "severity": c.severity.value,
                "title": c.title,
                "description": c.description,
                "source_a_evidence": c.source_a_evidence,
                "source_b_evidence": c.source_b_evidence,
                "recommendation": c.recommendation,
                "signal_strength": c.signal_strength,
            })

        yield _sse_event("result", {"count": len(conflicts), "conflicts": conflicts})

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/discover/stream")
def discover_stream(req: WorkspaceRequest):
    base = Path(req.workspace_path)
    config = load_config(base)
    kg = _get_kg(req.workspace_path)

    def generate() -> AsyncGenerator[str, None]:
        yield _sse_event("progress", {"step": "Reconciling sources...", "pct": 10})

        reconciler = Reconciler(kg, model=config.model)
        conflict_report = reconciler.reconcile()
        yield _sse_event("progress", {"step": "Analyzing opportunities...", "pct": 50})

        discoverer = Discoverer(kg, model=config.model)
        opportunities = discoverer.discover(conflict_report)
        yield _sse_event("progress", {"step": "Discovery complete", "pct": 100})

        result = []
        for opp in opportunities:
            result.append({
                "rank": opp.rank,
                "title": opp.title,
                "description": opp.description,
                "confidence": opp.confidence.value,
                "evidence_summary": opp.evidence_summary,
                "estimated_impact": opp.estimated_impact,
            })

        compass_dir = get_compass_dir(base)
        cache = [opp.model_dump() for opp in opportunities]
        (compass_dir / "opportunities_cache.json").write_text(json.dumps(cache, indent=2, default=str))

        yield _sse_event("result", {"count": len(result), "opportunities": result})

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------- Document CRUD endpoints ----------

from compass.documents import (
    InvalidDocumentPath,
    create_document,
    delete_document,
    get_document,
    list_documents,
    save_document,
)


@app.exception_handler(InvalidDocumentPath)
async def invalid_document_path_handler(request: Request, exc: InvalidDocumentPath):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


class DocumentSaveRequest(BaseModel):
    workspace_path: str
    id: str | None = None
    title: str
    doc_type: str = "custom"
    content_json: dict = Field(default_factory=dict)
    content_markdown: str = ""
    tags: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


@app.post("/documents/save")
def documents_save(req: DocumentSaveRequest):
    """Create or update a document."""
    base = Path(req.workspace_path)
    if req.id:
        existing = get_document(base, req.id)
        if existing:
            existing.title = req.title
            existing.doc_type = req.doc_type
            existing.content_json = req.content_json
            existing.content_markdown = req.content_markdown
            existing.tags = req.tags
            existing.evidence_ids = req.evidence_ids
            doc = save_document(base, existing)
        else:
            doc = create_document(base, req.title, req.doc_type, req.content_json, req.content_markdown, req.tags, req.evidence_ids)
    else:
        doc = create_document(base, req.title, req.doc_type, req.content_json, req.content_markdown, req.tags, req.evidence_ids)
    return {"status": "ok", "document": doc.model_dump()}


@app.post("/documents/list")
def documents_list(req: WorkspaceRequest):
    """List all documents."""
    docs = list_documents(Path(req.workspace_path))
    return {"status": "ok", "documents": [d.model_dump() for d in docs]}


class DocumentGetRequest(BaseModel):
    workspace_path: str
    id: str


@app.post("/documents/get")
def documents_get(req: DocumentGetRequest):
    """Get a document by ID."""
    doc = get_document(Path(req.workspace_path), req.id)
    if not doc:
        raise HTTPException(404, f"Document {req.id} not found")
    return {"status": "ok", "document": doc.model_dump()}


@app.post("/documents/delete")
def documents_delete(req: DocumentGetRequest):
    """Delete a document."""
    deleted = delete_document(Path(req.workspace_path), req.id)
    if not deleted:
        raise HTTPException(404, f"Document {req.id} not found")
    return {"status": "ok"}


# ---------- Dashboard endpoints ----------


class DashboardRequest(BaseModel):
    workspace_path: str
    question: str


@app.post("/dashboard/generate")
def dashboard_generate(req: DashboardRequest):
    """Generate a dashboard (chart specs) from a natural language question."""
    kg = _try_get_kg(req.workspace_path)
    if not kg or len(kg) == 0:
        raise HTTPException(400, "No evidence available. Ingest sources first.")

    from compass.engine.dashboarder import Dashboarder
    dashboarder = Dashboarder(kg)
    spec = dashboarder.generate(req.question)

    return {
        "status": "ok",
        "title": spec.title,
        "charts": [c.model_dump() for c in spec.charts],
    }


# ---------- Presentation endpoints ----------


class PresentationRequest(BaseModel):
    workspace_path: str
    topic: str
    description: str = ""
    audience: str = "cross-functional"
    slide_count: int = 8
    evidence_ids: list[str] = Field(default_factory=list)


@app.post("/presentation/generate")
def presentation_generate(req: PresentationRequest):
    """Generate a structured slide deck from evidence."""
    kg = _try_get_kg(req.workspace_path)
    if not kg or len(kg) == 0:
        raise HTTPException(400, "No evidence available. Ingest sources first.")

    from compass.engine.presenter import Presenter
    presenter = Presenter(kg)
    presentation = presenter.generate(
        topic=req.topic,
        description=req.description,
        audience=req.audience,
        slide_count=req.slide_count,
        evidence_ids=req.evidence_ids or None,
    )

    from compass import activity as act
    act.record(req.workspace_path, "presentation", f"Generated presentation: {req.topic}",
               metadata={"slides": len(presentation.slides), "audience": req.audience})

    return {
        "status": "ok",
        "presentation": presentation.model_dump(),
    }


# ---------- Prototype endpoints ----------

class PrototypeGenerateRequest(BaseModel):
    workspace_path: str
    description: str
    prototype_type: str = "landing-page"
    evidence_ids: list[str] = Field(default_factory=list)


class PrototypeVariantsRequest(BaseModel):
    workspace_path: str
    description: str
    prototype_type: str = "landing-page"
    num_variants: int = 3
    evidence_ids: list[str] = Field(default_factory=list)


class PrototypeIterateRequest(BaseModel):
    workspace_path: str
    html: str
    title: str = "Prototype"
    prototype_type: str = "landing-page"
    description: str = ""
    iteration_prompt: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


@app.post("/prototype/generate")
def prototype_generate(req: PrototypeGenerateRequest):
    """Generate a self-contained HTML prototype from evidence."""
    kg = _try_get_kg(req.workspace_path)
    if not kg or len(kg) == 0:
        raise HTTPException(400, "No evidence available. Ingest sources first.")

    from compass.engine.prototyper import Prototyper
    prototyper = Prototyper(kg)
    prototype = prototyper.generate(
        description=req.description,
        prototype_type=req.prototype_type,
        evidence_ids=req.evidence_ids or None,
    )

    return {
        "status": "ok",
        "prototype": prototype.model_dump(),
    }


@app.post("/prototype/variants")
def prototype_variants(req: PrototypeVariantsRequest):
    """Generate multiple variant prototypes for A/B comparison."""
    kg = _try_get_kg(req.workspace_path)
    if not kg or len(kg) == 0:
        raise HTTPException(400, "No evidence available. Ingest sources first.")

    from compass.engine.prototyper import Prototyper
    prototyper = Prototyper(kg)
    variants = prototyper.generate_variants(
        description=req.description,
        prototype_type=req.prototype_type,
        num_variants=req.num_variants,
        evidence_ids=req.evidence_ids or None,
    )

    return {
        "status": "ok",
        "variants": [v.model_dump() for v in variants],
    }


@app.post("/prototype/iterate")
def prototype_iterate(req: PrototypeIterateRequest):
    """Iterate on an existing prototype."""
    kg = _try_get_kg(req.workspace_path)
    if not kg or len(kg) == 0:
        raise HTTPException(400, "No evidence available. Ingest sources first.")

    from compass.engine.prototyper import Prototyper
    from compass.models.prototypes import Prototype

    prototyper = Prototyper(kg)
    current = Prototype(
        title=req.title,
        type=req.prototype_type,
        html=req.html,
        description=req.description,
        evidence_ids=req.evidence_ids,
    )
    updated = prototyper.iterate(current, req.iteration_prompt)

    return {
        "status": "ok",
        "prototype": updated.model_dump(),
    }


@app.get("/prototype/components")
def prototype_components_list():
    """List available prototype component snippets."""
    from compass.prototype_components import list_components
    return {"components": list_components()}


@app.get("/prototype/components/{component_id}")
def prototype_component_get(component_id: str):
    """Get a specific component snippet by ID."""
    from compass.prototype_components import get_component
    comp = get_component(component_id)
    if not comp:
        raise HTTPException(404, f"Component not found: {component_id}")
    return {
        "id": comp.id,
        "name": comp.name,
        "category": comp.category,
        "description": comp.description,
        "html": comp.html,
    }


# ---------- Template endpoints ----------

from compass.templates import get_template, list_templates


@app.get("/templates")
def templates_list():
    """List all available project templates."""
    return {"templates": list_templates()}


class TemplateInitRequest(BaseModel):
    workspace_path: str
    template_id: str
    product_name: str
    product_description: str = ""


@app.post("/templates/init")
def templates_init(req: TemplateInitRequest):
    """Initialize a workspace from a template."""
    template = get_template(req.template_id)
    if not template:
        raise HTTPException(400, f"Unknown template: {req.template_id}")

    base = Path(req.workspace_path)
    base.mkdir(parents=True, exist_ok=True)

    # Build config from template
    sources = []
    for src in template.starter_config.get("sources", []):
        sources.append(SourceConfig(
            type=src["type"],
            name=src["name"],
            path=src.get("path"),
        ))

    config = ProductConfig(
        name=req.product_name,
        description=req.product_description,
        sources=sources,
    )
    save_config(config, base)

    from compass.git_utils import init_git_repo
    git_created = init_git_repo(base)

    return {
        "status": "ok",
        "template": template.id,
        "product_name": req.product_name,
        "sources": [s.name for s in sources],
        "example_questions": template.example_questions,
        "default_chat_mode": template.default_chat_mode,
        "git_initialized": git_created,
    }


# ---------- Sync endpoints ----------

from compass.sync import scheduler


class SyncScheduleRequest(BaseModel):
    workspace_path: str
    source_name: str
    interval_minutes: int = 60


@app.post("/sync/schedule")
def sync_schedule(req: SyncScheduleRequest):
    """Schedule periodic sync for a source."""
    status = scheduler.schedule(req.source_name, req.interval_minutes)
    return {"status": "scheduled", **status.to_dict()}


@app.post("/sync/unschedule")
def sync_unschedule(req: SyncScheduleRequest):
    """Stop scheduled sync for a source."""
    scheduler.unschedule(req.source_name)
    return {"status": "unscheduled", "source_name": req.source_name}


@app.post("/sync/now")
def sync_now(req: SyncScheduleRequest):
    """Trigger an immediate sync for a source."""
    status = scheduler.sync_now(req.source_name)
    return {"status": "synced", **status.to_dict()}


class SyncStatusRequest(BaseModel):
    workspace_path: str
    source_name: str | None = None


@app.post("/sync/status")
def sync_status(req: SyncStatusRequest):
    """Get sync status for one or all sources."""
    result = scheduler.get_status(req.source_name)
    if isinstance(result, list):
        return {"sources": result}
    return result


# ---------- Activity endpoints ----------

from compass import activity


class ActivityRequest(BaseModel):
    workspace_path: str
    limit: int = 50
    event_type: str | None = None


@app.post("/activity")
def activity_list(req: ActivityRequest):
    """Get recent activity events for a workspace."""
    events = activity.get_events(
        req.workspace_path,
        limit=req.limit,
        event_type=req.event_type,
    )
    return {
        "events": [e.model_dump() for e in events],
    }


# ---------- Helpers ----------

def _get_kg(workspace_path: str) -> KnowledgeGraph:
    """Get the knowledge graph, loading from persistence (no re-ingestion).

    If the workspace_path differs from the cached one, create a new KG
    for the new workspace (workspace isolation).
    """
    global _kg, _kg_workspace_path

    # If we have a cached KG for a different workspace, discard it
    if _kg and _kg_workspace_path != workspace_path:
        _kg = None
        _kg_workspace_path = None

    if _kg and len(_kg) > 0:
        return _kg

    base = Path(workspace_path)
    compass_dir = get_compass_dir(base)
    _kg = KnowledgeGraph(persist_dir=compass_dir / "knowledge")
    _kg_workspace_path = workspace_path

    if len(_kg) == 0:
        raise HTTPException(400, "No evidence ingested. Run ingest first.")

    return _kg


@app.get("/connections/readiness")
def connection_readiness():
    return {"status": "ok", "busy": get_job_manager().busy(), "configured": has_configured_provider()}


@app.post("/connections/clear")
def clear_ai_connection():
    if get_job_manager().busy():
        raise HTTPException(409, "Finish or cancel generation before changing the AI connection")
    disconnect_orchestrator()
    return {"status": "ok"}


def main():
    """Run the engine server."""
    import uvicorn
    if not _engine_token:
        raise RuntimeError("Set COMPASS_ENGINE_TOKEN before starting the local engine")
    disconnect_orchestrator()  # Desktop server starts without an implicit environment-funded account.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9811
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
