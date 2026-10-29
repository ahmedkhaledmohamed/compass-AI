"""Activity tracking — workspace event log.

Records events like evidence ingestion, document creation, discoveries,
and presentation generation as a simple in-memory log.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ActivityEvent(BaseModel):
    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex[:10])
    event_type: str  # ingest, discover, reconcile, document, presentation, prototype, chat
    title: str
    description: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict = Field(default_factory=dict)


# In-memory store per workspace
_activity: dict[str, list[ActivityEvent]] = {}  # workspace_path -> events

MAX_EVENTS = 200


def record(workspace_path: str, event_type: str, title: str, description: str = "", metadata: dict | None = None) -> ActivityEvent:
    """Record an activity event."""
    event = ActivityEvent(
        event_type=event_type,
        title=title,
        description=description,
        metadata=metadata or {},
    )
    if workspace_path not in _activity:
        _activity[workspace_path] = []

    _activity[workspace_path].insert(0, event)

    # Trim to max
    if len(_activity[workspace_path]) > MAX_EVENTS:
        _activity[workspace_path] = _activity[workspace_path][:MAX_EVENTS]

    return event


def get_events(workspace_path: str, limit: int = 50, event_type: str | None = None) -> list[ActivityEvent]:
    """Get recent activity events."""
    events = _activity.get(workspace_path, [])
    if event_type:
        events = [e for e in events if e.event_type == event_type]
    return events[:limit]


def clear(workspace_path: str) -> None:
    """Clear activity for a workspace."""
    _activity.pop(workspace_path, None)
