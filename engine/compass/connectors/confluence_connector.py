"""Confluence connector — part of the DOCS source of truth.

Ingests: Confluence pages from Atlassian REST API or HTML/XML exports.
Answers: "What is DOCUMENTED?"

Dual-mode:
  - Live API: Atlassian Confluence REST API v2 (pages by space)
  - File import: Confluence HTML/XML export directories (fallback)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from compass.connectors.live_base import LiveConnector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

MAX_PAGES = 300


class ConfluenceConnector(LiveConnector):
    """Ingests pages from Confluence API or HTML/XML exports."""

    connector_type = "confluence"
    provider_id = "atlassian"  # Shares OAuth token with Jira
    rate_limit_rpm = 60

    def validate(self) -> bool:
        path = self.config.path
        if path and Path(path).expanduser().exists():
            return True
        return bool(self.has_credentials())

    # ------------------------------------------------------------------
    # Live API ingestion
    # ------------------------------------------------------------------

    def ingest_live(self) -> list[Evidence]:
        """Fetch pages from Confluence REST API."""
        evidence: list[Evidence] = []

        site = self._get_site()
        if not site:
            logger.warning("No Confluence site configured, falling back to file import")
            return self.ingest_file()

        base_url = f"https://{site}.atlassian.net/wiki/api/v2"
        space_key = self.config.options.get("space")

        # Fetch pages
        all_pages: list[dict] = []
        cursor: str | None = None

        while len(all_pages) < MAX_PAGES:
            params: dict[str, str] = {"limit": "50", "sort": "-modified-date"}
            if space_key:
                params["space-key"] = space_key
            if cursor:
                params["cursor"] = cursor

            try:
                res = self._api_get(f"{base_url}/pages", params=params)
                data = res.json()
                pages = data.get("results", [])
                if not pages:
                    break
                all_pages.extend(pages)
                # Pagination
                links = data.get("_links", {})
                next_link = links.get("next")
                if not next_link:
                    break
                # Extract cursor from next URL
                if "cursor=" in next_link:
                    cursor = next_link.split("cursor=")[-1].split("&")[0]
                else:
                    break
            except Exception as e:
                logger.warning("Failed to fetch Confluence pages: %s", e, exc_info=True)
                break

        # Fetch body for each page and create evidence
        for page in all_pages[:MAX_PAGES]:
            page_id = page.get("id", "")
            title = page.get("title", "Untitled")

            try:
                body_res = self._api_get(
                    f"{base_url}/pages/{page_id}",
                    params={"body-format": "storage"},
                )
                page_data = body_res.json()
                body_storage = page_data.get("body", {}).get("storage", {}).get("value", "")

                # Convert storage format (XHTML-like) to plain text
                text = self._storage_to_text(body_storage)
                if len(text) < 50:
                    continue

                evidence.append(Evidence(
                    source_type=SourceType.DOCS,
                    connector="confluence",
                    title=f"Confluence: {title}",
                    content=text[:15_000],
                    metadata={
                        "type": "confluence_page",
                        "page_id": page_id,
                        "source": "api",
                        "site": site,
                    },
                ))
            except Exception as e:
                logger.debug("Failed to fetch body for page %s: %s", page_id, e, exc_info=True)

        logger.info("Confluence live: fetched %d evidence items from %s", len(evidence), site)
        return evidence

    def _get_site(self) -> str | None:
        """Get the Atlassian site subdomain."""
        site = self.config.options.get("site")
        if site:
            return site
        url = self.config.url
        if url and "atlassian.net" in url:
            parts = url.split("//")[-1].split(".")
            if parts:
                return parts[0]
        return None

    def _storage_to_text(self, storage: str) -> str:
        """Convert Confluence storage format (XHTML) to plain text."""
        if not storage:
            return ""
        text = re.sub(r"<ac:structured-macro[^>]*>.*?</ac:structured-macro>", "", storage, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ------------------------------------------------------------------
    # File-based ingestion (original behavior)
    # ------------------------------------------------------------------

    def ingest_file(self) -> list[Evidence]:
        path = self.config.path
        if not path:
            return []

        p = Path(path).expanduser().resolve()
        evidence: list[Evidence] = []

        if p.is_dir():
            html_files = sorted(p.rglob("*.html"))[:MAX_PAGES]
            for fpath in html_files:
                ev = self._ingest_html(fpath)
                if ev:
                    evidence.append(ev)

            md_files = sorted(p.rglob("*.md"))[:MAX_PAGES]
            for fpath in md_files:
                ev = self._ingest_markdown(fpath)
                if ev:
                    evidence.append(ev)

        return evidence

    def _ingest_html(self, fpath: Path) -> Evidence | None:
        try:
            content = fpath.read_text(errors="ignore")
        except OSError:
            return None

        if not content.strip():
            return None

        text = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) < 50:
            return None

        title = self._extract_title(content, fpath.stem)

        return Evidence(
            source_type=SourceType.DOCS,
            connector="confluence",
            title=f"Confluence: {title}",
            content=text[:15_000],
            metadata={"file": str(fpath), "type": "confluence_page"},
        )

    def _ingest_markdown(self, fpath: Path) -> Evidence | None:
        try:
            content = fpath.read_text(errors="ignore")
        except OSError:
            return None

        if not content.strip() or len(content.strip()) < 50:
            return None

        title = fpath.stem.replace("-", " ").replace("_", " ").title()

        return Evidence(
            source_type=SourceType.DOCS,
            connector="confluence",
            title=f"Confluence: {title}",
            content=content[:15_000],
            metadata={"file": str(fpath), "type": "confluence_page"},
        )

    def _extract_title(self, html: str, fallback: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            title = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            if title:
                return title
        return fallback.replace("-", " ").replace("_", " ").title()
