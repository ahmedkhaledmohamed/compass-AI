"""Bounded desktop-local text ingestion with explicit coverage and cancellation."""
from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from compass.config import SourceConfig
from compass.engine.source_import import prepare_evidence
from compass.models.sources import Evidence, SourceType

MAX_FILES = 1_000
MAX_BYTES = 20_000_000
MAX_FILE_BYTES = 2_000_000
MAX_ENTRIES = 10_000
LOCAL_TYPES = {"code": SourceType.CODE, "docs": SourceType.DOCS, "interviews": SourceType.JUDGMENT,
               "analytics": SourceType.DATA, "support": SourceType.JUDGMENT}
TEXT = {".md", ".txt", ".rst", ".html"}
CODE = TEXT | {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".swift", ".go", ".rs", ".c", ".cpp",
               ".h", ".cs", ".sql", ".yaml", ".yml", ".toml", ".xml", ".css", ".json", ".sh", ".rb"}
EXCLUDED = {".git", ".compass", "node_modules", ".venv", "__pycache__"}


class ImportLimit(ValueError):
    """A source exceeded a declared bound; its previous evidence is preserved."""


def read_local_source(source: SourceConfig, check, progress):
    if source.type not in LOCAL_TYPES or source.url or source.auth or source.options or not source.path:
        raise ValueError("Choose a supported local source; live connectors require a separate authorized import.")
    root = Path(source.path)
    allowed = CODE if source.type == "code" else {".csv", ".tsv", ".json"} if source.type == "analytics" else TEXT | {".csv", ".tsv", ".json"} if source.type == "support" else TEXT
    coverage = {"entries_scanned": 0, "files_read": 0, "bytes_read": 0, "skipped_files": 0,
                "skipped_directories": 0, "skipped_examples": [], "truncated": False,
                "representation": "full_text", "limits": {"files": MAX_FILES, "bytes": MAX_BYTES,
                    "file_bytes": MAX_FILE_BYTES, "entries": MAX_ENTRIES}}
    pending = [root]
    items, signatures = [], []
    while pending:
        check()
        current = pending.pop()
        metadata = current.lstat()
        coverage["entries_scanned"] += 1
        if coverage["entries_scanned"] > MAX_ENTRIES:
            raise ImportLimit("Source exceeds 10,000 entries. Choose a smaller folder; prior evidence was kept.")
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("Source contains a symbolic link. Prior evidence was kept.")
        if stat.S_ISDIR(metadata.st_mode):
            with os.scandir(current) as directory:
                children = []
                for entry in directory:
                    check()
                    if entry.name in EXCLUDED:
                        coverage["skipped_directories"] += 1
                        continue
                    if len(children) + len(pending) + coverage["entries_scanned"] >= MAX_ENTRIES:
                        raise ImportLimit("Source exceeds 10,000 entries. Choose a smaller folder; prior evidence was kept.")
                    children.append(Path(entry.path))
                pending.extend(sorted(children, reverse=True))
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Source contains a special file. Prior evidence was kept.")
        if not current.resolve().is_relative_to(root.resolve()) or root.is_symlink():
            raise ValueError("A source file escaped its selected folder. Prior evidence was kept.")
        relative = current.relative_to(root).as_posix() if root.is_dir() else current.name
        if current.suffix.lower() not in allowed:
            coverage["skipped_files"] += 1
            if len(coverage["skipped_examples"]) < 10:
                coverage["skipped_examples"].append(relative)
            progress(coverage, relative)
            continue
        if metadata.st_size > MAX_FILE_BYTES:
            raise ImportLimit("A file exceeds 2 MB. Choose a smaller source file; prior evidence was kept.")
        if coverage["files_read"] >= MAX_FILES or coverage["bytes_read"] + metadata.st_size > MAX_BYTES:
            raise ImportLimit("Source exceeds 1,000 files or 20 MB. Choose fewer files; prior evidence was kept.")
        signature = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        # O_NOFOLLOW also protects the actual open after enumeration.
        descriptor = os.open(current, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != signature:
                raise ValueError("A source file changed during import. Retry; prior evidence was kept.")
            chunks, size = [], 0
            while True:
                check()
                chunk = handle.read(65_536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_FILE_BYTES or coverage["bytes_read"] + size > MAX_BYTES:
                    raise ImportLimit("Source changed beyond the import size limit; prior evidence was kept.")
        content = b"".join(chunks).decode("utf-8-sig")
        coverage["files_read"] += 1
        coverage["bytes_read"] += size
        signatures.append((current, signature))
        if content.strip():
            title = next((line[2:].strip() for line in content.splitlines()[:5] if line.startswith("# ")), current.name)
            items.append(Evidence(source_type=LOCAL_TYPES[source.type], connector=source.type, title=title[:200],
                content=content, timestamp=datetime.fromtimestamp(metadata.st_mtime, UTC),
                metadata={"file": str(current), "relative_path": relative, "bytes": size,
                    "format": current.suffix.lower(), "representation": "full_text"}))
        else:
            coverage["skipped_files"] += 1
        progress(coverage, relative)
    check()
    if not items:
        raise ValueError("No supported non-empty UTF-8 files were found. Prior evidence was kept.")
    for file, before in signatures:
        check()
        after = file.lstat()
        if stat.S_ISLNK(after.st_mode) or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != before:
            raise ValueError("A source file changed during import. Retry; prior evidence was kept.")
    coverage["complete"] = not coverage["skipped_files"] and not coverage["skipped_directories"]
    for item in items:
        item.metadata["source_coverage"] = {key: coverage[key] for key in ("files_read", "skipped_files", "skipped_directories", "complete", "representation")}
    return prepare_evidence(source, items), coverage
