"""Support ticket connector — part of the JUDGMENT source of truth.

Ingests: Support tickets, bug reports, feature requests (CSV or folder).
Answers: "What are users STRUGGLING with?"
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from compass.connectors.base import Connector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

MAX_TICKETS = 200


class SupportConnector(Connector):
    """Ingests support tickets and customer feedback."""

    connector_type = "support"

    def validate(self) -> bool:
        path = self.config.path
        if not path:
            return False
        return Path(path).expanduser().exists()

    def ingest(self) -> list[Evidence]:
        path = self.config.path
        if not path:
            return []

        p = Path(path).expanduser().resolve()
        evidence: list[Evidence] = []

        if p.is_file() and p.suffix.lower() == ".csv":
            evidence.extend(self._ingest_csv(p))
        elif p.is_dir():
            for fpath in sorted(p.rglob("*.csv")):
                evidence.extend(self._ingest_csv(fpath))
            for fpath in sorted(p.rglob("*.md")) + sorted(p.rglob("*.txt")):
                ev = self._ingest_text(fpath)
                if ev:
                    evidence.append(ev)

        return evidence

    def _detect_delimiter(self, fpath: Path) -> str:
        """Detect CSV delimiter by sniffing the first few lines."""
        try:
            with open(fpath, newline="", errors="ignore") as f:
                sample = f.read(4096)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            return ","

    def _ingest_csv(self, fpath: Path) -> list[Evidence]:
        """Ingest a CSV of support tickets. Expects columns like: title/subject, description/body, category/type."""
        try:
            delimiter = self._detect_delimiter(fpath)
            with open(fpath, newline="", errors="ignore") as f:
                # Skip BOM if present
                first_char = f.read(1)
                if first_char != "\ufeff":
                    f.seek(0)
                reader = csv.DictReader(f, delimiter=delimiter)
                rows = list(reader)

            if not rows:
                return []

            headers = [h.lower() for h in rows[0]]
            title_col = self._find_column(headers, ["title", "subject", "summary", "name"])
            body_col = self._find_column(headers, ["description", "body", "content", "text", "message"])
            category_col = self._find_column(headers, ["category", "type", "tag", "label"])

            evidence: list[Evidence] = []

            # Create a summary of all tickets
            ticket_lines = []
            for i, row in enumerate(rows[:MAX_TICKETS]):
                values = list(row.values())
                title = values[headers.index(title_col)] if title_col else f"Ticket {i+1}"
                body = values[headers.index(body_col)] if body_col else " | ".join(values)
                category = values[headers.index(category_col)] if category_col else ""
                cat_str = f" [{category}]" if category else ""
                ticket_lines.append(f"- {title}{cat_str}: {body[:200]}")

            summary = (
                f"Support tickets from {fpath.name}: {len(rows)} total\n\n"
                + "\n".join(ticket_lines)
            )

            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="support",
                title=f"Support tickets: {fpath.stem} ({len(rows)} tickets)",
                content=summary,
                metadata={
                    "file": str(fpath),
                    "type": "support_csv",
                    "ticket_count": len(rows),
                },
            ))

            # Also create per-category summaries if categories exist
            if category_col:
                categories: dict[str, list[str]] = {}
                for row in rows[:MAX_TICKETS]:
                    values = list(row.values())
                    cat = values[headers.index(category_col)].strip()
                    title = values[headers.index(title_col)] if title_col else ""
                    body = values[headers.index(body_col)] if body_col else " | ".join(values)
                    categories.setdefault(cat, []).append(f"{title}: {body[:150]}")

                for cat, tickets in categories.items():
                    if not cat:
                        continue
                    evidence.append(Evidence(
                        source_type=SourceType.JUDGMENT,
                        connector="support",
                        title=f"Support: {cat} ({len(tickets)} tickets)",
                        content=f"Category: {cat}\nTickets:\n" + "\n".join(f"- {t}" for t in tickets),
                        metadata={"type": "support_category", "category": cat, "count": len(tickets)},
                    ))

            return evidence
        except Exception as e:
            logger.warning("Failed to ingest support CSV %s: %s", fpath, e, exc_info=True)
            return []

    def _ingest_text(self, fpath: Path) -> Evidence | None:
        try:
            content = fpath.read_text(errors="ignore")
            if not content.strip():
                return None
            return Evidence(
                source_type=SourceType.JUDGMENT,
                connector="support",
                title=f"Support: {fpath.stem.replace('-', ' ').replace('_', ' ').title()}",
                content=content[:10_000],
                metadata={"file": str(fpath), "type": "support_text"},
            )
        except Exception as e:
            logger.warning("Failed to ingest support text %s: %s", fpath, e, exc_info=True)
            return None

    def _find_column(self, headers: list[str], candidates: list[str]) -> str | None:
        # Exact match first
        for candidate in candidates:
            if candidate in headers:
                return candidate
        # Substring match (e.g., "issue_title" matches "title")
        for candidate in candidates:
            for header in headers:
                if candidate in header:
                    return header
        return None
