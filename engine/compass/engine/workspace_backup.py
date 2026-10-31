"""Portable data-only recovery. No SQL, source approvals or credentials are imported."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from uuid import uuid4

from compass.config import ProductConfig, load_config, save_config
from compass.engine.evidence_repository import EvidenceRepository
from compass.engine.task_jobs import get_job_manager
from compass.engine.task_repository import TaskRepository
from compass.engine.task_service import source_key
from compass.models.documents import StoredDocument
from compass.models.sources import Evidence
from compass.models.tasks import ConnectionBinding, Task, now

MAX_BYTES = 250_000_000
FORMAT = "compass-workspace-backup-v1"
JOB_COLUMNS = (
    "id",
    "task_id",
    "base_revision",
    "kind",
    "request_id",
    "state",
    "stage",
    "created_at",
    "updated_at",
    "deadline",
    "model",
    "provider",
    "error_code",
    "message",
    "result_revision",
    "completion",
    "connection",
)
IMPORT_COLUMNS = ("id", "request_id", "state", "deadline", "created_at", "payload")


class BackupLimit(ValueError):
    """The declared backup workload was exceeded without changing saved work."""


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def decode(value):
    def pairs(entries):
        result = {}
        for key, item in entries:
            if key in result:
                raise ValueError("Backup contains duplicate fields")
            result[key] = item
        return result

    def constant(_):
        raise ValueError("Backup contains non-finite numbers")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def regular_read(file: Path, limit=MAX_BYTES):
    descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("Choose a regular Compass backup of at most 250 MB")
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
        if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise ValueError("The backup changed while reading. Select it again.")
        return data.decode("utf-8")


def product_data(config):
    return {
        "name": config.name,
        "description": config.description,
        "model": config.model,
        "sources": [
            {"id": s.id, "type": s.type, "name": s.name, "legacy_names": s.legacy_names}
            for s in config.sources
        ],
    }


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {
        "created_at",
        "product",
        "snapshots",
        "active_snapshot",
        "tasks",
        "revisions",
        "jobs",
        "imports",
        "documents",
    }:
        raise ValueError("Unsupported workspace backup structure")
    product = payload["product"]
    if (
        not isinstance(product, dict)
        or set(product) != {"name", "description", "model", "sources"}
        or not isinstance(product["sources"], list)
        or len(product["sources"]) > 100
    ):
        raise ValueError("Invalid backed-up product details")
    for source in product["sources"]:
        if not isinstance(source, dict) or set(source) != {"id", "type", "name", "legacy_names"}:
            raise ValueError(
                "Backup source configuration must exclude paths, options and authorization"
            )
    config = ProductConfig(**product)
    if len(config.name) > 200 or len(config.description) > 4000 or len(config.model) > 256:
        raise ValueError("Backed-up product details exceed supported limits")
    for key in ("snapshots", "tasks", "revisions", "jobs", "imports", "documents"):
        if not isinstance(payload[key], list) or len(payload[key]) > 50_000:
            raise ValueError("Backup exceeds the supported record limit")
    snapshots = {}
    total_items = 0
    for row in payload["snapshots"]:
        if (
            set(row) != {"id", "payload"}
            or not isinstance(row["payload"], str)
            or hashlib.sha256(row["payload"].encode()).hexdigest() != row["id"]
            or row["id"] in snapshots
        ):
            raise ValueError("Backup evidence checksum mismatch or duplicate snapshot")
        raw = decode(row["payload"])
        if not isinstance(raw, list):
            raise TypeError("Invalid evidence snapshot")
        total_items += len(raw)
        if total_items > 200_000:
            raise ValueError("Backup exceeds the supported evidence history limit")
        items = [Evidence.model_validate(value) for value in raw]
        if len({item.id for item in items}) != len(items):
            raise ValueError("Duplicate evidence in backed-up snapshot")
        snapshots[row["id"]] = {item.id: item for item in items}
    if payload["active_snapshot"] not in snapshots:
        raise ValueError("Backup active evidence snapshot is missing")
    current, revisions = {}, {}
    for table, target in (("tasks", current), ("revisions", revisions)):
        for row in payload[table]:
            if set(row) != {"id", "revision", "payload"}:
                raise ValueError("Invalid task record")
            task = Task.model_validate_json(row["payload"])
            key = task.id if table == "tasks" else (task.id, task.revision)
            if (
                not re.fullmatch(r"[a-f0-9]{32}", task.id)
                or key in target
                or task.id != row["id"]
                or task.revision != row["revision"]
                or task.evidence_snapshot not in snapshots
            ):
                raise ValueError("Invalid task identity or missing evidence snapshot")
            target[key] = task
            selected = {
                key: item
                for key, item in snapshots[task.evidence_snapshot].items()
                if source_key(item) in task.source_names or item.source_name in task.source_names
            }
            for artifact in (task.brief, task.handoff):
                if artifact:
                    for claim in artifact.body.claims:
                        for citation in claim.citations:
                            item = selected.get(citation.evidence_id)
                            if (
                                item is None
                                or not 0 <= citation.start < citation.end <= len(item.content)
                                or item.content[citation.start : citation.end] != citation.quote
                            ):
                                raise ValueError(
                                    "Backup citation does not match its selected saved evidence"
                                )
                    if (
                        artifact.retrieval
                        and artifact.retrieval.snapshot_id != task.evidence_snapshot
                    ):
                        raise ValueError("Backup retrieval snapshot does not match its task")
    for identifier, task in current.items():
        if (identifier, task.revision) not in revisions or revisions[
            identifier, task.revision
        ] != task:
            raise ValueError("Backup current task differs from revision history")
    if any(identifier not in current for identifier, _ in revisions):
        raise ValueError("Backup contains orphan task revisions")
    for table, columns in (("jobs", JOB_COLUMNS), ("imports", IMPORT_COLUMNS)):
        ids, requests = set(), set()
        for row in payload[table]:
            if (
                set(row) != set(columns)
                or not isinstance(row["id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", row["id"])
                or row["id"] in ids
                or not isinstance(row["request_id"], str)
                or not row["request_id"]
                or len(row["request_id"]) > 128
                or row["request_id"] in requests
            ):
                raise ValueError("Invalid or duplicate backed-up job")
            for value in row.values():
                if value is not None and (
                    type(value) not in (str, int, float)
                    or type(value) is int
                    and not -(2**63) <= value < 2**63
                    or type(value) is float
                    and not math.isfinite(value)
                ):
                    raise ValueError("Backed-up job contains an unsupported value")
            ids.add(row["id"])
            requests.add(row["request_id"])
            if row["state"] not in (
                "queued",
                "running",
                "succeeded",
                "completed",
                "partial",
                "failed",
                "cancelled",
                "timed_out",
                "interrupted",
            ) or any(
                isinstance(row[key], bool)
                or not isinstance(row[key], (float, int))
                or not math.isfinite(row[key])
                for key in ("deadline", "created_at")
            ):
                raise ValueError("Invalid backed-up job state or timestamp")
            if table == "jobs":
                if any(
                    not isinstance(row[key], str) or len(row[key]) > 2048
                    for key in ("task_id", "kind", "stage", "model", "provider")
                ):
                    raise ValueError("Invalid generation metadata")
                if any(
                    row[key] is not None and not isinstance(row[key], str)
                    for key in ("error_code", "message", "completion", "connection")
                ):
                    raise ValueError("Invalid generation completion fields")
                if (
                    row["state"] in ("completed", "partial")
                    or type(row["base_revision"]) is not int
                    or row["base_revision"] < 1
                    or not isinstance(row["updated_at"], (int, float))
                ):
                    raise ValueError("Invalid generation state or revision")
                if (
                    row["task_id"] not in current
                    or (row["task_id"], row["base_revision"]) not in revisions
                    or row["kind"] not in ("brief", "handoff")
                ):
                    raise ValueError("Backed-up generation references a missing task revision")
                if (
                    row["result_revision"] is not None
                    and (row["task_id"], row["result_revision"]) not in revisions
                ):
                    raise ValueError("Backed-up generation result is missing")
                if row["connection"] is not None:
                    ConnectionBinding.model_validate(decode(row["connection"]))
                if row["completion"] is not None:
                    completion = decode(row["completion"])
                    if (
                        not isinstance(completion, dict)
                        or set(completion) != {"model", "provider", "input_tokens", "output_tokens"}
                        or any(
                            not isinstance(completion[key], str)
                            or not completion[key]
                            or len(completion[key]) > 256
                            for key in ("model", "provider")
                        )
                    ):
                        raise ValueError("Invalid generation completion metadata")
                    for key in ("input_tokens", "output_tokens"):
                        if completion[key] is not None and (
                            type(completion[key]) is not int or completion[key] < 0
                        ):
                            raise ValueError("Invalid generation usage metadata")
            else:
                if row["state"] == "succeeded":
                    raise ValueError("Invalid import state")
                data = decode(row["payload"])
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("results"), list)
                    or len(data["results"]) > 100
                    or not isinstance(data.get("selection"), list)
                    or any(
                        not isinstance(result, dict)
                        or result.get("state")
                        not in (
                            "queued",
                            "reading",
                            "completed",
                            "failed",
                            "cancelled",
                            "timed_out",
                            "interrupted",
                        )
                        for result in data["results"]
                    )
                ):
                    raise ValueError("Invalid backed-up import history")
                if data.get("snapshot_id") and data["snapshot_id"] not in snapshots:
                    raise ValueError("Backed-up import references a missing snapshot")
                if any(
                    key in data and not isinstance(data[key], str)
                    for key in ("stage", "message", "current_source")
                ):
                    raise ValueError("Invalid backed-up import status text")
                for source in data["selection"]:
                    if (
                        not isinstance(source, dict)
                        or not re.fullmatch(r"src_[a-f0-9]{32}", str(source.get("id", "")))
                        or any(
                            not isinstance(source.get(key), str)
                            for key in ("name", "type", "config_hash")
                        )
                    ):
                        raise ValueError("Invalid backed-up import selection")
                for result in data["results"]:
                    if (
                        not re.fullmatch(r"src_[a-f0-9]{32}", str(result.get("source_id", "")))
                        or not isinstance(result.get("name"), str)
                        or type(result.get("items")) is not int
                        or result["items"] < 0
                    ):
                        raise ValueError("Invalid backed-up source result")
                    if result.get("snapshot_id") and result["snapshot_id"] not in snapshots:
                        raise ValueError("Backed-up source result references a missing snapshot")
                    if any(
                        key in result and not isinstance(result[key], str)
                        for key in ("message", "current_file", "error_code")
                    ):
                        raise ValueError("Invalid backed-up source status text")
                    coverage = result.get("coverage")
                    if coverage is not None and (
                        not isinstance(coverage, dict)
                        or any(
                            type(coverage.get(key)) is not int or coverage[key] < 0
                            for key in (
                                "files_read",
                                "bytes_read",
                                "skipped_files",
                                "skipped_directories",
                            )
                        )
                        or not isinstance(coverage.get("skipped_examples"), list)
                        or any(not isinstance(item, str) for item in coverage["skipped_examples"])
                    ):
                        raise ValueError("Invalid backed-up import coverage")
    documents = set()
    for row in payload["documents"]:
        if (
            set(row) != {"id", "payload"}
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", row["id"])
            or row["id"] in documents
        ):
            raise ValueError("Invalid legacy document identifier")
        document = StoredDocument.model_validate_json(row["payload"])
        if document.id != row["id"]:
            raise ValueError("Legacy document identity mismatch")
        documents.add(row["id"])
    return config


def create_backup(root: Path, destination: Path):
    if not destination.is_absolute() or not destination.name.endswith(".compass-backup.json"):
        raise ValueError("Choose a .compass-backup.json destination")
    load_config(root)
    tasks = TaskRepository(root)
    manager = get_job_manager()
    lease = manager.reserve_import(tasks)
    evidence = None
    try:
        config = load_config(root)
        knowledge = root / ".compass" / "knowledge"
        if knowledge.is_symlink() or (knowledge / EvidenceRepository.FILE).is_symlink():
            raise ValueError("Backup evidence storage cannot be a symbolic link")
        evidence = EvidenceRepository(knowledge)
        with tasks._connect() as task_db, evidence.transaction() as evidence_db:
            task_db.execute("BEGIN IMMEDIATE")
            size = sum(
                db.execute(
                    f"SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM {table}"
                ).fetchone()[0]
                for db, table in (
                    (task_db, "tasks"),
                    (task_db, "revisions"),
                    (evidence_db, "snapshots"),
                )
            )
            if size > MAX_BYTES:
                raise BackupLimit(
                    "Workspace history exceeds the 250 MB backup limit; saved work was kept"
                )

            def rows(db, table, columns):
                return [
                    dict(zip(columns, row, strict=True))
                    for row in db.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid")
                ]

            payload = {
                "created_at": now(),
                "product": product_data(config),
                "snapshots": rows(evidence_db, "snapshots", ("id", "payload")),
                "active_snapshot": evidence_db.execute(
                    "SELECT snapshot_id FROM active_snapshot WHERE singleton=1"
                ).fetchone()[0],
                "tasks": rows(task_db, "tasks", ("id", "revision", "payload")),
                "revisions": rows(task_db, "revisions", ("task_id", "revision", "payload")),
                "jobs": rows(task_db, "jobs", JOB_COLUMNS),
                "imports": rows(evidence_db, "import_jobs", IMPORT_COLUMNS),
                "documents": [],
            }
            payload["revisions"] = [
                {"id": row["task_id"], "revision": row["revision"], "payload": row["payload"]}
                for row in payload["revisions"]
            ]
        docs = root / ".compass" / "documents"
        if docs.is_symlink():
            raise ValueError("Legacy document storage cannot be a symbolic link")
        if docs.exists():
            for file in sorted(docs.glob("*.json")):
                payload["documents"].append({"id": file.stem, "payload": regular_read(file)})
        validate(payload)
        checksum = hashlib.sha256(encode(payload).encode()).hexdigest()
        archive = {"format": FORMAT, "sha256": checksum, "payload": payload}
        descriptor, temporary = tempfile.mkstemp(
            prefix=".compass-backup-", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                count = 0
                for chunk in json.JSONEncoder(
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).iterencode(archive):
                    count += len(chunk.encode())
                    if count > MAX_BYTES:
                        raise BackupLimit(
                            "Workspace history exceeds the 250 MB backup limit; saved work was kept"
                        )
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            read_backup(Path(temporary))
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {
            "tasks": len(payload["tasks"]),
            "revisions": len(payload["revisions"]),
            "snapshots": len(payload["snapshots"]),
            "documents": len(payload["documents"]),
            "bytes": count,
            "sha256": checksum,
        }
    finally:
        if evidence:
            evidence._db.close()
        manager.release_import(lease)


def read_backup(file: Path):
    archive = decode(regular_read(file))
    if (
        not isinstance(archive, dict)
        or set(archive) != {"format", "sha256", "payload"}
        or archive["format"] != FORMAT
        or hashlib.sha256(encode(archive["payload"]).encode()).hexdigest() != archive["sha256"]
    ):
        raise ValueError("Backup format or checksum is invalid; existing work was kept")
    validate(archive["payload"])
    return archive["payload"]


def restore_backup(file: Path, parent: Path):
    payload = read_backup(file)
    config = validate(payload)
    if not parent.is_absolute() or not parent.is_dir():
        raise ValueError("Choose a parent folder for the restored workspace")
    stage = Path(tempfile.mkdtemp(prefix=".compass-restore-", dir=parent))
    evidence = None
    try:
        storage = stage / ".compass"
        repository = TaskRepository(stage)
        evidence = EvidenceRepository(storage / "knowledge")
        expected_imports = []
        expected_jobs = []
        with evidence.transaction() as db:
            db.execute("DELETE FROM active_snapshot")
            db.execute("DELETE FROM snapshots")
            db.executemany(
                "INSERT INTO snapshots VALUES (?,?)",
                [(row["id"], row["payload"]) for row in payload["snapshots"]],
            )
            db.execute("INSERT INTO active_snapshot VALUES (1,?)", (payload["active_snapshot"],))
            for original in payload["imports"]:
                row = dict(original)
                if row["state"] in ("queued", "running"):
                    data = decode(row["payload"])
                    for result in data["results"]:
                        if result.get("state") in ("queued", "reading"):
                            result["state"] = "interrupted"
                    data.update(
                        stage="stopped",
                        message="Restored from backup. Unfinished sources were not replayed.",
                    )
                    row.update(state="interrupted", payload=encode(data))
                values = tuple(row[key] for key in IMPORT_COLUMNS)
                expected_imports.append(values)
                db.execute(
                    f"INSERT INTO import_jobs({','.join(IMPORT_COLUMNS)}) VALUES ({','.join('?' for _ in IMPORT_COLUMNS)})",
                    values,
                )
        evidence._db.close()
        evidence = None
        with repository._connect() as db:
            db.executemany(
                "INSERT INTO tasks VALUES (?,?,?)",
                [(row["id"], row["revision"], row["payload"]) for row in payload["tasks"]],
            )
            db.executemany(
                "INSERT INTO revisions VALUES (?,?,?)",
                [(row["id"], row["revision"], row["payload"]) for row in payload["revisions"]],
            )
            for original in payload["jobs"]:
                row = dict(original)
                if row["state"] in ("queued", "running"):
                    row.update(
                        state="interrupted",
                        stage="stopped",
                        error_code="interrupted",
                        message="Restored from backup. No inference request was replayed.",
                    )
                values = tuple(row[key] for key in JOB_COLUMNS)
                expected_jobs.append(values)
                db.execute(
                    f"INSERT INTO jobs({','.join(JOB_COLUMNS)}) VALUES ({','.join('?' for _ in JOB_COLUMNS)})",
                    values,
                )
        documents = storage / "documents"
        documents.mkdir(mode=0o700)
        for row in payload["documents"]:
            descriptor = os.open(
                documents / f"{row['id']}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(row["payload"])
                output.flush()
                os.fsync(output.fileno())
        save_config(config, stage)
        # Read back the new stores before publishing the complete workspace folder.
        with repository._connect() as db:
            if (
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("SELECT id,revision,payload FROM tasks ORDER BY rowid").fetchall()
                != [(row["id"], row["revision"], row["payload"]) for row in payload["tasks"]]
                or db.execute(
                    "SELECT task_id,revision,payload FROM revisions ORDER BY rowid"
                ).fetchall()
                != [(row["id"], row["revision"], row["payload"]) for row in payload["revisions"]]
                or db.execute(f"SELECT {','.join(JOB_COLUMNS)} FROM jobs ORDER BY rowid").fetchall()
                != expected_jobs
            ):
                raise ValueError("Restored task history failed readback")
        check = EvidenceRepository(storage / "knowledge")
        try:
            for row in payload["snapshots"]:
                check.read(row["id"])
            if (
                check._db.execute(
                    f"SELECT {','.join(IMPORT_COLUMNS)} FROM import_jobs ORDER BY rowid"
                ).fetchall()
                != expected_imports
            ):
                raise ValueError("Restored import history failed readback")
        finally:
            check._db.close()
        destination = parent / f"Compass restored {uuid4().hex}"
        if destination.exists():
            raise ValueError("Choose another restore destination")
        os.rename(stage, destination)
        return {
            "workspace_path": str(destination),
            "name": config.name,
            "description": config.description,
            "tasks": len(payload["tasks"]),
            "snapshots": len(payload["snapshots"]),
            "documents": len(payload["documents"]),
        }
    except Exception:
        if evidence:
            evidence._db.close()
        shutil.rmtree(stage)
        raise
