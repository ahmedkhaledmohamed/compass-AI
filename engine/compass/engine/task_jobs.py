"""Durable generation jobs with cancellation fenced at the artifact transaction."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import RLock, Timer
from uuid import uuid4

from compass.engine.native_inference import connection_binding
from compass.engine.provider_transport import ProviderError, RequestControl
from compass.engine.task_repository import TaskConflict, TaskNotFound, TaskRepository

ACTIVE = ("queued", "running")


class JobStopped(RuntimeError):
    """A cancelled, interrupted or expired job cannot publish an artifact."""


def check_job(db, job_id: str) -> None:
    row = db.execute("SELECT state, deadline FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or row[0] not in ACTIVE or row[1] <= time.time():
        raise JobStopped("Generation stopped; its result was not saved")


class JobContext:
    def __init__(self, manager, repository, job_id):
        self.manager = manager
        self.repository = repository
        self.id = job_id

    def stage(self, stage: str):
        with self.repository._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            check_job(db, self.id)
            db.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
                       (stage, time.time(), self.id))

    def check(self):
        with self.repository._connect() as db:
            check_job(db, self.id)

    def control(self):
        with self.repository._connect() as db:
            deadline = db.execute("SELECT deadline FROM jobs WHERE id=?", (self.id,)).fetchone()[0]

        def cancelled():
            with self.repository._connect() as db:
                row = db.execute("SELECT state FROM jobs WHERE id=?", (self.id,)).fetchone()
            return row is None or row[0] not in ACTIVE

        return RequestControl(max(0, min(300, deadline - time.time())), cancelled)

    def record_completion(self, completion):
        with self.repository._connect() as db:
            db.execute("UPDATE jobs SET completion=? WHERE id=?",
                       (json.dumps(completion.metadata()), self.id))


class JobManager:
    """One active generation per workspace and a bounded process-wide queue.

    The workspace lock is held for this engine's lifetime. A new engine may
    recover interrupted jobs only after the old engine has released its lock.
    No job is replayed automatically after a crash or restart.
    """

    def __init__(self):
        self._mutex = RLock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="compass-generation")
        self._locks: dict[str, int] = {}
        self._futures: dict[tuple[str, str], Future] = {}
        self._timers: dict[tuple[str, str], Timer] = {}
        self._closed = False
        self._imports: set[str] = set()

    def _own(self, repository: TaskRepository) -> str:
        workspace = str(repository.path.parent.resolve())
        with self._mutex:
            if self._closed:
                raise JobStopped("Engine is shutting down")
            if workspace in self._locks:
                return workspace
            if os.name != "posix":
                raise ValueError("Generation jobs currently require macOS or Linux")
            import fcntl

            lock_path = repository.path.parent / "jobs.lock"
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(fd)
                raise TaskConflict("This workspace is being used by another engine. Close its generation session first.") from exc
            try:
                with repository._connect() as db:
                    db.execute("UPDATE jobs SET state='interrupted', stage='stopped', "
                               "error_code='interrupted', message='Engine stopped. No request was replayed.', "
                               "updated_at=? WHERE state IN ('queued','running')", (time.time(),))
            except Exception:
                os.close(fd)
                raise
            self._locks[workspace] = fd
            return workspace

    def start(self, service, task_id, expected_revision, kind, request_id,
              orchestrator, timeout_seconds=120):
        if not 0 < timeout_seconds <= 300:
            raise ValueError("Generation deadline must be between zero and 300 seconds")
        repository = service.tasks
        workspace = self._own(repository)
        with self._mutex:
            with repository._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute("SELECT id, task_id, base_revision, kind FROM jobs WHERE request_id=?",
                                   (request_id,)).fetchone()
                if prior:
                    if tuple(prior[1:]) != (task_id, expected_revision, kind):
                        raise TaskConflict("Request ID was already used for a different operation")
                    return self.get(service, task_id, prior[0])
                if any(key[0] == workspace and not future.done()
                       for key, future in self._futures.items()):
                    raise TaskConflict("A previous provider request is still running or stopping in this workspace")
                if sum(not future.done() for future in self._futures.values()) >= 8:
                    raise TaskConflict("The generation queue is full. Wait for a running request to finish.")
                if workspace in self._imports:
                    raise TaskConflict("Finish or cancel the active import before generating a draft")
                task = repository._read(db, task_id)
                if task.revision != expected_revision:
                    raise TaskConflict("Task changed. Reload before generating.")
                service.prepare_generation(task, kind)
                job_id = uuid4().hex
                now = time.time()
                try:
                    db.execute("INSERT INTO jobs(id, task_id, base_revision, kind, request_id, state, stage, "
                               "created_at, updated_at, deadline, model, provider, connection) "
                               "VALUES (?, ?, ?, ?, ?, 'queued', 'queued', ?, ?, ?, ?, ?, ?)",
                               (job_id, task_id, expected_revision, kind, request_id, now, now,
                                now + timeout_seconds, orchestrator.default_model,
                                type(orchestrator.provider).__name__,
                                json.dumps(connection_binding(orchestrator.provider))))
                except sqlite3.IntegrityError as exc:
                    raise TaskConflict("A generation job is already active in this workspace") from exc
            key = (workspace, job_id)
            timer = Timer(timeout_seconds, self._stop, args=(repository, job_id, "timed_out"))
            timer.daemon = True
            self._timers[key] = timer
            future = self._executor.submit(self._run, service, job_id, task_id, expected_revision, kind, orchestrator)
            self._futures[key] = future
            future.add_done_callback(lambda _: self._finished(key))
            timer.start()
        return self.get(service, task_id, job_id)

    def _run(self, service, job_id, task_id, revision, kind, orchestrator):
        repository = service.tasks
        try:
            with repository._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                check_job(db, job_id)
                db.execute("UPDATE jobs SET state='running', stage='validating', updated_at=? WHERE id=?",
                           (time.time(), job_id))
            return service._generate(task_id, revision, kind, orchestrator,
                                     JobContext(self, repository, job_id))
        except JobStopped:
            # Deadline checks can race the timer; make the terminal state durable.
            self._stop(repository, job_id, "timed_out")
            raise
        except Exception as exc:
            from compass.engine.passage_retrieval import (
                RetrievalBudget,
                RetrievalEmpty,
                RetrievalError,
            )
            from compass.engine.task_service import ContextBudgetExceeded
            code, message = "provider_error", "The AI request failed. Check the selected connection and try again."
            if isinstance(exc, ProviderError):
                code, message = exc.code, str(exc)
                if exc.completion:
                    with repository._connect() as db:
                        db.execute("UPDATE jobs SET completion=? WHERE id=?", (json.dumps(exc.completion), job_id))
            elif isinstance(exc, TaskConflict):
                code, message = "task_changed", "Task or evidence changed during generation. Reload before trying again."
            elif isinstance(exc, (ContextBudgetExceeded, RetrievalBudget)):
                code, message = "context_limit", "Selected evidence exceeds the context limit. Select fewer sources; nothing was silently omitted."
            elif isinstance(exc, RetrievalEmpty):
                code, message = "retrieval_empty", str(exc)
            elif isinstance(exc, RetrievalError):
                code, message = "retrieval_error", str(exc)
            elif isinstance(exc, ValueError):
                code, message = "invalid_output", "The draft could not be validated. Nothing was saved; review the selected evidence and try again."
            with repository._connect() as db:
                db.execute("UPDATE jobs SET state='failed', stage='stopped', error_code=?, message=?, "
                           "updated_at=? WHERE id=? AND state IN ('queued','running')",
                           (code, message, time.time(), job_id))
            raise

    def _finished(self, key):
        with self._mutex:
            self._futures.pop(key, None)
            timer = self._timers.pop(key, None)
            if timer:
                timer.cancel()
            if self._closed:
                self._release_idle_locks()

    def _stop(self, repository, job_id, state):
        message = ("Generation deadline reached. Its result was not saved." if state == "timed_out"
                   else "Cancelled. An already-sent provider request may still incur usage; its result will not be saved.")
        with repository._connect() as db:
            db.execute("UPDATE jobs SET state=?, stage='stopped', error_code=?, message=?, updated_at=? "
                       "WHERE id=? AND state IN ('queued','running')",
                       (state, state, message, time.time(), job_id))

    def get(self, service, task_id, job_id):
        workspace = self._own(service.tasks)
        with service.tasks._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM jobs WHERE id=? AND task_id=?", (job_id, task_id)).fetchone()
            if row is None:
                raise TaskNotFound("Generation job not found in this task/workspace")
            result = dict(row)
        future = self._futures.get((workspace, job_id))
        result["draining"] = bool(future and not future.done() and result["state"] in ("cancelled", "timed_out", "interrupted"))
        result["completion"] = json.loads(result["completion"]) if result["completion"] else None
        result["connection"] = json.loads(result["connection"]) if result["connection"] else None
        result.pop("request_id", None)
        return result

    def latest(self, service, task_id):
        self._own(service.tasks)
        service.tasks.read(task_id)
        with service.tasks._connect() as db:
            row = db.execute("SELECT id FROM jobs WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                             (task_id,)).fetchone()
        return self.get(service, task_id, row[0]) if row else None

    def cancel(self, service, task_id, job_id):
        self.get(service, task_id, job_id)
        self._stop(service.tasks, job_id, "cancelled")
        future = self._futures.get((str(service.tasks.path.parent.resolve()), job_id))
        if future:
            future.cancel()
        return self.get(service, task_id, job_id)

    def wait(self, service, task_id, job_id):
        job = self.get(service, task_id, job_id)
        key = (str(service.tasks.path.parent.resolve()), job_id)
        future = self._futures.get(key)
        if future is None:
            if job["state"] == "succeeded":
                return service.tasks.read(task_id, job["result_revision"])
            if job["error_code"] == "task_changed":
                raise TaskConflict(job["message"])
            if job["error_code"] in ("invalid_output", "context_limit"):
                raise ValueError(job["message"])
            raise JobStopped(job["message"] or "Generation was interrupted")
        while True:
            remaining = job["deadline"] - time.time()
            if remaining <= 0:
                self._stop(service.tasks, job_id, "timed_out")
                raise JobStopped("Generation deadline reached; nothing was saved")
            try:
                return future.result(timeout=min(0.2, remaining))
            except CancelledError as exc:
                raise JobStopped("Generation was cancelled; nothing was saved") from exc
            except TimeoutError:
                job = self.get(service, task_id, job_id)
                if job["state"] in ("cancelled", "timed_out", "interrupted"):
                    raise JobStopped(job["message"]) from None

    def _release_idle_locks(self):
        for workspace, fd in list(self._locks.items()):
            if workspace not in self._imports and not any(key[0] == workspace and not future.done() for key, future in self._futures.items()):
                os.close(fd)
                del self._locks[workspace]

    def busy(self):
        with self._mutex:
            return bool(self._imports) or any(not future.done() for future in self._futures.values())

    def reserve_import(self, repository):
        workspace = self._own(repository)
        with self._mutex:
            if workspace in self._imports or any(key[0] == workspace and not future.done() for key, future in self._futures.items()):
                raise TaskConflict("Finish or cancel the active workspace operation before importing")
            self._imports.add(workspace)
        return workspace

    def release_import(self, workspace):
        with self._mutex:
            self._imports.discard(workspace)
            if self._closed:
                self._release_idle_locks()

    def busy_workspace(self, repository):
        workspace = str(repository.path.parent.resolve())
        with self._mutex:
            return workspace in self._imports or any(key[0] == workspace and not future.done() for key, future in self._futures.items())

    def close(self):
        with self._mutex:
            self._closed = True
            for (workspace, job_id), future in list(self._futures.items()):
                if not future.done():
                    repository = TaskRepository(Path(workspace).parent)
                    with repository._connect() as db:
                        db.execute("UPDATE jobs SET state='interrupted', stage='stopped', "
                                   "error_code='interrupted', message='Engine stopped. No request was replayed.', "
                                   "updated_at=? WHERE id=? AND state IN ('queued','running')", (time.time(), job_id))
                    future.cancel()
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._release_idle_locks()


_manager = None
_manager_lock = RLock()


def get_job_manager():
    global _manager
    with _manager_lock:
        if _manager is None or _manager._closed:
            _manager = JobManager()
        return _manager


def close_job_manager():
    with _manager_lock:
        if _manager is not None:
            _manager.close()
