"""Product workspace configuration."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import yaml
from pydantic import BaseModel, Field, model_validator

COMPASS_DIR = ".compass"
CONFIG_FILE = "compass.yaml"
OUTPUT_DIR = "output"


class AuthConfig(BaseModel):
    """Auth configuration for a source connector.

    The credential_ref is a logical key (e.g. "github") that maps to
    credentials stored in the Electron vault. The engine never persists
    raw tokens — they are injected at runtime via POST /credentials/inject.
    """

    method: str = "oauth"  # "oauth", "api_key", "pat"
    credential_ref: str = ""  # e.g. "github", "atlassian", "slack"
    scopes: list[str] = Field(default_factory=list)


class SourceConfig(BaseModel):
    """Configuration for a single evidence source."""

    id: str = ""
    legacy_names: list[str] = Field(default_factory=list)
    type: str  # code, docs, data, interviews, support
    name: str
    path: str | None = None
    url: str | None = None
    options: dict = Field(default_factory=dict)
    auth: AuthConfig | None = None

    @model_validator(mode="after")
    def stable_identity(self):
        if not self.id:
            self.legacy_names = list(dict.fromkeys([*self.legacy_names, self.name]))
            self.id = "src_" + uuid5(NAMESPACE_URL, f"compass-source:{self.type}:{self.name}").hex
        if not re.fullmatch(r"src_[a-f0-9]{32}", self.id):
            raise ValueError("Invalid source identity")
        return self


class ProductConfig(BaseModel):
    """Root configuration for a Compass product workspace."""

    name: str
    description: str = ""
    sources: list[SourceConfig] = Field(default_factory=list)
    model: str = "claude-sonnet-4-20250514"

    @model_validator(mode="after")
    def unique_sources(self):
        if len({source.id for source in self.sources}) != len(self.sources) or len({source.name for source in self.sources}) != len(self.sources):
            raise ValueError("Source identities and names must be distinct within a workspace")
        return self

    def add_source(self, source: SourceConfig) -> None:
        existing = next((item for item in self.sources if item.id == source.id), None)
        if existing is None:
            existing = next((item for item in self.sources if item.name == source.name), None)
            if existing:
                source.id = existing.id  # Preserve identity for existing CLI/name-based reconnects.
        if existing:
            if any(item.name == source.name and item.id != existing.id for item in self.sources):
                raise ValueError("Another source already uses this name")
            source.legacy_names = list(dict.fromkeys([*existing.legacy_names, *source.legacy_names]))
            self.sources = [source if item.id == existing.id else item for item in self.sources]
        else:
            self.sources.append(source)


def get_compass_dir(base: Path | None = None) -> Path:
    """Get the .compass directory, creating if needed."""
    base = base or Path.cwd()
    compass_dir = base / COMPASS_DIR
    compass_dir.mkdir(parents=True, exist_ok=True)
    (compass_dir / OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    return compass_dir


def get_output_dir(base: Path | None = None) -> Path:
    return get_compass_dir(base) / OUTPUT_DIR


def load_config(base: Path | None = None) -> ProductConfig:
    """Load product config from .compass/compass.yaml."""
    config_path = get_compass_dir(base) / CONFIG_FILE
    if not config_path.exists():
        raise FileNotFoundError(
            "No Compass workspace found. Run 'compass init' first."
        )
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return ProductConfig(**data)


def save_config(config: ProductConfig, base: Path | None = None) -> Path:
    """Save product config to .compass/compass.yaml."""
    config_path = get_compass_dir(base) / CONFIG_FILE
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=config_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config.model_dump(), handle, default_flow_style=False, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, config_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return config_path


def get_api_key() -> str:
    """Get the Anthropic API key from environment."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        from dotenv import load_dotenv

        load_dotenv()
        key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Export it or add to .env file."
        )
    return key


def get_llm_provider() -> str:
    """Get the configured LLM provider name (anthropic | cloud | byok)."""
    return os.environ.get("COMPASS_LLM_PROVIDER", "anthropic").lower()
