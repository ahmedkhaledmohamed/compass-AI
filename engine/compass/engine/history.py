"""Discovery history — tracks how opportunities and conflicts evolve over time.

Appends each discovery/reconciliation run to a JSON file so PMs can see:
- "This conflict was first detected 3 months ago"
- How priorities shift over time
- Which opportunities keep appearing vs. which are one-off signals
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from compass.models.conflicts import ConflictReport
from compass.models.specs import Opportunity

HISTORY_FILE = "discovery_history.json"
FEEDBACK_FILE = "feedback.json"
QUALITY_LOG_FILE = "quality_log.json"


def _load_history(compass_dir: Path) -> list[dict]:
    """Load history entries from disk."""
    history_path = compass_dir / HISTORY_FILE
    if not history_path.exists():
        return []
    try:
        return json.loads(history_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_history(compass_dir: Path, entries: list[dict]) -> None:
    """Save history entries to disk."""
    history_path = compass_dir / HISTORY_FILE
    history_path.write_text(json.dumps(entries, indent=2, default=str))


def record_discovery(
    compass_dir: Path,
    opportunities: list[Opportunity],
    conflict_report: ConflictReport | None = None,
    prompt_version: str = "v1",
) -> dict:
    """Record a discovery run in history. Returns the new entry."""
    entries = _load_history(compass_dir)

    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "discovery",
        "prompt_version": prompt_version,
        "opportunity_count": len(opportunities),
        "conflict_count": len(conflict_report.conflicts) if conflict_report else 0,
        "opportunities": [
            {
                "title": opp.title,
                "rank": opp.rank,
                "confidence": opp.confidence.value,
                "description": opp.description[:200],
            }
            for opp in opportunities
        ],
        "conflicts": [
            {
                "title": c.title,
                "severity": c.severity.value,
                "conflict_type": c.conflict_type.description if hasattr(c.conflict_type, 'description') else str(c.conflict_type),
            }
            for c in (conflict_report.conflicts if conflict_report else [])
        ],
    }

    entries.append(entry)
    _save_history(compass_dir, entries)
    return entry


def get_history(compass_dir: Path) -> list[dict]:
    """Get all history entries."""
    return _load_history(compass_dir)


def get_opportunity_timeline(compass_dir: Path, title: str) -> list[dict]:
    """Get the timeline for a specific opportunity across discovery runs."""
    entries = _load_history(compass_dir)
    timeline = []

    for entry in entries:
        if entry.get("type") != "discovery":
            continue
        for opp in entry.get("opportunities", []):
            if opp.get("title", "").lower() == title.lower():
                timeline.append({
                    "timestamp": entry["timestamp"],
                    "rank": opp["rank"],
                    "confidence": opp["confidence"],
                })
                break

    return timeline


def get_conflict_first_seen(compass_dir: Path, title: str) -> str | None:
    """Get when a conflict was first detected."""
    entries = _load_history(compass_dir)

    for entry in entries:
        for conflict in entry.get("conflicts", []):
            if conflict.get("title", "").lower() == title.lower():
                return entry.get("timestamp")

    return None


def get_history_summary(compass_dir: Path) -> dict:
    """Get a summary of discovery history."""
    entries = _load_history(compass_dir)

    if not entries:
        return {"total_runs": 0}

    discovery_runs = [e for e in entries if e.get("type") == "discovery"]

    # Track opportunity frequency
    opp_counts: dict[str, int] = {}
    for entry in discovery_runs:
        for opp in entry.get("opportunities", []):
            title = opp.get("title", "")
            opp_counts[title] = opp_counts.get(title, 0) + 1

    # Track conflict persistence
    conflict_counts: dict[str, int] = {}
    for entry in discovery_runs:
        for conflict in entry.get("conflicts", []):
            title = conflict.get("title", "")
            conflict_counts[title] = conflict_counts.get(title, 0) + 1

    recurring_opps = {k: v for k, v in opp_counts.items() if v >= 2}
    persistent_conflicts = {k: v for k, v in conflict_counts.items() if v >= 2}

    return {
        "total_runs": len(discovery_runs),
        "first_run": discovery_runs[0].get("timestamp") if discovery_runs else None,
        "last_run": discovery_runs[-1].get("timestamp") if discovery_runs else None,
        "recurring_opportunities": recurring_opps,
        "persistent_conflicts": persistent_conflicts,
        "total_unique_opportunities": len(opp_counts),
        "total_unique_conflicts": len(conflict_counts),
    }


def compare_with_previous(compass_dir: Path, current_opportunities: list[dict]) -> list[dict]:
    """Compare current opportunities with previous runs to tag each as NEW, PERSISTENT, or RESOLVED.

    Args:
        current_opportunities: List of dicts with at least a "title" key.

    Returns:
        List of dicts with added "status" and "persistence_count" fields.
        Also returns a list of resolved opportunities (appeared before, gone now).
    """
    entries = _load_history(compass_dir)
    discovery_runs = [e for e in entries if e.get("type") == "discovery"]

    # Count how many times each opportunity appeared historically
    opp_history: dict[str, int] = {}
    for entry in discovery_runs:
        for opp in entry.get("opportunities", []):
            title = opp.get("title", "")
            opp_history[title.lower()] = opp_history.get(title.lower(), 0) + 1

    # Tag current opportunities
    tagged = []
    for opp in current_opportunities:
        title_lower = opp.get("title", "").lower()
        count = opp_history.get(title_lower, 0)
        if count == 0:
            status = "NEW"
        elif count >= 3:
            status = f"PERSISTENT x{count}"
        else:
            status = f"SEEN x{count}"
        tagged.append({**opp, "status": status, "persistence_count": count})

    return tagged


def get_resolved_opportunities(compass_dir: Path, current_titles: list[str]) -> list[dict]:
    """Find opportunities that appeared in previous runs but not in the current one."""
    entries = _load_history(compass_dir)
    discovery_runs = [e for e in entries if e.get("type") == "discovery"]

    if not discovery_runs:
        return []

    # Get all previously seen opportunities
    all_previous: dict[str, dict] = {}
    for entry in discovery_runs:
        for opp in entry.get("opportunities", []):
            title = opp.get("title", "")
            all_previous[title.lower()] = {
                "title": title,
                "last_seen": entry.get("timestamp", ""),
                "confidence": opp.get("confidence", ""),
            }

    current_lower = {t.lower() for t in current_titles}
    resolved = []
    for title_lower, info in all_previous.items():
        if title_lower not in current_lower:
            resolved.append({**info, "status": "RESOLVED"})

    return resolved


# ---------------------------------------------------------------------------
# Feedback / Quality tracking
# ---------------------------------------------------------------------------

def _load_feedback(compass_dir: Path) -> list[dict]:
    """Load all feedback entries."""
    path = compass_dir / FEEDBACK_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_feedback(compass_dir: Path, entries: list[dict]) -> None:
    path = compass_dir / FEEDBACK_FILE
    path.write_text(json.dumps(entries, indent=2, default=str))


def record_feedback(
    compass_dir: Path,
    opportunity_title: str,
    rating: str,
) -> dict:
    """Record feedback on an opportunity.

    Args:
        opportunity_title: Title of the opportunity
        rating: "known" (already knew), "surprise" (new insight), "wrong" (incorrect)
    """
    entries = _load_feedback(compass_dir)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "opportunity_title": opportunity_title,
        "rating": rating,
    }
    entries.append(entry)
    _save_feedback(compass_dir, entries)

    # Also update quality log
    _update_quality_log(compass_dir)

    return entry


def _update_quality_log(compass_dir: Path) -> None:
    """Recalculate quality stats from all feedback."""
    feedback = _load_feedback(compass_dir)
    if not feedback:
        return

    total = len(feedback)
    surprises = sum(1 for f in feedback if f.get("rating") == "surprise")
    known = sum(1 for f in feedback if f.get("rating") == "known")
    wrong = sum(1 for f in feedback if f.get("rating") == "wrong")

    quality_data = {
        "last_updated": datetime.now().isoformat(),
        "total_ratings": total,
        "surprises": surprises,
        "known": known,
        "wrong": wrong,
        "surprise_rate": round(surprises / total * 100, 1) if total else 0,
        "accuracy_rate": round((total - wrong) / total * 100, 1) if total else 0,
    }

    path = compass_dir / QUALITY_LOG_FILE
    path.write_text(json.dumps(quality_data, indent=2))


def get_quality_stats(compass_dir: Path) -> dict:
    """Get aggregate quality stats."""
    path = compass_dir / QUALITY_LOG_FILE
    if not path.exists():
        # Calculate from feedback directly
        feedback = _load_feedback(compass_dir)
        if not feedback:
            return {"total_ratings": 0, "message": "No feedback recorded yet."}
        _update_quality_log(compass_dir)

    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"total_ratings": 0, "message": "Error reading quality log."}
