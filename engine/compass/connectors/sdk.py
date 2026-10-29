"""Compass Connector SDK — build custom connectors for any evidence source.

Usage:
    from compass.connectors.sdk import Connector, Evidence, SourceType, SourceConfig

    class ConfluenceConnector(Connector):
        connector_type = "confluence"

        def validate(self) -> bool:
            return bool(self.config.path)

        def ingest(self) -> list[Evidence]:
            # Your ingestion logic here
            return [
                Evidence(
                    source_type=SourceType.DOCS,
                    connector="confluence",
                    title="Page: Architecture Overview",
                    content="...",
                    metadata={"space": "ENG", "page_id": "12345"},
                )
            ]

    # Register your connector:
    from compass.connectors import CONNECTORS
    CONNECTORS["confluence"] = ConfluenceConnector

Then use it:
    compass connect confluence --path /path/to/export
"""

# Re-export the public API for connector authors
from compass.config import SourceConfig
from compass.connectors.base import Connector
from compass.models.sources import Evidence, EvidenceStore, SourceType

__all__ = [
    "Connector",
    "Evidence",
    "EvidenceStore",
    "SourceConfig",
    "SourceType",
]
