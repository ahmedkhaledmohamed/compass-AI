"""Desktop task operations backed by the shared task application service."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from compass.config import load_config
from compass.engine.task_jobs import JobStopped, get_job_manager
from compass.engine.task_repository import TaskConflict, TaskNotFound
from compass.engine.task_service import TaskService, source_key
from compass.models.tasks import ArtifactKind, DraftBody, StrictModel

router = APIRouter(prefix="/tasks")


class Workspace(StrictModel):
    workspace_path: str = Field(min_length=1, max_length=4096)


class CreateTask(Workspace):
    question: str = Field(min_length=1, max_length=4000)
    reader: str = Field(min_length=1, max_length=1000)
    completion_condition: str = Field(min_length=1, max_length=2000)
    source_names: list[str] = Field(min_length=1, max_length=50)


class TaskRef(Workspace):
    task_id: str = Field(min_length=1, max_length=128)


class Operation(TaskRef):
    expected_revision: int = Field(ge=1)
    kind: ArtifactKind


class Revision(Operation):
    body: DraftBody


class Review(Operation):
    claim_index: int = Field(ge=0)
    decision: Literal["accepted", "rejected"]


class Acceptance(Operation):
    decision: str = Field(min_length=1, max_length=6000)


def execute(req: Workspace, operation: Callable):
    try:
        workspace = Path(req.workspace_path)
        load_config(workspace)
        return operation(TaskService(workspace))
    except TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (TaskConflict, JobStopped) as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def view(service, task):
    return {"status": "ok", "task": task.model_dump(), "stale": service.stale(task)}


@router.post("/create")
def create(req: CreateTask):
    return execute(req, lambda service: view(service, service.create(
        req.question, req.reader, req.completion_condition, req.source_names,
    )))


@router.post("/list")
def list_tasks(req: Workspace):
    return execute(req, lambda service: {
        "status": "ok", "tasks": [{
            "id": task.id, "revision": task.revision, "question": task.question,
            "updated_at": task.updated_at,
            "brief_state": task.brief.state if task.brief else None,
            "handoff_state": task.handoff.state if task.handoff else None,
        } for task in service.tasks.list()],
    })


@router.post("/get")
def get(req: TaskRef):
    return execute(req, lambda service: view(service, service.tasks.read(req.task_id)))


@router.post("/sources")
def sources(req: Workspace):
    def result(service):
        groups = {}
        for item in service.evidence.read()[1]:
            key = source_key(item)
            groups[key] = groups.get(key, 0) + 1
        return {"status": "ok", "sources": [{"name": name, "items": count}
                                               for name, count in sorted(groups.items())]}
    return execute(req, result)


@router.post("/evidence")
def evidence(req: TaskRef):
    def result(service):
        task = service.tasks.read(req.task_id)
        return {"status": "ok", "snapshot": task.evidence_snapshot,
                "items": [item.model_dump(mode="json") for item in service.selected_evidence(task)]}
    return execute(req, result)


@router.post("/generate")
def generate(req: Operation):
    return execute(req, lambda service: view(service, service.generate(
        req.task_id, req.expected_revision, req.kind,
    )))


@router.post("/revise")
def revise(req: Revision):
    return execute(req, lambda service: view(service, service.revise(
        req.task_id, req.expected_revision, req.kind, req.body,
    )))


@router.post("/review")
def review(req: Review):
    return execute(req, lambda service: view(service, service.review(
        req.task_id, req.expected_revision, req.kind, req.claim_index, req.decision,
    )))


@router.post("/accept")
def accept(req: Acceptance):
    return execute(req, lambda service: view(service, service.accept(
        req.task_id, req.expected_revision, req.kind, req.decision,
    )))


class StartJob(Operation):
    request_id: str = Field(min_length=1, max_length=128)


class JobRef(TaskRef):
    job_id: str = Field(min_length=1, max_length=128)


@router.post("/jobs/start", status_code=202)
def start_job(req: StartJob):
    return execute(req, lambda service: {"status": "ok", "job": service.start_job(
        req.task_id, req.expected_revision, req.kind, req.request_id,
    )})


@router.post("/jobs/status")
def job_status(req: JobRef):
    return execute(req, lambda service: {"status": "ok", "job": get_job_manager().get(
        service, req.task_id, req.job_id,
    )})


@router.post("/jobs/latest")
def latest_job(req: TaskRef):
    return execute(req, lambda service: {"status": "ok", "job": get_job_manager().latest(service, req.task_id)})


@router.post("/jobs/cancel")
def cancel_job(req: JobRef):
    return execute(req, lambda service: {"status": "ok", "job": get_job_manager().cancel(
        service, req.task_id, req.job_id,
    )})
