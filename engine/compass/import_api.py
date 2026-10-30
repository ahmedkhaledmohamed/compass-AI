"""Authenticated desktop import operations, scoped to a selected workspace."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import Field

from compass.config import load_config
from compass.engine.import_jobs import get_import_manager
from compass.engine.task_repository import TaskConflict, TaskNotFound
from compass.models.tasks import StrictModel

router = APIRouter(prefix="/imports")


class Workspace(StrictModel):
    workspace_path: str = Field(min_length=1, max_length=4096)


class StartImport(Workspace):
    source_ids: list[str] = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=128)


class ImportRef(Workspace):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def execute(req, operation):
    try:
        workspace = Path(req.workspace_path)
        load_config(workspace)
        return {"status": "ok", "job": operation(get_import_manager(), workspace)}
    except TaskNotFound as cause:
        raise HTTPException(404, str(cause)) from cause
    except TaskConflict as cause:
        raise HTTPException(409, str(cause)) from cause
    except FileNotFoundError as cause:
        raise HTTPException(404, "Workspace not found") from cause
    except ValueError as cause:
        raise HTTPException(422, str(cause)) from cause


@router.post("/start", status_code=202)
def start(req: StartImport):
    return execute(req, lambda manager, workspace: manager.start(workspace, req.source_ids, req.request_id))


@router.post("/status")
def status(req: ImportRef):
    return execute(req, lambda manager, workspace: manager.get(workspace, req.job_id))


@router.post("/latest")
def latest(req: Workspace):
    return execute(req, lambda manager, workspace: manager.get(workspace))


@router.post("/cancel")
def cancel(req: ImportRef):
    return execute(req, lambda manager, workspace: manager.cancel(workspace, req.job_id))
