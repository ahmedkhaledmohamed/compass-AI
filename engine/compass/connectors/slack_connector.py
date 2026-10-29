"""Slack connector — part of the JUDGMENT source of truth.

Ingests: Slack messages from Web API or export directories.
Answers: "What is the team DISCUSSING and DECIDING?"

Dual-mode:
  - Live API: Slack Web API (conversations.list/history, search.messages)
  - File import: Slack export directory structure
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from compass.connectors.live_base import LiveConnector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

MAX_CHANNELS = 50
MAX_DAYS_PER_CHANNEL = 30
MIN_MESSAGES_PER_DAY = 3

SLACK_API = "https://slack.com/api"


class SlackConnector(LiveConnector):
    """Ingests conversations from Slack API or export directories."""

    connector_type = "slack"
    provider_id = "slack"
    rate_limit_rpm = 50  # Slack Tier 3

    def validate(self) -> bool:
        path = self.config.path
        if path and Path(path).expanduser().is_dir():
            return True
        return bool(self.has_credentials())

    # ------------------------------------------------------------------
    # Live API ingestion
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Slack uses Bearer token in Authorization header."""
        token = self._get_token()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def ingest_live(self) -> list[Evidence]:
        """Fetch messages from Slack Web API."""
        evidence: list[Evidence] = []

        # Get channel list
        try:
            res = self._api_get(
                f"{SLACK_API}/conversations.list",
                params={"types": "public_channel", "limit": str(MAX_CHANNELS), "exclude_archived": "true"},
            )
            data = res.json()
            if not data.get("ok"):
                logger.warning("Slack conversations.list failed: %s", data.get("error"))
                return self.ingest_file()
            channels = data.get("channels", [])
        except Exception as e:
            logger.warning("Failed to fetch Slack channels: %s", e, exc_info=True)
            return self.ingest_file()

        for channel in channels[:MAX_CHANNELS]:
            channel_id = channel.get("id", "")
            channel_name = channel.get("name", channel_id)

            try:
                history_res = self._api_get(
                    f"{SLACK_API}/conversations.history",
                    params={"channel": channel_id, "limit": "200"},
                )
                history = history_res.json()
                if not history.get("ok"):
                    continue
                messages = history.get("messages", [])
            except Exception as e:
                logger.debug("Failed to fetch history for #%s: %s", channel_name, e, exc_info=True)
                continue

            # Filter bot/system messages
            human_messages = [
                m for m in messages
                if isinstance(m, dict)
                and not m.get("bot_id")
                and not m.get("subtype")
                and m.get("text", "").strip()
            ]

            if not human_messages:
                continue

            # Format messages
            lines = []
            for msg in human_messages[:100]:
                user = msg.get("user", "unknown")
                text = msg.get("text", "")[:500]
                lines.append(f"**{user}:** {text}")

            content = (
                f"Slack channel #{channel_name}: {len(human_messages)} recent messages\n\n"
                + "\n".join(lines)
            )

            evidence.append(Evidence(
                source_type=SourceType.JUDGMENT,
                connector="slack",
                title=f"Slack #{channel_name} ({len(human_messages)} messages)",
                content=content[:15_000],
                metadata={
                    "type": "slack_channel_summary",
                    "channel": channel_name,
                    "channel_id": channel_id,
                    "total_messages": len(human_messages),
                    "source": "api",
                },
            ))

        # Also search for relevant messages if search query is configured
        search_query = self.config.options.get("search_query")
        if search_query:
            try:
                search_res = self._api_get(
                    f"{SLACK_API}/search.messages",
                    params={"query": search_query, "count": "50"},
                )
                search_data = search_res.json()
                if search_data.get("ok"):
                    matches = search_data.get("messages", {}).get("matches", [])
                    for match in matches[:50]:
                        text = match.get("text", "")[:2000]
                        channel_info = match.get("channel", {})
                        ch_name = channel_info.get("name", "unknown") if isinstance(channel_info, dict) else "unknown"
                        user = match.get("user", "") or match.get("username", "unknown")
                        evidence.append(Evidence(
                            source_type=SourceType.JUDGMENT,
                            connector="slack",
                            title=f"Slack search: #{ch_name} by {user}",
                            content=text,
                            metadata={
                                "type": "slack_search_result",
                                "channel": ch_name,
                                "query": search_query,
                                "source": "api",
                            },
                        ))
            except Exception as e:
                logger.debug("Slack search failed: %s", e, exc_info=True)

        logger.info("Slack live: fetched %d evidence items", len(evidence))
        return evidence

    # ------------------------------------------------------------------
    # File-based ingestion (original behavior)
    # ------------------------------------------------------------------

    def ingest_file(self) -> list[Evidence]:
        path = self.config.path
        if not path:
            return []

        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            return []

        evidence: list[Evidence] = []
        users = self._load_users(p)

        channel_dirs = sorted(
            [d for d in p.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda d: d.name,
        )[:MAX_CHANNELS]

        for channel_dir in channel_dirs:
            channel_evidence = self._ingest_channel(channel_dir, users)
            evidence.extend(channel_evidence)

        return evidence

    def _load_users(self, export_dir: Path) -> dict[str, str]:
        """Load user ID -> display name mapping from users.json."""
        users_file = export_dir / "users.json"
        if not users_file.exists():
            return {}

        try:
            data = json.loads(users_file.read_text(errors="ignore"))
            return {
                u["id"]: u.get("real_name") or u.get("name", u["id"])
                for u in data
                if isinstance(u, dict) and "id" in u
            }
        except (json.JSONDecodeError, OSError):
            return {}

    def _ingest_channel(self, channel_dir: Path, users: dict[str, str]) -> list[Evidence]:
        """Ingest a single channel directory into evidence items."""
        channel_name = channel_dir.name
        day_files = sorted(channel_dir.glob("*.json"))[:MAX_DAYS_PER_CHANNEL]

        if not day_files:
            return []

        evidence: list[Evidence] = []
        all_messages: list[str] = []
        total_count = 0

        for day_file in day_files:
            try:
                messages = json.loads(day_file.read_text(errors="ignore"))
            except (json.JSONDecodeError, OSError):
                continue

            if not isinstance(messages, list):
                continue

            human_messages = [
                m for m in messages
                if isinstance(m, dict)
                and not m.get("bot_id")
                and not m.get("subtype")
                and m.get("text", "").strip()
            ]

            if len(human_messages) < MIN_MESSAGES_PER_DAY:
                continue

            total_count += len(human_messages)
            date_str = day_file.stem

            day_lines = [f"### {date_str}"]
            for msg in human_messages[:50]:
                user_id = msg.get("user", "unknown")
                user_name = users.get(user_id, user_id)
                text = msg["text"][:500]
                day_lines.append(f"**{user_name}:** {text}")

            day_summary = "\n".join(day_lines)
            all_messages.append(day_summary)

            if len(human_messages) >= 10:
                evidence.append(Evidence(
                    source_type=SourceType.JUDGMENT,
                    connector="slack",
                    title=f"Slack #{channel_name} — {date_str} ({len(human_messages)} messages)",
                    content=day_summary[:10_000],
                    metadata={
                        "type": "slack_conversation",
                        "channel": channel_name,
                        "date": date_str,
                        "message_count": len(human_messages),
                    },
                ))

        if all_messages:
            summary_content = (
                f"Slack channel #{channel_name}: {total_count} messages across {len(all_messages)} days\n\n"
                + "\n\n".join(all_messages[:10])
            )
            evidence.insert(0, Evidence(
                source_type=SourceType.JUDGMENT,
                connector="slack",
                title=f"Slack #{channel_name} summary ({total_count} messages)",
                content=summary_content[:15_000],
                metadata={
                    "type": "slack_channel_summary",
                    "channel": channel_name,
                    "total_messages": total_count,
                    "days": len(all_messages),
                },
            ))

        return evidence
