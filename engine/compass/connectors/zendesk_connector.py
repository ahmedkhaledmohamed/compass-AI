"""Zendesk connector — part of the JUDGMENT source of truth.

Ingests: Zendesk tickets from REST API or JSON/CSV exports.
Answers: "What are customers STRUGGLING with?"

Dual-mode:
  - Live API: Zendesk REST API (tickets, comments, satisfaction)
  - File import: Zendesk JSON/CSV export files (fallback)
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from compass.connectors.live_base import LiveConnector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

MAX_TICKETS = 500


class ZendeskConnector(LiveConnector):
    """Ingests tickets from Zendesk API or export files."""

    connector_type = "zendesk"
    provider_id = "zendesk"
    rate_limit_rpm = 60

    def validate(self) -> bool:
        path = self.config.path
        if path and Path(path).expanduser().exists():
            return True
        return bool(self.has_credentials())

    def _auth_headers(self) -> dict[str, str]:
        """Zendesk uses Bearer token or basic auth with API token."""
        token = self._get_token()
        if not token:
            return {}

        from compass.server import get_credential
        cred = get_credential(self.provider_id)
        metadata = cred.get("metadata", {}) if cred else {}
        email = metadata.get("email")

        if email:
            import base64
            auth_str = base64.b64encode(f"{email}/token:{token}".encode()).decode()
            return {"Authorization": f"Basic {auth_str}"}

        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Live API ingestion
    # ------------------------------------------------------------------

    def ingest_live(self) -> list[Evidence]:
        """Fetch tickets from Zendesk REST API."""
        evidence: list[Evidence] = []

        subdomain = self._get_subdomain()
        if not subdomain:
            logger.warning("No Zendesk subdomain configured, falling back to file import")
            return self.ingest_file()

        base_url = f"https://{subdomain}.zendesk.com/api/v2"

        all_tickets: list[dict] = []
        url: str | None = f"{base_url}/tickets.json?sort_by=updated_at&sort_order=desc&per_page=100"

        while url and len(all_tickets) < MAX_TICKETS:
            try:
                res = self._api_get(url)
                data = res.json()
                tickets = data.get("tickets", [])
                if not tickets:
                    break
                all_tickets.extend(tickets)
                url = data.get("next_page")
            except Exception as e:
                logger.warning("Failed to fetch Zendesk tickets: %s", e, exc_info=True)
                break

        if not all_tickets:
            return evidence

        # Summary
        summary_lines = []
        for ticket in all_tickets[:MAX_TICKETS]:
            subject = ticket.get("subject", "")
            status = ticket.get("status", "")
            tags = ticket.get("tags", [])
            tag_str = f" [{', '.join(tags[:3])}]" if tags else ""
            summary_lines.append(f"- [{status}] {subject}{tag_str}")

        evidence.append(Evidence(
            source_type=SourceType.JUDGMENT,
            connector="zendesk",
            title=f"Zendesk tickets ({len(all_tickets)} recent)",
            content=f"Zendesk tickets: {len(all_tickets)} total\n\n" + "\n".join(summary_lines),
            metadata={"type": "zendesk_export", "ticket_count": len(all_tickets), "source": "api", "subdomain": subdomain},
        ))

        # Per-ticket with comments
        for ticket in all_tickets[:MAX_TICKETS]:
            description = ticket.get("description", "")
            if not description or len(description) < 50:
                continue

            subject = ticket.get("subject", "")
            status = ticket.get("status", "")
            priority = ticket.get("priority", "")
            satisfaction = ticket.get("satisfaction_rating", {})
            sat_score = satisfaction.get("score", "") if isinstance(satisfaction, dict) else ""

            ticket_id = ticket.get("id")
            comment_text = ""
            if ticket_id:
                try:
                    comments_res = self._api_get(f"{base_url}/tickets/{ticket_id}/comments.json")
                    comments = comments_res.json().get("comments", [])
                    if comments:
                        comment_lines = [f"- {c.get('author_id', 'unknown')}: {c.get('body', '')[:300]}" for c in comments[:10]]
                        comment_text = "\n\n**Comments:**\n" + "\n".join(comment_lines)
                except Exception:
                    logger.debug("Could not enrich ticket evidence with satisfaction ratings.", exc_info=True)

            content_parts = [
                f"**{subject}**",
                f"Status: {status}",
                f"Priority: {priority}" if priority else "",
                f"Satisfaction: {sat_score}" if sat_score else "",
                "",
                description[:5000],
                comment_text,
            ]

            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="zendesk",
                title=f"Zendesk: {subject}",
                content="\n".join(p for p in content_parts if p),
                metadata={"type": "zendesk_ticket", "status": status, "priority": priority or "", "source": "api"},
            ))

        # Tag distribution
        tag_counts: dict[str, int] = {}
        for ticket in all_tickets:
            for tag in ticket.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        if tag_counts:
            dist_lines = ["**Tag distribution:**"]
            for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1])[:20]:
                dist_lines.append(f"- {tag}: {count}")
            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="zendesk",
                title=f"Zendesk tags: {len(tag_counts)} unique tags across {len(all_tickets)} tickets",
                content="\n".join(dist_lines),
                metadata={"type": "zendesk_distribution", "source": "api"},
            ))

        logger.info("Zendesk live: fetched %d evidence items from %s", len(evidence), subdomain)
        return evidence

    def _get_subdomain(self) -> str | None:
        subdomain = self.config.options.get("subdomain")
        if subdomain:
            return subdomain
        from compass.server import get_credential
        cred = get_credential(self.provider_id)
        if cred:
            metadata = cred.get("metadata", {})
            if metadata.get("subdomain"):
                return metadata["subdomain"]
        url = self.config.url
        if url and "zendesk.com" in url:
            parts = url.split("//")[-1].split(".")
            if parts:
                return parts[0]
        return None

    # ------------------------------------------------------------------
    # File-based ingestion (original behavior)
    # ------------------------------------------------------------------

    def ingest_file(self) -> list[Evidence]:
        path = self.config.path
        if not path:
            return []
        p = Path(path).expanduser().resolve()
        evidence: list[Evidence] = []
        if p.is_file():
            if p.suffix.lower() == ".json":
                evidence.extend(self._ingest_json(p))
            elif p.suffix.lower() == ".csv":
                evidence.extend(self._ingest_csv(p))
        elif p.is_dir():
            for fpath in sorted(p.rglob("*.json"))[:20]:
                evidence.extend(self._ingest_json(fpath))
            for fpath in sorted(p.rglob("*.csv"))[:20]:
                evidence.extend(self._ingest_csv(fpath))
        return evidence

    def _ingest_json(self, fpath: Path) -> list[Evidence]:
        try:
            data = json.loads(fpath.read_text(errors="ignore"))
        except (json.JSONDecodeError, OSError):
            return []

        tickets = []
        if isinstance(data, list):
            tickets = data
        elif isinstance(data, dict):
            tickets = data.get("tickets", data.get("results", []))
        if not tickets:
            return []

        evidence: list[Evidence] = []

        summary_lines = []
        for ticket in tickets[:MAX_TICKETS]:
            subject = ticket.get("subject", ticket.get("title", ""))
            status = ticket.get("status", "")
            tags = ticket.get("tags", [])
            tag_str = f" [{', '.join(tags[:3])}]" if tags else ""
            summary_lines.append(f"- [{status}] {subject}{tag_str}")

        evidence.append(Evidence(
            source_type=SourceType.JUDGMENT,
            connector="zendesk",
            title=f"Zendesk tickets: {fpath.stem} ({len(tickets)} tickets)",
            content=f"Zendesk tickets from {fpath.name}: {len(tickets)} total\n\n" + "\n".join(summary_lines),
            metadata={"file": str(fpath), "type": "zendesk_export", "ticket_count": len(tickets)},
        ))

        for ticket in tickets[:MAX_TICKETS]:
            description = ticket.get("description", "")
            if not description or len(description) < 50:
                continue
            subject = ticket.get("subject", ticket.get("title", ""))
            status = ticket.get("status", "")
            priority = ticket.get("priority", "")
            comments = ticket.get("comments", [])
            comment_text = ""
            if comments:
                comment_text = "\n\n**Comments:**\n" + "\n".join(
                    f"- {c.get('author', 'unknown')}: {c.get('body', '')[:300]}" for c in comments[:5]
                )
            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="zendesk",
                title=f"Zendesk: {subject}",
                content=f"**{subject}**\nStatus: {status}\nPriority: {priority}\n\n{description[:5000]}{comment_text}",
                metadata={"type": "zendesk_ticket", "status": status, "priority": priority},
            ))

        tag_counts: dict[str, int] = {}
        for ticket in tickets:
            for tag in ticket.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if tag_counts:
            dist_lines = ["**Tag distribution:**"]
            for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1])[:20]:
                dist_lines.append(f"- {tag}: {count}")
            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="zendesk",
                title=f"Zendesk tags: {len(tag_counts)} unique tags across {len(tickets)} tickets",
                content="\n".join(dist_lines),
                metadata={"type": "zendesk_distribution"},
            ))

        return evidence

    def _ingest_csv(self, fpath: Path) -> list[Evidence]:
        try:
            with open(fpath, newline="", errors="ignore") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except (OSError, csv.Error):
            return []
        if not rows:
            return []

        original_headers = list(rows[0].keys())
        header_map = {h.lower(): h for h in original_headers}
        lowered = list(header_map.keys())
        subject_col = self._find_col(lowered, ["subject", "title", "summary"])
        status_col = self._find_col(lowered, ["status", "state"])
        subject_key = header_map[subject_col] if subject_col else None
        status_key = header_map[status_col] if status_col else None

        summary_lines = []
        for row in rows[:MAX_TICKETS]:
            subject = row.get(subject_key, "Ticket") if subject_key else "Ticket"
            status = row.get(status_key, "") if status_key else ""
            status_str = f" [{status}]" if status else ""
            summary_lines.append(f"- {subject}{status_str}")

        return [Evidence(
            source_type=SourceType.JUDGMENT,
            connector="zendesk",
            title=f"Zendesk CSV: {fpath.stem} ({len(rows)} tickets)",
            content=f"Zendesk tickets: {len(rows)} total\n\n" + "\n".join(summary_lines),
            metadata={"file": str(fpath), "type": "zendesk_csv", "ticket_count": len(rows)},
        )]

    def _find_col(self, headers: list[str], candidates: list[str]) -> str | None:
        for c in candidates:
            if c in headers:
                return c
        return None
