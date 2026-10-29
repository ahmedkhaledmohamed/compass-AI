"""Evidence source connectors."""

from compass.connectors.analytics import AnalyticsConnector
from compass.connectors.base import Connector
from compass.connectors.confluence_connector import ConfluenceConnector
from compass.connectors.docs import DocsConnector
from compass.connectors.github_connector import GitHubConnector
from compass.connectors.google_docs_connector import GoogleDocsConnector
from compass.connectors.interviews import InterviewConnector
from compass.connectors.jira_connector import JiraConnector
from compass.connectors.linear_connector import LinearConnector
from compass.connectors.notion_connector import NotionConnector
from compass.connectors.slack_connector import SlackConnector
from compass.connectors.support import SupportConnector
from compass.connectors.zendesk_connector import ZendeskConnector

CONNECTORS: dict[str, type[Connector]] = {
    "github": GitHubConnector,
    "code": GitHubConnector,
    "docs": DocsConnector,
    "google_docs": GoogleDocsConnector,
    "google_drive": GoogleDocsConnector,
    "analytics": AnalyticsConnector,
    "data": AnalyticsConnector,
    "interviews": InterviewConnector,
    "support": SupportConnector,
    "jira": JiraConnector,
    "slack": SlackConnector,
    "linear": LinearConnector,
    "notion": NotionConnector,
    "confluence": ConfluenceConnector,
    "zendesk": ZendeskConnector,
}


def get_connector(connector_type: str) -> type[Connector]:
    """Get a connector class by type name."""
    if connector_type not in CONNECTORS:
        available = ", ".join(CONNECTORS.keys())
        raise ValueError(f"Unknown connector type: {connector_type}. Available: {available}")
    return CONNECTORS[connector_type]


__all__ = [
    "CONNECTORS",
    "AnalyticsConnector",
    "ConfluenceConnector",
    "Connector",
    "DocsConnector",
    "GitHubConnector",
    "GoogleDocsConnector",
    "InterviewConnector",
    "JiraConnector",
    "LinearConnector",
    "NotionConnector",
    "SlackConnector",
    "SupportConnector",
    "ZendeskConnector",
    "get_connector",
]
