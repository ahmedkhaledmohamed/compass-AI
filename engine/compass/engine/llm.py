"""LLM client wrapper for Compass reasoning.

Thin delegation layer — all calls route through the Orchestrator.
Preserves the existing ask()/ask_json() signatures so downstream code is unchanged.
"""

from __future__ import annotations

from collections.abc import Generator

from compass.engine.orchestrator import get_orchestrator


def ask(
    prompt: str,
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
) -> str:
    return get_orchestrator().ask(prompt, system, model, max_tokens)


def ask_json(
    prompt: str,
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
) -> dict | list:
    return get_orchestrator().ask_json(prompt, system, model, max_tokens)


def ask_stream(
    prompt: str,
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
) -> Generator[str, None, None]:
    return get_orchestrator().ask_stream(prompt, system, model, max_tokens)
