"""Product workspace configuration."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

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

    type: str  # code, docs, data, interviews, support
    name: str
    path: str | None = None
    url: str | None = None
    options: dict = Field(default_factory=dict)
    auth: AuthConfig | None = None


class ProductConfig(BaseModel):
    """Root configuration for a Compass product workspace."""

    name: str
    description: str = ""
    sources: list[SourceConfig] = Field(default_factory=list)
    model: str = "claude-sonnet-4-20250514"

    def add_source(self, source: SourceConfig) -> None:
        existing = [s for s in self.sources if s.name == source.name]
        if existing:
            self.sources = [s if s.name != source.name else source for s in self.sources]
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
    with open(config_path, "w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, sort_keys=False)
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
