"""Reviewed Markdown handoffs from one immutable task/evidence revision."""
from __future__ import annotations

import hashlib
import html
import json
import re

from compass.engine.passage_retrieval import coverage_for, line_range, passages_for
from compass.engine.task_repository import TaskConflict
from compass.models.tasks import now


def text(value: str) -> str:
    # Treat saved/model/user text as text, never executable HTML or Markdown links.
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def block(value: str, language: str = "text") -> str:
    fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", value)), default=3))
    return f"{fence}{language}\n{value}\n{fence}"


def export_task(service, task_id: str, expected_revision: int, kind: str):
    task = service.tasks.read(task_id)
    if task.revision != expected_revision:
        raise TaskConflict("Task changed. Reload before exporting its saved review.")
    artifact = service._artifact(task, kind)
    if (artifact.state != "accepted" or not artifact.decision or "unreviewed" in artifact.reviews
            or "accepted" not in artifact.reviews):
        raise ValueError("Accept the reviewed artifact and choose a direction before exporting")
    if kind == "handoff" and (task.brief is None or task.brief.state != "accepted"):
        raise ValueError("The handoff requires its accepted decision brief before export")
    service.validate_body(task, artifact.body)
    items = service.selected_evidence(task)
    by_id = {item.id:item for item in items}
    stale = service.stale(task)
    exported_at = now()
    citations, references = [], {}
    for claim, review in zip(artifact.body.claims, artifact.reviews, strict=True):
        if review != "accepted":
            continue
        for citation in claim.citations:
            key = (citation.evidence_id, citation.start, citation.end)
            if key in references:
                continue
            number = len(citations) + 1
            references[key] = number
            item = by_id[citation.evidence_id]
            locator = next(passages_for(item))
            first, last = line_range(item.content, citation.start, citation.end)
            citations.append({"reference":f"E{number}","evidence_id":item.id,
                              "source_id":item.source_id or item.source_name,
                              "document_id":locator["document_id"],"content_version":locator["version"],
                              "start":citation.start,"end":citation.end,"line_start":first,"line_end":last,
                              "quote":citation.quote})
    sources = []
    for item in items:
        locator = next(passages_for(item), None)
        # Explicit allowlist: no config, URLs, file paths, credentials or arbitrary metadata.
        sources.append({"evidence_id":item.id,"source_id":item.source_id or item.source_name,
                        "source_name":item.source_name,"source_type":item.source_type.value,
                        "title":item.title,"document_id":locator["document_id"] if locator else item.document_id,
                        "content_version":locator["version"] if locator else hashlib.sha256(item.content.encode()).hexdigest(),
                        "characters":len(item.content),"import_coverage":coverage_for(item)})
    manifest = {"format":"compass-reviewed-handoff-v1","exported_at":exported_at,
                "task_id":task.id,"task_revision":task.revision,"artifact_kind":kind,
                "state":"accepted","edited_by_user":artifact.edited_by_user,
                "accepted_brief_direction":task.brief.decision if kind == "handoff" else None,
                "source_freshness_at_export":"needs_review" if stale else "unchanged",
                "evidence_snapshot":task.evidence_snapshot,"workflow_version":artifact.workflow_version,
                "actual_model":artifact.model,"provider":artifact.provider,
                "input_tokens":artifact.input_tokens,"output_tokens":artifact.output_tokens,
                "selected_connection":artifact.connection.model_dump() if artifact.connection else None,
                "retrieval":artifact.retrieval.model_dump() if artifact.retrieval else None,
                "omitted_rejected_claims":artifact.reviews.count("rejected"),
                "sources":sources,"citations":citations}
    lines = [f"# {text(artifact.body.title)}", "", "Accepted by the author after claim review.",
             f"Exported task revision {task.revision} at {exported_at}.", "",
             "**Sources changed: this artifact needs review against current evidence. The passages below are the saved versions reviewed by the author.**" if stale else
             "Selected saved source versions were unchanged at export. This does not establish source accuracy or completeness.", "",
             "## Task", "", text(task.question), "", f"Reader: {text(task.reader)}", "",
             f"Completion condition: {text(task.completion_condition)}", "", "## Summary", "", text(artifact.body.summary),
             "", "## Reviewed claims", ""]
    for number, (claim, review) in enumerate(zip(artifact.body.claims, artifact.reviews, strict=True), 1):
        if review != "accepted":
            continue
        refs = ", ".join(f"E{references[(c.evidence_id,c.start,c.end)]}" for c in claim.citations)
        lines.extend([f"### Claim {number} · {claim.kind}", "", text(claim.text), "",
                      f"Supporting passage references: {refs}." if refs else "No source passage attached; this is a proposal or unknown.", ""])
    if manifest["omitted_rejected_claims"]:
        lines.extend([f"{manifest['omitted_rejected_claims']} rejected claims were omitted from this handoff.", ""])
    if kind == "handoff":
        lines.extend(["## Accepted brief direction", "", text(task.brief.decision), ""])
    lines.extend(["## Chosen direction", "", text(artifact.decision), "", "## Proposed action", "", text(artifact.body.proposed_action),
                  "", "## Alternatives", "", *[f"- {text(value)}" for value in artifact.body.alternatives], "",
                  "## Unknowns", "", *([f"- {text(value)}" for value in artifact.body.unknowns] or ["None recorded; this is not a completeness claim."]), "",
                  "## Evidence passages", "", "Offsets are zero-based Unicode code points in the saved document; end is exclusive. Exact quotes are fenced as text.", ""])
    for citation in citations:
        item = by_id[citation["evidence_id"]]
        lines.extend([f"### {citation['reference']} · {text(item.title)}", "", f"Source: {text(item.source_name)} ({item.source_type.value}).",
                      f"Saved characters {citation['start']}–{citation['end']}; lines {citation['line_start']}–{citation['line_end']}.",
                      "", block(citation["quote"]), ""])
    lines.extend(["## AI context and provenance", "", f"Actual model: {text(artifact.model)}. Provider/route: {text(artifact.provider)}.",
                  "This artifact includes saved human corrections." if artifact.edited_by_user else "No human text corrections were recorded for this artifact.",
                  "Human edits and claim review do not establish that an observation is true or that a proposal is implemented."])
    if artifact.retrieval:
        receipt = artifact.retrieval
        lines.extend([f"The original AI received {receipt.provided_characters} of {receipt.total_characters} saved characters from {receipt.selected_documents} selected documents.",
                      f"Import omissions reported for {receipt.import_omission_documents} documents; coverage unknown for {receipt.unknown_import_documents}."])
    else:
        lines.append("Original AI retrieval coverage was not recorded for this artifact.")
    lines.extend(["", "## Source manifest", "", "This manifest describes the saved selected evidence and included citations. Application credential records, workspace paths, source configuration and arbitrary metadata are excluded. User-authored text and cited passages may contain sensitive information.", "",
                  block(json.dumps(manifest, ensure_ascii=False, indent=2), "json"), ""])
    markdown = "\n".join(lines)
    if len(markdown.encode()) > 5_000_000:
        raise ValueError("This handoff exceeds the 5 MB Markdown export limit. Saved work was kept.")
    if service.tasks.read(task_id).revision != expected_revision:
        raise TaskConflict("Task changed while preparing the export. Reload and export its saved review again.")
    return {"filename":f"compass-{kind}-{task.id}.md","markdown":markdown,"manifest":manifest}
