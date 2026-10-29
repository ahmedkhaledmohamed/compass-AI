"""Notion connector — part of the DOCS source of truth.

Ingests: Notion pages from API or markdown exports.
Answers: "What is DOCUMENTED and DECIDED?"

Dual-mode:
  - Live API: Notion API v1 (databases, pages, block children)
  - File import: Notion markdown/CSV export directories (fallback)
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from compass.connectors.live_base import LiveConnector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

MAX_PAGES = 200

NOTION_API = "https://api.notion.com/v1"


class NotionConnector(LiveConnector):
    """Ingests pages from Notion API or markdown export directories."""

    connector_type = "notion"
    provider_id = "notion"
    rate_limit_rpm = 180

    def validate(self) -> bool:
        path = self.config.path
        if path and Path(path).expanduser().exists():
            return True
        return bool(self.has_credentials())

    def _auth_headers(self) -> dict[str, str]:
        """Notion requires Notion-Version header."""
        headers = super()._auth_headers()
        headers["Notion-Version"] = "2022-06-28"
        return headers

    # ------------------------------------------------------------------
    # Live API ingestion
    # ------------------------------------------------------------------

    def ingest_live(self) -> list[Evidence]:
        """Fetch pages and databases from Notion API."""
        evidence: list[Evidence] = []

        # Search for pages
        all_pages: list[dict] = []
        cursor: str | None = None

        while len(all_pages) < MAX_PAGES:
            body: dict = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            try:
                res = self._api_post(f"{NOTION_API}/search", json=body)
                data = res.json()
                results = data.get("results", [])
                if not results:
                    break
                all_pages.extend(results)
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
            except Exception as e:
                logger.warning("Failed to search Notion pages: %s", e, exc_info=True)
                break

        for page in all_pages[:MAX_PAGES]:
            page_id = page.get("id", "")
            title = self._extract_page_title(page)
            try:
                blocks_res = self._api_get(
                    f"{NOTION_API}/blocks/{page_id}/children",
                    params={"page_size": "100"},
                )
                blocks = blocks_res.json().get("results", [])
                text = self._blocks_to_text(blocks)
                if len(text) < 50:
                    continue
                evidence.append(Evidence(
                    source_type=SourceType.DOCS,
                    connector="notion",
                    title=f"Notion: {title}",
                    content=text[:15_000],
                    metadata={"type": "notion_page", "page_id": page_id, "source": "api"},
                ))
            except Exception as e:
                logger.debug("Failed to fetch blocks for page %s: %s", page_id, e, exc_info=True)

        # Search for databases
        try:
            db_body: dict = {"filter": {"property": "object", "value": "database"}, "page_size": 20}
            db_res = self._api_post(f"{NOTION_API}/search", json=db_body)
            databases = db_res.json().get("results", [])
            for db in databases:
                db_id = db.get("id", "")
                db_title = self._extract_db_title(db)
                try:
                    query_res = self._api_post(
                        f"{NOTION_API}/databases/{db_id}/query",
                        json={"page_size": 100},
                    )
                    rows = query_res.json().get("results", [])
                    if rows:
                        lines = []
                        for row in rows[:100]:
                            props = row.get("properties", {})
                            line_parts = []
                            for prop_name, prop_val in props.items():
                                text_val = self._extract_property_text(prop_val)
                                if text_val:
                                    line_parts.append(f"{prop_name}: {text_val[:200]}")
                            if line_parts:
                                lines.append("- " + " | ".join(line_parts))
                        evidence.append(Evidence(
                            source_type=SourceType.DOCS,
                            connector="notion",
                            title=f"Notion DB: {db_title} ({len(rows)} rows)",
                            content=f"Notion database: {db_title}\nRows: {len(rows)}\n\n" + "\n".join(lines),
                            metadata={"type": "notion_database", "database_id": db_id, "row_count": len(rows), "source": "api"},
                        ))
                except Exception as e:
                    logger.debug("Failed to query database %s: %s", db_id, e, exc_info=True)
        except Exception as e:
            logger.debug("Failed to search Notion databases: %s", e, exc_info=True)

        logger.info("Notion live: fetched %d evidence items", len(evidence))
        return evidence

    def _extract_page_title(self, page: dict) -> str:
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                return "".join(t.get("plain_text", "") for t in prop.get("title", [])) or "Untitled"
        return "Untitled"

    def _extract_db_title(self, db: dict) -> str:
        return "".join(t.get("plain_text", "") for t in db.get("title", [])) or "Untitled Database"

    def _extract_property_text(self, prop: dict) -> str:
        ptype = prop.get("type", "")
        if ptype == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
        if ptype == "rich_text":
            return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
        if ptype == "number":
            val = prop.get("number")
            return str(val) if val is not None else ""
        if ptype == "select":
            sel = prop.get("select")
            return sel.get("name", "") if sel else ""
        if ptype == "multi_select":
            return ", ".join(s.get("name", "") for s in prop.get("multi_select", []))
        if ptype == "status":
            st = prop.get("status")
            return st.get("name", "") if st else ""
        if ptype == "checkbox":
            return "Yes" if prop.get("checkbox") else "No"
        if ptype == "date":
            d = prop.get("date")
            return d.get("start", "") if d else ""
        if ptype == "url":
            return prop.get("url", "") or ""
        return ""

    def _blocks_to_text(self, blocks: list[dict]) -> str:
        parts: list[str] = []
        for block in blocks:
            btype = block.get("type", "")
            data = block.get(btype, {})
            if btype in ("paragraph", "heading_1", "heading_2", "heading_3", "quote", "callout"):
                text = "".join(t.get("plain_text", "") for t in data.get("rich_text", []))
                if text:
                    parts.append(text)
            elif btype in ("bulleted_list_item", "numbered_list_item"):
                text = "".join(t.get("plain_text", "") for t in data.get("rich_text", []))
                if text:
                    parts.append(f"- {text}")
            elif btype == "to_do":
                text = "".join(t.get("plain_text", "") for t in data.get("rich_text", []))
                checked = data.get("checked", False)
                if text:
                    parts.append(f"[{'x' if checked else ' '}] {text}")
            elif btype == "code":
                text = "".join(t.get("plain_text", "") for t in data.get("rich_text", []))
                if text:
                    parts.append(f"```{data.get('language', '')}\n{text}\n```")
            elif btype == "divider":
                parts.append("---")
        return "\n".join(parts)

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
            if p.suffix.lower() == ".md":
                ev = self._ingest_markdown(p)
                if ev:
                    evidence.append(ev)
            elif p.suffix.lower() == ".csv":
                evidence.extend(self._ingest_csv(p))
        elif p.is_dir():
            md_files = sorted(p.rglob("*.md"))[:MAX_PAGES]
            for fpath in md_files:
                ev = self._ingest_markdown(fpath)
                if ev:
                    evidence.append(ev)
            for fpath in sorted(p.rglob("*.csv"))[:20]:
                evidence.extend(self._ingest_csv(fpath))

        return evidence

    def _ingest_markdown(self, fpath: Path) -> Evidence | None:
        try:
            content = fpath.read_text(errors="ignore")
        except OSError:
            return None
        if not content.strip():
            return None
        title = self._clean_notion_title(fpath.stem)
        return Evidence(
            source_type=SourceType.DOCS,
            connector="notion",
            title=f"Notion: {title}",
            content=content[:15_000],
            metadata={"file": str(fpath), "type": "notion_page"},
        )

    def _ingest_csv(self, fpath: Path) -> list[Evidence]:
        try:
            with open(fpath, newline="", errors="ignore") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except (OSError, csv.Error):
            return []
        if not rows:
            return []

        headers = list(rows[0].keys())
        name_col = self._find_column(headers, ["name", "title", "page", "task"])

        summary_lines = []
        for row in rows[:MAX_PAGES]:
            if name_col:
                name = row.get(name_col, "")
                other = " | ".join(f"{k}: {v}" for k, v in row.items() if k != name_col and v and len(v) < 200)
                summary_lines.append(f"- **{name}**: {other[:300]}")
            else:
                summary_lines.append("- " + " | ".join(f"{k}: {v}" for k, v in row.items() if v)[:300])

        db_title = self._clean_notion_title(fpath.stem)
        return [Evidence(
            source_type=SourceType.DOCS,
            connector="notion",
            title=f"Notion DB: {db_title} ({len(rows)} rows)",
            content=(
                f"Notion database: {db_title}\nColumns: {', '.join(headers)}\n"
                f"Rows: {len(rows)}\n\n" + "\n".join(summary_lines)
            ),
            metadata={"file": str(fpath), "type": "notion_database", "row_count": len(rows), "columns": headers},
        )]

    def _clean_notion_title(self, stem: str) -> str:
        parts = stem.rsplit(" ", 1)
        if len(parts) == 2 and len(parts[1]) == 32:
            try:
                int(parts[1], 16)
                return parts[0]
            except ValueError:
                pass
        return stem.replace("-", " ").replace("_", " ")

    def _find_column(self, headers: list[str], candidates: list[str]) -> str | None:
        headers_lower = [h.lower() for h in headers]
        for candidate in candidates:
            if candidate in headers_lower:
                idx = headers_lower.index(candidate)
                return headers[idx]
        return None
