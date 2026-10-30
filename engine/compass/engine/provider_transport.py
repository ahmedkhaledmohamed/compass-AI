"""Bounded, cancellable HTTP inference without automatic billing or route fallback."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

MAX_RESPONSE_BYTES = 2_000_000
MESSAGES = {
    "authentication": "Reconnect the selected AI account or replace its API key.",
    "permission": "This account does not have permission for the selected model or route.",
    "quota": "The selected AI account has no available credits or plan allowance.",
    "rate_limited": "The selected provider is rate limiting requests. Wait before trying again.",
    "timeout": "The AI request exceeded its deadline. No draft was accepted.",
    "cancelled": "The AI request was cancelled. Its result was not saved.",
    "unavailable": "The selected model or serving provider is unavailable. No fallback was used.",
    "invalid_request": "The selected provider rejected this request or model configuration.",
    "invalid_response": "The provider returned an invalid or incomplete response. No draft was accepted.",
    "output_limit": "The response exceeded its output limit. Select less evidence or revise the task.",
    "refused": "The selected model declined this request. Review the task and selected evidence.",
    "unsupported": "This provider does not support the bounded task workflow.",
}


class ProviderError(RuntimeError):
    def __init__(self, code: str, completion: dict | None = None):
        self.completion = completion
        self.code = code if code in MESSAGES else "invalid_response"
        super().__init__(MESSAGES[self.code])


@dataclass(frozen=True)
class RequestControl:
    timeout_seconds: float = 120
    cancelled: Callable[[], bool] = field(default=lambda: False, repr=False)


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None

    def metadata(self) -> dict:
        return {
            "model": self.model,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def error_for_status(status: int) -> ProviderError:
    return ProviderError(
        {
            401: "authentication",
            402: "quota",
            403: "permission",
            408: "timeout",
            429: "rate_limited",
            404: "unavailable",
        }.get(status, "unavailable" if status >= 500 else "invalid_request")
    )


def response_error(error) -> ProviderError:
    code = error.get("code", error.get("type")) if isinstance(error, dict) else None
    if isinstance(code, int) and not isinstance(code, bool):
        return error_for_status(code)
    return ProviderError(
        {
            "subscription_sharing_usage_limit_exceeded": "quota",
            "subscription_sharing_usage_unavailable": "quota",
            "insufficient_quota": "quota",
            "rate_limit_exceeded": "rate_limited",
            "invalid_api_key": "authentication",
            "authentication_error": "authentication",
            "permission_error": "permission",
            "overloaded_error": "unavailable",
            "subscription_sharing_unsupported_capability": "invalid_request",
        }.get(code, "invalid_response")
    )


def token_count(usage: dict, name: str) -> int | None:
    count = usage.get(name)
    if count is None:
        return None  # Missing usage is unknown, never zero-cost inference.
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ProviderError("invalid_response")
    return count


class HTTPInference:
    """Fixed provider origins, one attempt, total deadline and closed-on-cancel I/O."""

    def __init__(
        self, provider: str, credential: str, serving_provider: str = "", *, transport=None
    ):
        if provider not in ("anthropic", "openrouter", "huggingface", "chatgpt"):
            raise ProviderError("unsupported")
        if not credential or "\n" in credential or "\r" in credential:
            raise ProviderError("authentication")
        if provider in ("openrouter", "huggingface") and (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}", serving_provider)
            or serving_provider.lower() in ("auto", "fastest", "cheapest", "preferred")
        ):
            raise ProviderError("invalid_request")
        self.provider = provider
        self._credential = credential
        self.serving_provider = serving_provider
        self._transport = transport

    def complete(self, prompt, system, model, max_tokens, control: RequestControl) -> Completion:
        if not model or len(model) > 256 or model.lower() in ("auto", "openrouter/auto"):
            raise ProviderError("invalid_request")
        if not 0 < control.timeout_seconds <= 300:
            raise ProviderError("timeout")
        if control.cancelled():
            raise ProviderError("cancelled")
        try:
            return asyncio.run(self._bounded(prompt, system, model, max_tokens, control))
        except ProviderError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("unavailable") from exc
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ProviderError("invalid_response") from exc

    async def _bounded(self, prompt, system, model, max_tokens, control):
        async def watch_cancel():
            while True:
                if control.cancelled():
                    raise ProviderError("cancelled")
                await asyncio.sleep(0.05)

        async with asyncio.timeout(control.timeout_seconds):
            request = asyncio.create_task(self._request(prompt, system, model, max_tokens, control))
            watcher = asyncio.create_task(watch_cancel())
            try:
                done, _ = await asyncio.wait(
                    (request, watcher), return_when=asyncio.FIRST_COMPLETED
                )
                if watcher in done:
                    return watcher.result()
                result = request.result()
                if control.cancelled():
                    raise ProviderError("cancelled")
                return result
            finally:
                for task in (request, watcher):
                    task.cancel()
                await asyncio.gather(request, watcher, return_exceptions=True)

    def _request_data(self, prompt, system, model, max_tokens):
        headers = {"Content-Type": "application/json"}
        if self.provider == "anthropic":
            headers.update({"x-api-key": self._credential, "anthropic-version": "2023-06-01"})
            return (
                "https://api.anthropic.com/v1/messages",
                headers,
                {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        headers["Authorization"] = f"Bearer {self._credential}"
        if self.provider == "chatgpt":
            return (
                "https://api.openai.com/v1/responses",
                headers,
                {
                    "model": model,
                    "instructions": system,
                    "store": False,
                    "stream": True,
                    "input": [
                        {"role": "user", "content": [{"type": "input_text", "text": prompt}]}
                    ],
                },
            )
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if self.provider == "openrouter":
            body["provider"] = {
                "only": [self.serving_provider],
                "allow_fallbacks": False,
                "data_collection": "deny",
                "require_parameters": True,
            }
            return "https://openrouter.ai/api/v1/chat/completions", headers, body
        # HF's explicit suffix selects a serving provider; no :auto routing.
        if ":" in model:
            raise ProviderError("invalid_request")
        body["model"] = f"{model}:{self.serving_provider}"
        return "https://router.huggingface.co/v1/chat/completions", headers, body

    async def _request(self, prompt, system, model, max_tokens, control):
        url, headers, body = self._request_data(prompt, system, model, max_tokens)
        async with (
            httpx.AsyncClient(
                transport=self._transport,
                timeout=control.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream("POST", url, headers=headers, json=body) as response,
        ):
            if not 200 <= response.status_code < 300:
                # Some plan-allowance errors use HTTP 429. Inspect only a bounded
                # machine code, never retain the upstream diagnostic message.
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16_384:
                        break
                try:
                    error = json.loads(raw).get("error", {})
                    if isinstance(error, dict) and error.get("code") in (
                        "subscription_sharing_usage_limit_exceeded",
                        "subscription_sharing_usage_unavailable",
                        "insufficient_quota",
                        "invalid_api_key",
                        "rate_limit_exceeded",
                    ):
                        raise response_error(error)
                except (ValueError, TypeError, AttributeError):
                    pass
                raise error_for_status(response.status_code)
            if self.provider == "chatgpt":
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise ProviderError("invalid_response")
                return await self._responses(response)
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_RESPONSE_BYTES:
                    raise ProviderError("output_limit")
            data = json.loads(chunks)
            if not isinstance(data, dict):
                raise ProviderError("invalid_response")
            if data.get("error"):
                raise response_error(data["error"])
            return self._completion(data)

    def _completion(self, data):
        model = data.get("model")
        if not isinstance(model, str) or not model or len(model) > 256:
            raise ProviderError("invalid_response")
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            raise ProviderError("invalid_response")
        provider = (
            self.provider
            if not self.serving_provider
            else f"{self.provider}:{self.serving_provider}"
        )
        input_key, output_key = (
            ("prompt_tokens", "completion_tokens")
            if self.provider in ("openrouter", "huggingface")
            else ("input_tokens", "output_tokens")
        )
        metadata = Completion(
            "", model, provider, token_count(usage, input_key), token_count(usage, output_key)
        ).metadata()
        try:
            if self.provider == "anthropic":
                if data.get("stop_reason") == "max_tokens":
                    raise ProviderError("output_limit")
                if data.get("stop_reason") not in ("end_turn", "stop_sequence"):
                    raise ProviderError(
                        "refused" if data.get("stop_reason") == "refusal" else "invalid_response"
                    )
                blocks = data["content"]
                if not isinstance(blocks, list) or any(
                    block.get("type") != "text" for block in blocks
                ):
                    raise ProviderError("invalid_response")
                text = "".join(block["text"] for block in blocks)
                input_key, output_key = "input_tokens", "output_tokens"
            elif self.provider == "chatgpt":
                if data.get("status") != "completed":
                    raise ProviderError("invalid_response")
                blocks = [
                    block for message in data["output"] for block in message.get("content", [])
                ]
                if any(block.get("type") == "refusal" for block in blocks):
                    raise ProviderError("refused")
                text = "".join(
                    block["text"] for block in blocks if block.get("type") == "output_text"
                )
                input_key, output_key = "input_tokens", "output_tokens"
            else:
                choices = data["choices"]
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ProviderError("invalid_response")
                choice = choices[0]
                if choice.get("finish_reason") == "length":
                    raise ProviderError("output_limit")
                if choice.get("finish_reason") == "content_filter" or choice["message"].get(
                    "refusal"
                ):
                    raise ProviderError("refused")
                if choice.get("finish_reason") != "stop" or choice["message"].get("tool_calls"):
                    raise ProviderError("invalid_response")
                text = choice["message"]["content"]
                input_key, output_key = "prompt_tokens", "completion_tokens"
            if not isinstance(text, str) or not text.strip():
                raise ProviderError("invalid_response")
            return Completion(
                text, model, provider, token_count(usage, input_key), token_count(usage, output_key)
            )
        except ProviderError as exc:
            exc.completion = metadata
            raise

    async def _responses(self, response):
        buffer = b""
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ProviderError("output_limit")
            buffer = (buffer + chunk).replace(b"\r\n", b"\n")
            while b"\n\n" in buffer:
                frame, buffer = buffer.split(b"\n\n", 1)
                data = b"\n".join(
                    line[5:].lstrip(b" ")
                    for line in frame.splitlines()
                    if line.startswith(b"data:")
                )
                if not data or data == b"[DONE]":
                    continue
                event = json.loads(data)
                kind = event.get("type")
                if kind == "response.completed":
                    return self._completion(event["response"])
                if kind in ("response.failed", "error"):
                    raise response_error(
                        event.get("error") or event.get("response", {}).get("error")
                    )
                if kind == "response.incomplete":
                    raise ProviderError("output_limit")
        raise ProviderError("invalid_response")
