"""Deterministic, selected-snapshot passage retrieval without external services."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter

from compass.models.sources import Evidence
from compass.models.tasks import Citation, PassageLocator, RetrievalReceipt

CHUNK_CHARS = 3_000
OVERLAP = 300
MAX_PASSAGES = 16
MAX_DOCUMENTS = 1_000
MAX_BYTES = 20_000_000
STOP = {"a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could", "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "may", "of", "on", "or", "our", "should", "that", "the", "their", "them", "these", "they", "this", "to", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you", "your"}


class RetrievalError(RuntimeError):
    """Local retrieval failed; an empty result must not hide the error."""


class RetrievalEmpty(ValueError):
    """No relevant passages were found within the selected task scope."""


class RetrievalBudget(ValueError):
    """Accepted support cannot fit the bounded model context."""


def coverage_for(item: Evidence):
    raw = item.metadata.get("source_coverage")
    if not isinstance(raw, dict) or not isinstance(raw.get("complete"), bool):
        return None
    return {"complete":raw["complete"], "representation": "full_text" if raw.get("representation")=="full_text" else "unknown",
            **{key:value for key in ("files_read","skipped_files","skipped_directories")
               if isinstance(value:=raw.get(key), int) and 0<=value<=10_000}}


def query_terms(query: str):
    unique = list(dict.fromkeys(word.casefold() for word in re.findall(r"[^\W_]+", query, re.UNICODE)
                                if 2 <= len(word) <= 80 and word.casefold() not in STOP))
    return unique[:64], len(unique) > 64


def line_breaks(text: str, start: int, end: int) -> int:
    count = text.count("\n", start, end) + text.count("\r", start, end) - text.count("\r\n", start, end)
    return count - int(end > start and end < len(text) and text[end - 1] == "\r" and text[end] == "\n")


def line_range(text: str, start: int, end: int):
    first = line_breaks(text, 0, start) + 1
    ended = text[end - 1] == "\n" or (text[end - 1] == "\r" and (end == len(text) or text[end] != "\n"))
    return first, first + line_breaks(text, start, end) - int(ended)


def passages_for(item: Evidence):
    content = item.content
    document = item.document_id or "doc_" + hashlib.sha256(f"{item.source_id or item.source_name}:{item.id}".encode()).hexdigest()
    version = item.content_hash or hashlib.sha256(content.encode()).hexdigest()
    start, line = 0, 1
    while start < len(content):
        end = min(start + CHUNK_CHARS, len(content))
        if end < len(content):
            boundary = max(content.rfind("\n", start + CHUNK_CHARS * 4 // 5, end),
                           content.rfind(" ", start + CHUNK_CHARS * 4 // 5, end))
            if boundary >= 0:
                end = boundary + 1
        text = content[start:end]
        if text:
            chunk_id = "chk_" + hashlib.sha256(f"{document}:{version}:{start}:{end}".encode()).hexdigest()
            yield {"id": item.id, "document_id": document, "version": version, "chunk_id": chunk_id,
                   "source": item.source_id or item.source_name or f"legacy:{item.connector}",
                   "source_name": item.source_name, "source_type": item.source_type.value,
                   "title": item.title[:240], "start": start, "end": end, "line_start": line,
                   "line_end": line + line_breaks(content, start, end) - int(text.endswith("\n") or (text.endswith("\r") and (end==len(content) or content[end]!="\n"))),
                   "content": text, "coverage": coverage_for(item)}
        if end == len(content):
            break
        next_start = max(start + 1, end - OVERLAP)
        line += line_breaks(content, start, next_start)
        start = next_start


def required_passage(item: Evidence, citation: Citation):
    if not 0 <= citation.start < citation.end <= len(item.content) or item.content[citation.start:citation.end] != citation.quote:
        raise RetrievalError("Accepted brief support no longer matches the selected saved evidence")
    start, end = max(0, citation.start - 200), min(len(item.content), citation.end + 200)
    document = item.document_id or "doc_" + hashlib.sha256(f"{item.source_id or item.source_name}:{item.id}".encode()).hexdigest()
    version = item.content_hash or hashlib.sha256(item.content.encode()).hexdigest()
    first, last = line_range(item.content, start, end)
    return {"id":item.id,"document_id":document,"version":version,
            "chunk_id":"chk_"+hashlib.sha256(f"{document}:{version}:{start}:{end}".encode()).hexdigest(),
            "source":item.source_id or item.source_name or f"legacy:{item.connector}", "source_name":item.source_name,
            "source_type":item.source_type.value,"title":item.title[:240],"start":start,"end":end,"line_start":first,"line_end":last,
            "content":item.content[start:end],"coverage":coverage_for(item),"reason":"accepted_brief"}


def receipt_for(snapshot, chunks, selected, terms, truncated, matches, items):
    total_chars = sum(len(item.content) for item in items)
    intervals = {}
    for passage in selected:
        intervals.setdefault(passage["id"], []).append((passage["start"], passage["end"]))
    provided = 0
    for ranges in intervals.values():
        previous_end = 0
        for start, end in sorted(ranges):
            provided += max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
    return RetrievalReceipt(
        snapshot_id=snapshot, status="empty" if not selected else "complete" if provided == total_chars else "partial",
        query_terms=terms, query_truncated=truncated, selected_documents=len(items), total_passages=len(chunks),
        selection_mode="all_saved_text" if matches is None else "keyword_ranked", matched_passages=matches, total_characters=total_chars, provided_characters=provided,
        import_omission_documents=sum((coverage_for(item) or {}).get("complete") is False for item in items),
        unknown_import_documents=sum(coverage_for(item) is None for item in items),
        passages=[PassageLocator(evidence_id=p["id"], document_id=p["document_id"], version=p["version"],
            chunk_id=p["chunk_id"], start=p["start"], end=p["end"], line_start=p["line_start"], line_end=p["line_end"],
            rank=rank + 1, score=p.get("score"), reason=p.get("reason", "all_saved_text" if matches is None else "keyword_ranked")) for rank, p in enumerate(selected)],
    )


def trim_receipt(receipt: RetrievalReceipt, selected):
    ranges = {}
    for passage in selected:
        ranges.setdefault(passage["id"], []).append((passage["start"], passage["end"]))
    provided = 0
    for intervals in ranges.values():
        previous_end = 0
        for start, end in sorted(intervals):
            provided += max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
    return receipt.model_copy(update={
        "status": "empty" if not selected else "complete" if provided == receipt.total_characters else "partial",
        "provided_characters": provided, "passages": receipt.passages[:len(selected)],
    })


def retrieve(items: list[Evidence], snapshot: str, query: str, budget: int = 45_000, check=lambda: None, search: bool = False, required: list[Citation] | None = None):
    if len(items) > MAX_DOCUMENTS or sum(len(item.content.encode()) for item in items) > MAX_BYTES:
        raise RetrievalError("Selected evidence exceeds local search's 1,000-document or 20 MB limit. Select fewer sources; saved evidence was kept.")
    terms, truncated = query_terms(query)
    chunks = []
    for item in sorted(items, key=lambda entry: (entry.source_id or entry.source_name, entry.document_id or entry.id)):
        check()
        chunks.extend(passages_for(item))
    required_chunks = []
    by_id = {item.id:item for item in items}
    for citation in required or []:
        check()
        if citation.evidence_id not in by_id:
            raise RetrievalError("Accepted brief support is outside the selected snapshot")
        item=by_id[citation.evidence_id]
        if not 0<=citation.start<citation.end<=len(item.content) or item.content[citation.start:citation.end]!=citation.quote:
            raise RetrievalError("Accepted brief support no longer matches the selected saved evidence")
        containing = next((p for p in chunks if p["id"]==citation.evidence_id and p["start"]<=citation.start<citation.end<=p["end"]), None)
        passage = {**containing,"reason":"accepted_brief"} if containing else required_passage(by_id[citation.evidence_id],citation)
        if not any(p["chunk_id"]==passage["chunk_id"] for p in required_chunks):
            required_chunks.append(passage)
    required_size = sum(len(json.dumps(p,ensure_ascii=False))+2 for p in required_chunks)
    if len(required_chunks)>MAX_PASSAGES or required_size>budget:
        raise RetrievalBudget("Accepted brief support exceeds the available context budget. Reduce retained claims or shorten the task before drafting a handoff.")
    known_chunks = {p["chunk_id"] for p in chunks}
    chunks.extend(p for p in required_chunks if p["chunk_id"] not in known_chunks)
    complete_size = sum(len(json.dumps(passage, ensure_ascii=False)) + 2 for passage in chunks)
    if not search and len(chunks) <= MAX_PASSAGES and complete_size <= budget:
        required_ids={p["chunk_id"] for p in required_chunks}
        selected = required_chunks + [p for p in chunks if p["chunk_id"] not in required_ids]
        return selected, receipt_for(snapshot, chunks, selected, terms, truncated, None, items)
    ranked = []
    if terms and chunks:
        try:
            with sqlite3.connect(":memory:") as db:
                db.execute("CREATE VIRTUAL TABLE passages USING fts5(title, content, tokenize='porter unicode61')")
                for offset in range(0, len(chunks), 32):
                    check()
                    db.executemany("INSERT INTO passages(title,content) VALUES (?,?)",
                                   [(chunk["title"], chunk["content"]) for chunk in chunks[offset:offset + 32]])
                expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                ranked = db.execute("SELECT rowid,bm25(passages,2.0,1.0) FROM passages WHERE passages MATCH ? "
                                    "ORDER BY 2, rowid", (expression,)).fetchall()
        except sqlite3.Error as cause:
            raise RetrievalError("Local passage search failed. Saved evidence is unchanged; restart the engine and retry.") from cause
    selected = [*required_chunks]
    count = Counter(p["document_id"] for p in selected)
    used = required_size
    for row, score in ranked:
        check()
        passage = {**chunks[row - 1], "score": -score}
        size = len(json.dumps(passage, ensure_ascii=False)) + 2
        if len(selected)>=MAX_PASSAGES:
            break
        if count[passage["document_id"]] >= 4 or used + size > budget:
            continue
        if any(p["id"] == passage["id"] and max(0, min(p["end"], passage["end"]) - max(p["start"], passage["start"]))
               > min(p["end"] - p["start"], passage["end"] - passage["start"]) // 2 for p in selected):
            continue
        selected.append(passage)
        count[passage["document_id"]] += 1
        used += size
        if len(selected) >= MAX_PASSAGES:
            break
    return selected, receipt_for(snapshot, chunks, selected, terms, truncated, len(ranked), items)
