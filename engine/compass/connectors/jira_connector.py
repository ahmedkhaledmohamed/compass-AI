"""Jira connector — part of the JUDGMENT source of truth.

Ingests: Jira issues from JSON export files or Jira Cloud REST API.
Answers: "What is the team PLANNING and TRACKING?"

Dual-mode:
  - Live API: Fetches via Jira Cloud REST API v3 when credentials are available
  - File import: Reads from JSON export files (fallback)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from compass.connectors.live_base import LiveConnector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

MAX_ISSUES = 500


class JiraConnector(LiveConnector):
    """Ingests Jira issues from API or JSON export files."""

    connector_type = "jira"
    provider_id = "atlassian"
    rate_limit_rpm = 60  # Jira Cloud allows ~100/min for standard tier

    def validate(self) -> bool:
        path = self.config.path
        if path and Path(path).expanduser().exists():
            return True
        return bool(self.has_credentials())

    # ------------------------------------------------------------------
    # Live API ingestion
    # ------------------------------------------------------------------

    def ingest_live(self) -> list[Evidence]:
        """Fetch issues from Jira Cloud REST API v3."""
        evidence: list[Evidence] = []

        site = self._get_site()
        project_key = self._get_project_key()
        if not site:
            logger.warning("No Jira site configured, falling back to file import")
            return self.ingest_file()

        base_url = f"https://{site}.atlassian.net/rest/api/3"

        # Build JQL query
        jql_parts = []
        if project_key:
            jql_parts.append(f"project = {project_key}")
        jql_parts.append("updated >= -30d")
        jql = " AND ".join(jql_parts)

        # Fetch issues
        all_issues: list[dict] = []
        start_at = 0
        max_results = 50

        while len(all_issues) < MAX_ISSUES:
            try:
                res = self._api_get(
                    f"{base_url}/search",
                    params={
                        "jql": jql,
                        "startAt": str(start_at),
                        "maxResults": str(max_results),
                        "fields": "summary,status,priority,description,comment,assignee,reporter,created,updated,labels,issuetype",
                    },
                )
                data = res.json()
                issues = data.get("issues", [])
                if not issues:
                    break
                all_issues.extend(issues)
                total = data.get("total", 0)
                start_at += len(issues)
                if start_at >= total:
                    break
            except Exception as e:
                logger.warning("Failed to fetch Jira issues: %s", e, exc_info=True)
                break

        if not all_issues:
            logger.info("No Jira issues found for %s", jql)
            return evidence

        # Convert to normalized format and create evidence
        normalized = [self._normalize_issue(issue) for issue in all_issues]

        # Summary evidence
        summary_lines = []
        for issue in normalized[:MAX_ISSUES]:
            key = issue.get("key", "")
            summary = issue.get("summary", "")
            status = issue.get("status", "")
            priority = issue.get("priority", "")
            prefix = f"[{key}]" if key else ""
            status_str = f" ({status})" if status else ""
            priority_str = f" [{priority}]" if priority else ""
            summary_lines.append(f"- {prefix}{priority_str} {summary}{status_str}")

        scope = f"project {project_key}" if project_key else site
        evidence.append(Evidence(
            source_type=SourceType.JUDGMENT,
            connector="jira",
            title=f"Jira issues: {scope} ({len(normalized)} issues, last 30 days)",
            content=(
                f"Jira issues from {scope}: {len(normalized)} total (last 30 days)\n\n"
                + "\n".join(summary_lines)
            ),
            metadata={
                "type": "jira_export",
                "issue_count": len(normalized),
                "source": "api",
                "site": site,
            },
        ))

        # Per-issue evidence for issues with substantial descriptions
        for issue in normalized[:MAX_ISSUES]:
            description = issue.get("description", "")
            if not description or len(description) < 50:
                continue

            key = issue.get("key", "")
            summary = issue.get("summary", "")
            status = issue.get("status", "")
            priority = issue.get("priority", "")
            comments = issue.get("comments", [])

            content_parts = [
                f"**{key}: {summary}**",
                f"Status: {status}" if status else "",
                f"Priority: {priority}" if priority else "",
                "",
                description[:5000],
            ]

            if comments:
                content_parts.append("\n**Comments:**")
                for comment in comments[:10]:
                    author = comment.get("author", "unknown")
                    body = comment.get("body", "")[:500]
                    content_parts.append(f"- {author}: {body}")

            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="jira",
                title=f"Jira: {key} — {summary}" if key else f"Jira: {summary}",
                content="\n".join(content_parts),
                metadata={
                    "type": "jira_issue",
                    "key": key,
                    "status": status,
                    "priority": priority,
                    "source": "api",
                },
            ))

        # Status distribution
        status_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {}
        for issue in normalized:
            s = issue.get("status", "unknown")
            p = issue.get("priority", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1
            priority_counts[p] = priority_counts.get(p, 0) + 1

        dist_lines = ["**Status distribution:**"]
        for s, c in sorted(status_counts.items(), key=lambda x: -x[1]):
            dist_lines.append(f"- {s}: {c}")
        dist_lines.append("\n**Priority distribution:**")
        for p, c in sorted(priority_counts.items(), key=lambda x: -x[1]):
            dist_lines.append(f"- {p}: {c}")

        evidence.append(Evidence(
            source_type=SourceType.JUDGMENT,
            connector="jira",
            title=f"Jira distribution: {len(normalized)} issues across {len(status_counts)} statuses",
            content="\n".join(dist_lines),
            metadata={"type": "jira_distribution", "source": "api"},
        ))

        logger.info("Jira live: fetched %d evidence items from %s", len(evidence), scope)
        return evidence

    def _get_site(self) -> str | None:
        """Get the Jira site subdomain from config."""
        site = self.config.options.get("site")
        if site:
            return site
        # Try to parse from URL (e.g. https://myteam.atlassian.net)
        url = self.config.url
        if url and "atlassian.net" in url:
            parts = url.split("//")[-1].split(".")
            if parts:
                return parts[0]
        return None

    def _get_project_key(self) -> str | None:
        """Get the Jira project key from config."""
        return self.config.options.get("project") or self.config.options.get("project_key")

    # ------------------------------------------------------------------
    # ADF (Atlassian Document Format) to plain text
    # ------------------------------------------------------------------

    def _adf_to_text(self, node: dict | str | None) -> str:
        """Convert Atlassian Document Format JSON to plain text."""
        if not node:
            return ""
        if isinstance(node, str):
            return node

        text_parts: list[str] = []
        node_type = node.get("type", "")

        if node_type == "text":
            text_parts.append(node.get("text", ""))
        elif node_type in ("paragraph", "heading"):
            children_text = " ".join(
                self._adf_to_text(c) for c in node.get("content", [])
            )
            text_parts.append(children_text + "\n")
        elif node_type in ("bulletList", "orderedList"):
            for item in node.get("content", []):
                item_text = self._adf_to_text(item).strip()
                text_parts.append(f"- {item_text}")
        elif node_type == "listItem":
            text_parts.append(
                " ".join(self._adf_to_text(c) for c in node.get("content", []))
            )
        elif node_type == "codeBlock":
            code = " ".join(
                self._adf_to_text(c) for c in node.get("content", [])
            )
            text_parts.append(f"```\n{code}\n```")
        else:
            # Generic: recurse into content
            for child in node.get("content", []):
                text_parts.append(self._adf_to_text(child))

        return "\n".join(text_parts)

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

        evidence: list[Evidence] = []

        # Summary evidence item
        summary_lines = []
        for issue in issues[:MAX_ISSUES]:
            key = issue.get("key", "")
            summary = issue.get("summary", "")
            status = issue.get("status", "")
            priority = issue.get("priority", "")
            prefix = f"[{key}]" if key else ""
            status_str = f" ({status})" if status else ""
            priority_str = f" [{priority}]" if priority else ""
            summary_lines.append(f"- {prefix}{priority_str} {summary}{status_str}")

        evidence.append(Evidence(
            source_type=SourceType.JUDGMENT,
            connector="jira",
            title=f"Jira issues: {fpath.stem} ({len(issues)} issues)",
            content=(
                f"Jira issues from {fpath.name}: {len(issues)} total\n\n"
                + "\n".join(summary_lines)
            ),
            metadata={
                "file": str(fpath),
                "type": "jira_export",
                "issue_count": len(issues),
            },
        ))

        # Per-issue evidence for issues with substantial descriptions
        for issue in issues[:MAX_ISSUES]:
            description = issue.get("description", "")
            if not description or len(description) < 50:
                continue

            key = issue.get("key", "")
            summary = issue.get("summary", "")
            status = issue.get("status", "")
            priority = issue.get("priority", "")
            comments = issue.get("comments", [])

            content_parts = [
                f"**{key}: {summary}**",
                f"Status: {status}" if status else "",
                f"Priority: {priority}" if priority else "",
                "",
                description[:5000],
            ]

            if comments:
                content_parts.append("\n**Comments:**")
                for comment in comments[:10]:
                    author = comment.get("author", "unknown")
                    body = comment.get("body", "")[:500]
                    content_parts.append(f"- {author}: {body}")

            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="jira",
                title=f"Jira: {key} — {summary}" if key else f"Jira: {summary}",
                content="\n".join(content_parts),
                metadata={
                    "type": "jira_issue",
                    "key": key,
                    "status": status,
                    "priority": priority,
                },
            ))

        # Status distribution evidence
        status_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {}
        for issue in issues:
            s = issue.get("status", "unknown")
            p = issue.get("priority", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1
            priority_counts[p] = priority_counts.get(p, 0) + 1

        dist_lines = ["**Status distribution:**"]
        for s, c in sorted(status_counts.items(), key=lambda x: -x[1]):
            dist_lines.append(f"- {s}: {c}")
        dist_lines.append("\n**Priority distribution:**")
        for p, c in sorted(priority_counts.items(), key=lambda x: -x[1]):
            dist_lines.append(f"- {p}: {c}")

        evidence.append(Evidence(
            source_type=SourceType.JUDGMENT,
            connector="jira",
            title=f"Jira distribution: {len(issues)} issues across {len(status_counts)} statuses",
            content="\n".join(dist_lines),
            metadata={"type": "jira_distribution"},
        ))

        return evidence

    def _extract_issues(self, data: object) -> list[dict]:
        """Extract issue dicts from various Jira export formats."""
        if isinstance(data, list):
            return [self._normalize_issue(item) for item in data if isinstance(item, dict)]

        if isinstance(data, dict):
            if "issues" in data:
                return [self._normalize_issue(item) for item in data["issues"] if isinstance(item, dict)]
            return [self._normalize_issue(data)]

        return []

    def _normalize_issue(self, raw: dict) -> dict:
        """Normalize a Jira issue dict into a consistent format."""
        fields = raw.get("fields", {})
        if fields and isinstance(fields, dict):
            comments_raw = fields.get("comment", {})
            comments = []
            if isinstance(comments_raw, dict):
                for c in comments_raw.get("comments", []):
                    author_data = c.get("author", {})
                    comments.append({
                        "author": author_data.get("displayName", "unknown") if isinstance(author_data, dict) else "unknown",
                        "body": c.get("body", "") if isinstance(c.get("body"), str) else self._adf_to_text(c.get("body")),
                    })

            status_data = fields.get("status", {})
            priority_data = fields.get("priority", {})

            # Description may be ADF (Jira Cloud v3) or plain text
            desc_raw = fields.get("description", "")
            description = desc_raw if isinstance(desc_raw, str) else self._adf_to_text(desc_raw)

            return {
                "key": raw.get("key", ""),
                "summary": fields.get("summary", ""),
                "description": description,
                "status": status_data.get("name", "") if isinstance(status_data, dict) else str(status_data),
                "priority": priority_data.get("name", "") if isinstance(priority_data, dict) else str(priority_data),
                "comments": comments,
            }

        return {
            "key": raw.get("key", raw.get("id", "")),
            "summary": raw.get("summary", raw.get("title", "")),
            "description": raw.get("description", raw.get("body", "")),
            "status": raw.get("status", ""),
            "priority": raw.get("priority", ""),
            "comments": raw.get("comments", []),
        }
