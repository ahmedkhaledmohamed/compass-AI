"""Native-only workspace recovery operations, authenticated by the engine session."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import Field

from compass.engine.task_repository import TaskConflict
from compass.engine.workspace_backup import BackupLimit, create_backup, restore_backup
from compass.models.tasks import StrictModel

router = APIRouter(prefix="/workspace")


class BackupRequest(StrictModel):
    workspace_path: str = Field(min_length=1, max_length=4096)
    backup_path: str = Field(min_length=1, max_length=4096)


class RestoreRequest(StrictModel):
    backup_path: str = Field(min_length=1, max_length=4096)
    parent_path: str = Field(min_length=1, max_length=4096)


def execute(operation):
    try:
        return {"status": "ok", **operation()}
    except BackupLimit as cause:
        raise HTTPException(
            422,
            "Workspace history exceeds the 250 MB backup limit. Existing work was kept; export selected reviewed artifacts or keep an independent copy of the workspace.",
        ) from cause
    except TaskConflict as cause:
        raise HTTPException(
            409, "Finish or cancel the active workspace operation before backing up"
        ) from cause
    except (ValueError, TypeError, KeyError, RecursionError, AttributeError) as cause:
        raise HTTPException(
            422,
            "Backup validation failed. Choose a supported, intact Compass backup; saved work was kept.",
        ) from cause
    except OSError as cause:
        raise HTTPException(
            422,
            "Backup files could not be accessed or saved. Check the selected drive and permissions; existing work was kept.",
        ) from cause


@router.post("/backup")
def backup(req: BackupRequest):
    return execute(lambda: create_backup(Path(req.workspace_path), Path(req.backup_path)))


@router.post("/restore")
def restore(req: RestoreRequest):
    return execute(lambda: restore_backup(Path(req.backup_path), Path(req.parent_path)))
