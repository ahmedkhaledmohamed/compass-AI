"""Linear connector — part of the JUDGMENT source of truth.

Ingests: Linear issues from GraphQL API or JSON export files.
Answers: "What is the team PLANNING and TRACKING?"

Dual-mode:
  - Live API: Linear GraphQL API (issues, projects, cycles)
  - File import: Linear JSON export files (fallback)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from compass.connectors.live_base import LiveConnector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

MAX_ISSUES = 500

LINEAR_API = "https://api.linear.app/graphql"

ISSUES_QUERY = """
query($first: Int!, $after: String) {
  issues(first: $first, after: $after, orderBy: updatedAt) {
    pageInfo { hasNextPage endCursor }
    nodes {
      identifier
      title
      description
      state { name }
      priorityLabel
      labels { nodes { name } }
      assignee { name }
      creator { name }
      createdAt
      updatedAt
      comments(first: 5) { nodes { body user { name } } }
    }
  }
}
"""

PROJECTS_QUERY = """
query {
  projects(first: 20, orderBy: updatedAt) {
    nodes {
      name
      description
      state
      progress
      startDate
      targetDate
      lead { name }
    }
  }
}
"""

CYCLES_QUERY = """
query {
  cycles(first: 5, orderBy: endsAt) {
    nodes {
      number
      name
      startsAt
      endsAt
      progress
      completedScopeCount
      scopeCount
    }
  }
}
"""


class LinearConnector(LiveConnector):
    """Ingests Linear issues from API or JSON export files."""

    connector_type = "linear"
    provider_id = "linear"
    rate_limit_rpm = 60

    def validate(self) -> bool:
        path = self.config.path
        if path and Path(path).expanduser().exists():
            return True
        return bool(self.has_credentials())

    # ------------------------------------------------------------------
    # Live API ingestion (GraphQL)
    # ------------------------------------------------------------------

    def ingest_live(self) -> list[Evidence]:
        """Fetch issues, projects, cycles from Linear GraphQL API."""
        evidence: list[Evidence] = []

        # Fetch issues (paginated)
        all_issues: list[dict] = []
        cursor = None
        while len(all_issues) < MAX_ISSUES:
            try:
                variables: dict = {"first": 50}
                if cursor:
                    variables["after"] = cursor
                res = self._api_post(
                    LINEAR_API,
                    json={"query": ISSUES_QUERY, "variables": variables},
                )
                data = res.json()
                issues_data = data.get("data", {}).get("issues", {})
                nodes = issues_data.get("nodes", [])
                if not nodes:
                    break
                all_issues.extend(nodes)
                page_info = issues_data.get("pageInfo", {})
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")
            except Exception as e:
                logger.warning("Failed to fetch Linear issues: %s", e, exc_info=True)
                break

        if all_issues:
            normalized = [self._normalize(issue) for issue in all_issues]
            evidence.extend(self._issues_to_evidence(normalized, "Linear API"))

        # Fetch projects
        try:
            res = self._api_post(LINEAR_API, json={"query": PROJECTS_QUERY})
            data = res.json()
            projects = data.get("data", {}).get("projects", {}).get("nodes", [])
            if projects:
                lines = []
                for p in projects:
                    lead = p.get("lead", {})
                    lead_name = lead.get("name", "") if isinstance(lead, dict) else ""
                    progress = p.get("progress", 0)
                    lines.append(
                        f"- **{p.get('name', '')}** ({p.get('state', '')}, "
                        f"{progress:.0%}) — {p.get('description', '')[:200]}"
                        + (f" [Lead: {lead_name}]" if lead_name else "")
                    )
                evidence.append(Evidence(
                    source_type=SourceType.JUDGMENT,
                    connector="linear",
                    title=f"Linear projects ({len(projects)} active)",
                    content="Active Linear projects:\n\n" + "\n".join(lines),
                    metadata={"type": "linear_projects", "source": "api"},
                ))
        except Exception as e:
            logger.debug("Failed to fetch Linear projects: %s", e, exc_info=True)

        # Fetch cycles
        try:
            res = self._api_post(LINEAR_API, json={"query": CYCLES_QUERY})
            data = res.json()
            cycles = data.get("data", {}).get("cycles", {}).get("nodes", [])
            if cycles:
                lines = []
                for c in cycles:
                    scope = c.get("scopeCount", 0)
                    completed = c.get("completedScopeCount", 0)
                    name = c.get("name") or f"Cycle {c.get('number', '?')}"
                    lines.append(
                        f"- **{name}**: {completed}/{scope} complete "
                        f"({c.get('startsAt', '')[:10]} → {c.get('endsAt', '')[:10]})"
                    )
                evidence.append(Evidence(
                    source_type=SourceType.JUDGMENT,
                    connector="linear",
                    title=f"Linear cycles ({len(cycles)} recent)",
                    content="Recent Linear cycles:\n\n" + "\n".join(lines),
                    metadata={"type": "linear_cycles", "source": "api"},
                ))
        except Exception as e:
            logger.debug("Failed to fetch Linear cycles: %s", e, exc_info=True)

        logger.info("Linear live: fetched %d evidence items", len(evidence))
        return evidence

    # ------------------------------------------------------------------
    # File-based ingestion (original behavior)
    # ------------------------------------------------------------------

    def ingest_file(self) -> list[Evidence]:
        path = self.config.path
        if not path:
            return []

        p = Path(path).expanduser().resolve()
        evidence: list[Evidence] = []

        if p.is_file() and p.suffix.lower() == ".json":
            evidence.extend(self._ingest_json(p))
        elif p.is_dir():
            for fpath in sorted(p.rglob("*.json")):
                evidence.extend(self._ingest_json(fpath))

        return evidence

    def _ingest_json(self, fpath: Path) -> list[Evidence]:
        try:
            data = json.loads(fpath.read_text(errors="ignore"))
        except (json.JSONDecodeError, OSError):
            return []

        issues = self._extract_issues(data)
        if not issues:
            return []

        return self._issues_to_evidence(issues, fpath.stem, {"file": str(fpath)})

    # ------------------------------------------------------------------
    # Shared evidence building
    # ------------------------------------------------------------------

    def _issues_to_evidence(
        self,
        issues: list[dict],
        source_label: str,
        extra_meta: dict | None = None,
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        meta = extra_meta or {}

        # Summary
        summary_lines = []
        for issue in issues[:MAX_ISSUES]:
            identifier = issue.get("identifier", "")
            title = issue.get("title", "")
            state = issue.get("state", "")
            priority_label = issue.get("priority_label", "")
            prefix = f"[{identifier}]" if identifier else ""
            state_str = f" ({state})" if state else ""
            prio_str = f" [{priority_label}]" if priority_label else ""
            summary_lines.append(f"- {prefix}{prio_str} {title}{state_str}")

        evidence.append(Evidence(
            source_type=SourceType.JUDGMENT,
            connector="linear",
            title=f"Linear issues: {source_label} ({len(issues)} issues)",
            content=(
                f"Linear issues from {source_label}: {len(issues)} total\n\n"
                + "\n".join(summary_lines)
            ),
            metadata={"type": "linear_export", "issue_count": len(issues), **meta},
        ))

        # Per-issue
        for issue in issues[:MAX_ISSUES]:
            description = issue.get("description", "")
            if not description or len(description) < 50:
                continue

            identifier = issue.get("identifier", "")
            title = issue.get("title", "")
            state = issue.get("state", "")
            priority_label = issue.get("priority_label", "")
            labels = issue.get("labels", [])
            comments = issue.get("comments", [])

            content_parts = [
                f"**{identifier}: {title}**" if identifier else f"**{title}**",
                f"State: {state}" if state else "",
                f"Priority: {priority_label}" if priority_label else "",
                f"Labels: {', '.join(labels)}" if labels else "",
                "",
                description[:5000],
            ]

            if comments:
                content_parts.append("\n**Comments:**")
                for comment in comments[:10]:
                    user = comment.get("user", "unknown")
                    body = comment.get("body", "")[:500]
                    content_parts.append(f"- {user}: {body}")

            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="linear",
                title=f"Linear: {identifier} — {title}" if identifier else f"Linear: {title}",
                content="\n".join(p for p in content_parts if p is not None),
                metadata={
                    "type": "linear_issue",
                    "identifier": identifier,
                    "state": state,
                    "priority": priority_label,
                    **meta,
                },
            ))

        # Distribution
        state_counts: dict[str, int] = {}
        for issue in issues:
            s = issue.get("state", "unknown")
            state_counts[s] = state_counts.get(s, 0) + 1

        dist_lines = ["**State distribution:**"]
        for s, c in sorted(state_counts.items(), key=lambda x: -x[1]):
            dist_lines.append(f"- {s}: {c}")

        evidence.append(Evidence(
            source_type=SourceType.JUDGMENT,
            connector="linear",
            title=f"Linear distribution: {len(issues)} issues across {len(state_counts)} states",
            content="\n".join(dist_lines),
            metadata={"type": "linear_distribution", **meta},
        ))

        return evidence

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_issues(self, data: object) -> list[dict]:
        """Extract issue dicts from various Linear export formats."""
        if isinstance(data, list):
            return [self._normalize(item) for item in data if isinstance(item, dict)]

        if isinstance(data, dict):
            if "issues" in data:
                issues_data = data["issues"]
                if isinstance(issues_data, list):
                    return [self._normalize(item) for item in issues_data]
                if isinstance(issues_data, dict) and "nodes" in issues_data:
                    return [self._normalize(item) for item in issues_data["nodes"]]

            if "data" in data and isinstance(data["data"], dict):
                return self._extract_issues(data["data"])

            if "title" in data:
                return [self._normalize(data)]

        return []

    def _normalize(self, raw: dict) -> dict:
        """Normalize a Linear issue dict."""
        state = raw.get("state", {})
        state_name = state.get("name", "") if isinstance(state, dict) else str(state)

        labels_raw = raw.get("labels", {})
        labels = []
        if isinstance(labels_raw, dict) and "nodes" in labels_raw:
            labels = [lb.get("name", "") for lb in labels_raw["nodes"] if isinstance(lb, dict)]
        elif isinstance(labels_raw, list):
            labels = [lb.get("name", lb) if isinstance(lb, dict) else str(lb) for lb in labels_raw]

        comments_raw = raw.get("comments", {})
        comments = []
        if isinstance(comments_raw, dict) and "nodes" in comments_raw:
            for c in comments_raw["nodes"]:
                if isinstance(c, dict):
                    user_data = c.get("user", {})
                    comments.append({
                        "user": user_data.get("name", "unknown") if isinstance(user_data, dict) else "unknown",
                        "body": c.get("body", ""),
                    })
        elif isinstance(comments_raw, list):
            for c in comments_raw:
                if isinstance(c, dict):
                    comments.append({
                        "user": c.get("user", "unknown"),
                        "body": c.get("body", ""),
                    })

        return {
            "identifier": raw.get("identifier", ""),
            "title": raw.get("title", ""),
            "description": raw.get("description", ""),
            "state": state_name,
            "priority_label": raw.get("priorityLabel", raw.get("priority_label", "")),
            "labels": labels,
            "comments": comments,
        }
