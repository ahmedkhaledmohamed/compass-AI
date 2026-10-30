"""Transactional task revisions with optimistic concurrency and retained history."""

import os
import sqlite3
import tempfile
import time
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
            if version not in (0, 1, 2, 3, 4):
                raise ValueError(f"Unsupported task database version: {version}")
            if version == 0:
                db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
                db.execute("CREATE TABLE revisions (task_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                           "payload TEXT NOT NULL, PRIMARY KEY(task_id, revision))")
            if version in (1, 2, 3):
                self._backup_version(db, version)
            if version in (0, 1):
                db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), "
                           "base_revision INTEGER NOT NULL, kind TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, "
                           "state TEXT NOT NULL, stage TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
                           "deadline REAL NOT NULL, model TEXT NOT NULL, provider TEXT NOT NULL, "
                           "error_code TEXT, message TEXT, result_revision INTEGER, completion TEXT)")
                db.execute("CREATE UNIQUE INDEX one_active_job ON jobs((1)) WHERE state IN ('queued','running')")
            if version == 2:
                db.execute("ALTER TABLE jobs ADD COLUMN completion TEXT")
            if version in (0, 1, 2, 3):
                db.execute("PRAGMA user_version=4")

    def _backup_version(self, db, version):
        directory = self.path.parent / "task-backups"
        if not directory.resolve().is_relative_to(self.path.parent.resolve()):
            raise ValueError("Task backup must remain inside the workspace")
        directory.mkdir(mode=0o700, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f"tasks-v{version}-", suffix=".tmp", dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                for statement in db.iterdump():
                    output.write(statement + "\n")
                output.write(f"PRAGMA user_version={version};\n")
                output.flush()
                os.fsync(output.fileno())
            with sqlite3.connect(":memory:") as restored:
                restored.executescript(temporary.read_text())
                original = db.execute("SELECT id, revision, payload FROM tasks ORDER BY id").fetchall()
                history = db.execute("SELECT task_id, revision, payload FROM revisions ORDER BY task_id, revision").fetchall()
                if (restored.execute("SELECT id, revision, payload FROM tasks ORDER BY id").fetchall() != original
                        or restored.execute("SELECT task_id, revision, payload FROM revisions ORDER BY task_id, revision").fetchall() != history):
                    raise ValueError("Task migration backup failed readback")
                if version >= 2 and restored.execute("SELECT * FROM jobs ORDER BY id").fetchall() != db.execute("SELECT * FROM jobs ORDER BY id").fetchall():
                    raise ValueError("Generation history backup failed readback")
            temporary.rename(temporary.with_suffix(".sql"))
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA foreign_keys=ON")
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

    def update(self, task_id: str, expected_revision: int, change: Callable[[Task], None],
               job_id: str | None = None) -> Task:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._read(db, task_id)
            if task.revision != expected_revision:
                raise TaskConflict("Task changed. Reload it before saving or generating again.")
            if job_id:
                from compass.engine.task_jobs import check_job
                check_job(db, job_id)
            change(task)
            task.revision += 1
            task.updated_at = now()
            # Validate the complete result before any write.
            task = Task.model_validate(task.model_dump())
            payload = task.model_dump_json()
            db.execute("INSERT INTO revisions VALUES (?, ?, ?)", (task.id, task.revision, payload))
            db.execute("UPDATE tasks SET revision=?, payload=? WHERE id=?",
                       (task.revision, payload, task.id))
            if job_id:
                check_job(db, job_id)
                db.execute("UPDATE jobs SET state='succeeded', stage='completed', result_revision=?, "
                           "updated_at=? WHERE id=?", (task.revision, time.time(), job_id))
            return task
