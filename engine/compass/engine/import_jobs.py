"""Durable local import jobs; evidence and source results publish in one transaction."""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock
from uuid import uuid4

from compass.config import load_config
from compass.engine.evidence_repository import EvidenceRepository
from compass.engine.local_import import MAX_BYTES, MAX_FILES, ImportLimit, read_local_source
from compass.engine.task_jobs import get_job_manager
from compass.engine.task_repository import TaskConflict, TaskNotFound, TaskRepository

ACTIVE = ("queued", "running")


class ImportStopped(RuntimeError):
    pass


def fingerprint(source):
    return hashlib.sha256(json.dumps(source.model_dump(), sort_keys=True).encode()).hexdigest()


class ImportManager:
    def __init__(self, reader=read_local_source):
        self.reader = reader
        self.generation = get_job_manager()
        self.mutex = RLock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="compass-import")
        self.futures, self.stops, self.cache = {}, {}, {}
        self.owned = set()
        self.repositories = {}
        self.closed = False

    def _repository(self, root):
        root = Path(root).resolve()
        task_repository = TaskRepository(root)
        workspace = self.generation._own(task_repository)
        with self.mutex:
            repository = self.repositories.get(workspace)
            if repository is None:
                repository = EvidenceRepository(root / ".compass" / "knowledge")
                self.repositories[workspace] = repository
            if workspace not in self.owned:
                with repository.transaction() as db:
                    rows = db.execute("SELECT id,payload FROM import_jobs WHERE state IN ('queued','running')").fetchall()
                    for job_id, raw in rows:
                        payload = json.loads(raw)
                        for result in payload["results"]:
                            if result["state"] in ("queued", "reading"):
                                result["state"] = "interrupted"
                        payload.update(stage="stopped", message="Engine stopped. Completed sources were kept; unfinished sources were not replayed.")
                        db.execute("UPDATE import_jobs SET state='interrupted',payload=? WHERE id=?", (json.dumps(payload), job_id))
                self.owned.add(workspace)
        return root, workspace, repository, task_repository

    def _view(self, repository, job_id):
        with repository._lock:
            row = repository._db.execute("SELECT state,deadline,created_at,payload FROM import_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise TaskNotFound("Import job not found in this workspace")
        return {**json.loads(row[3]), "id": job_id, "state": row[0], "deadline": row[1], "created_at": row[2]}

    def start(self, root, source_ids, request_id, timeout_seconds=120):
        if not request_id or len(request_id) > 128 or not 0 < timeout_seconds <= 300:
            raise ValueError("A bounded request ID and import deadline are required")
        if not source_ids or len(source_ids) > 100 or len(source_ids) != len(set(source_ids)):
            raise ValueError("Select distinct sources to import")
        root, workspace, repository, task_repository = self._repository(root)
        config = load_config(root)
        selected = [next((source for source in config.sources if source.id == source_id), None) for source_id in source_ids]
        if any(source is None for source in selected):
            raise ValueError("A selected source is no longer configured")
        signature = [{"id": source.id, "name": source.name, "type": source.type, "config_hash": fingerprint(source)} for source in selected]
        with self.mutex:
            if self.closed:
                raise TaskConflict("The engine is shutting down")
            with repository._lock:
                prior = repository._db.execute("SELECT id,payload FROM import_jobs WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if json.loads(prior[1])["selection"] != signature:
                    raise TaskConflict("This request ID already belongs to a different source selection")
                return self.get(root, prior[0])
            if sum(not future.done() for future in self.futures.values()) >= 4:
                raise TaskConflict("The import queue is full. Wait for an active import.")
            self.generation.reserve_import(task_repository)
            job_id, now = uuid4().hex, time.time()
            payload = {"selection": signature, "stage": "queued", "message": "", "current_source": "",
                       "results": [{"source_id": source.id, "name": source.name, "type": source.type, "state": "queued", "items": 0} for source in selected]}
            try:
                with repository.transaction() as db:
                    db.execute("INSERT INTO import_jobs VALUES (?,?,?,?,?,?)", (job_id, request_id, "queued", now + timeout_seconds, now, json.dumps(payload)))
                key = (workspace, job_id)
                self.stops[key] = Event()
                self.cache[key] = {**payload, "id": job_id, "state": "queued", "deadline": now + timeout_seconds, "created_at": now}
                future = self.executor.submit(self._run, root, workspace, repository, job_id, selected, now + timeout_seconds)
                self.futures[key] = future
                future.add_done_callback(lambda _: self._finished(workspace, key))
            except Exception:
                self.generation.release_import(workspace)
                raise
        return self.get(root, job_id)

    def _finished(self, workspace, key):
        with self.mutex:
            self.futures.pop(key, None)
            self.stops.pop(key, None)
            self.cache.pop(key, None)
        self.generation.release_import(workspace)

    def _check(self, key, deadline):
        if self.closed or self.stops[key].is_set() or time.time() >= deadline:
            raise ImportStopped("Import stopped. Previously completed sources were kept.")

    def _update(self, repository, job_id, state=None, **changes):
        with repository.transaction() as db:
            row = db.execute("SELECT state,payload FROM import_jobs WHERE id=?", (job_id,)).fetchone()
            if row[0] not in ACTIVE:
                return
            payload = json.loads(row[1])
            payload.update(changes)
            db.execute("UPDATE import_jobs SET state=?,payload=? WHERE id=?", (state or row[0], json.dumps(payload), job_id))

    def _run(self, root, workspace, repository, job_id, sources, deadline):
        key = (workspace, job_id)
        check = lambda: self._check(key, deadline)
        results = [{"source_id": source.id, "name": source.name, "type": source.type, "state": "queued", "items": 0} for source in sources]
        try:
            check()
            self._update(repository, job_id, "running", stage="reading")
            for index, source in enumerate(sources):
                check()
                result = results[index]
                result["state"] = "reading"
                self._update(repository, job_id, current_source=source.name, results=results, stage="reading")
                last_report = 0
                def progress(coverage, file, result=result, source=source):
                    nonlocal last_report
                    check()
                    result.update(coverage=coverage.copy(), current_file=file)
                    if time.monotonic() - last_report > 0.1:
                        self._update(repository, job_id, current_source=source.name, results=results)
                        last_report = time.monotonic()
                try:
                    items, coverage = self.reader(source, check, progress)
                    check()
                    current = next((item for item in load_config(root).sources if item.id == source.id), None)
                    if current is None or fingerprint(current) != fingerprint(source):
                        raise ValueError("Source configuration changed. Retry; previous evidence was kept.")
                    with repository.transaction() as db:
                        check()
                        state = db.execute("SELECT state FROM import_jobs WHERE id=?", (job_id,)).fetchone()[0]
                        if state not in ACTIVE:
                            raise ImportStopped("Import stopped")
                        _, existing = repository._read()
                        retained = [item for item in existing if not (item.source_id == source.id if item.source_id else item.source_name in source.legacy_names)]
                        combined = retained + items
                        if len(combined) > MAX_FILES or sum(len(item.content.encode()) for item in combined) > MAX_BYTES:
                            raise ImportLimit("Workspace evidence exceeds 1,000 items or 20 MB. Prior evidence was kept.")
                        snapshot = repository._write(combined)
                        check()
                        published = {**result, "state": "completed", "items": len(items), "coverage": coverage, "snapshot_id": snapshot}
                        next_results = [*results]
                        next_results[index] = published
                        payload = json.loads(db.execute("SELECT payload FROM import_jobs WHERE id=?", (job_id,)).fetchone()[0])
                        payload.update(results=next_results, snapshot_id=snapshot, stage="reading", current_source=source.name)
                        db.execute("UPDATE import_jobs SET payload=? WHERE id=?", (json.dumps(payload), job_id))
                        check()
                    results[index] = published
                except ImportStopped:
                    raise
                except Exception as cause:  # noqa: BLE001 - Redacted per-source failure boundary.
                    result.update(state="failed", items=0, error_code="limit" if isinstance(cause, ImportLimit) else "source_error",
                                  message=str(cause) if isinstance(cause, ValueError) else "This source could not be read. Previous evidence was kept.")
                    self._update(repository, job_id, results=results)
            check()
            failures = sum(result["state"] == "failed" for result in results)
            incomplete = any(not result.get("coverage", {}).get("complete", True) for result in results)
            state = "failed" if failures == len(results) else "partial" if failures or incomplete else "completed"
            self._update(repository, job_id, state, stage="completed", results=results,
                         message="Some files or sources were omitted. Review coverage before using the evidence." if state == "partial" else "")
        except ImportStopped:
            state = "interrupted" if self.closed else "cancelled" if self.stops[key].is_set() else "timed_out"
            for result in results:
                if result["state"] in ("queued", "reading"):
                    result["state"] = state
            self._update(repository, job_id, state, stage="stopped", results=results,
                         message="Import stopped. Completed sources were kept; unfinished sources kept their previous evidence.")
        except Exception:  # noqa: BLE001 - Durable, redacted background-job failure boundary.
            self._update(repository, job_id, "failed", stage="stopped", results=results,
                         message="Import could not finish. Completed sources were kept; unfinished sources kept previous evidence.")

    def get(self, root, job_id=None):
        root, workspace, repository, _ = self._repository(root)
        if job_id is None:
            with repository._lock:
                row = repository._db.execute("SELECT id FROM import_jobs ORDER BY created_at DESC LIMIT 1").fetchone()
            if row is None:
                return None
            job_id = row[0]
        result = self._view(repository, job_id)
        key = (workspace, job_id)
        future = self.futures.get(key)
        result["draining"] = bool(future and not future.done() and result["state"] not in ACTIVE)
        selection = result.pop("selection", [])
        result["source_ids"] = [source["id"] for source in selection]
        if result["state"] in ACTIVE and (self.stops.get(key, Event()).is_set() or time.time() >= result["deadline"]):
            result["cancel_requested"] = True
            result["stage"] = "stopping"
            result["message"] = "Stopping import. Completed sources stay saved; unfinished sources keep their previous evidence."
        self.cache[key] = result
        return result

    def cancel(self, root, job_id):
        workspace = str(Path(root).resolve() / ".compass")
        key = (workspace, job_id)
        stopped = self.stops.get(key)
        cached = self.cache.get(key)
        if stopped is not None and cached is not None:
            if cached["state"] in ACTIVE:
                stopped.set()  # Stop before waiting for a source publication transaction.
                return {**{name:value for name,value in cached.items() if name!="selection"}, "cancel_requested": True, "stage": "stopping",
                        "message": "Stopping import. Completed sources stay saved; unfinished sources keep previous evidence."}
            return cached
        _, workspace, repository, _ = self._repository(root)
        result = self._view(repository, job_id)
        if result["state"] in ACTIVE and (workspace, job_id) not in self.futures:
            self._update(repository, job_id, "interrupted", stage="stopped",
                         message="The import worker stopped. Completed sources were kept; no source was replayed.")
        return self.get(root, job_id)

    def close(self):
        self.closed = True
        for stopped in list(self.stops.values()):
            stopped.set()
        for repository in list(self.repositories.values()):
            with repository.transaction() as db:
                for job_id, raw in db.execute("SELECT id,payload FROM import_jobs WHERE state IN ('queued','running')").fetchall():
                    payload = json.loads(raw)
                    for result in payload["results"]:
                        if result["state"] in ("queued", "reading"):
                            result["state"] = "interrupted"
                    payload.update(stage="stopped", message="Engine stopped. Completed sources were kept; unfinished sources were not replayed.")
                    db.execute("UPDATE import_jobs SET state='interrupted',payload=? WHERE id=?", (json.dumps(payload), job_id))
        self.executor.shutdown(wait=False, cancel_futures=True)


_manager = None
_manager_lock = RLock()


def get_import_manager():
    global _manager
    with _manager_lock:
        if _manager is None or _manager.closed:
            _manager = ImportManager()
        return _manager


def close_import_manager():
    with _manager_lock:
        if _manager:
            _manager.close()
