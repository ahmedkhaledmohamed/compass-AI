"""Transactional task revisions with optimistic concurrency and retained history."""

import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from compass.models.tasks import Task, now


class TaskConflict(ValueError):
    """The caller is editing an obsolete task revision."""


class TaskNotFound(ValueError):
    """The task does not exist in the selected workspace."""


class TaskRepository:
    def __init__(self, workspace: Path):
        root = workspace.resolve()
        directory = root / ".compass"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory.resolve().is_relative_to(root):
            raise ValueError("Task storage must remain inside the selected workspace")
        self.path = directory / "tasks.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Task database cannot be a symbolic link")
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported task database version: {version}")
            if version == 0:
                db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
                db.execute("CREATE TABLE revisions (task_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                           "payload TEXT NOT NULL, PRIMARY KEY(task_id, revision))")
                db.execute("PRAGMA user_version=1")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _read(db, task_id: str, revision: int | None = None) -> Task:
        if revision is None:
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        else:
            row = db.execute("SELECT payload FROM revisions WHERE task_id=? AND revision=?",
                             (task_id, revision)).fetchone()
        if row is None:
            raise TaskNotFound("Task or revision not found in this workspace")
        return Task.model_validate_json(row[0])

    def read(self, task_id: str, revision: int | None = None) -> Task:
        with self._connect() as db:
            return self._read(db, task_id, revision)

    def list(self) -> list[Task]:
        with self._connect() as db:
            return [Task.model_validate_json(row[0]) for row in db.execute(
                "SELECT payload FROM tasks ORDER BY rowid DESC"
            )]

    def create(self, task: Task) -> Task:
        with self._connect() as db:
            db.execute("INSERT INTO tasks VALUES (?, ?, ?)",
                       (task.id, task.revision, task.model_dump_json()))
            db.execute("INSERT INTO revisions VALUES (?, ?, ?)",
                       (task.id, task.revision, task.model_dump_json()))
        return task

    def update(self, task_id: str, expected_revision: int, change: Callable[[Task], None]) -> Task:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._read(db, task_id)
            if task.revision != expected_revision:
                raise TaskConflict("Task changed. Reload it before saving or generating again.")
            change(task)
            task.revision += 1
            task.updated_at = now()
            # Validate the complete result before any write.
            task = Task.model_validate(task.model_dump())
            payload = task.model_dump_json()
            db.execute("INSERT INTO revisions VALUES (?, ?, ?)", (task.id, task.revision, payload))
            db.execute("UPDATE tasks SET revision=?, payload=? WHERE id=?",
                       (task.revision, payload, task.id))
            return task
