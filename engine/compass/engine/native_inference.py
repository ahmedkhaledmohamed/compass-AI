"""Task-scoped native inference; no provider credential enters the engine."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr, field_validator

from compass.engine.provider_transport import (
    Completion,
    HTTPInference,
    ProviderError,
    RequestControl,
    token_count,
)
from compass.models.tasks import ConnectionBinding, StrictModel


class NativeGrant(StrictModel):
    url: str
    token: SecretStr
    connection: ConnectionBinding

    @field_validator("url")
    @classmethod
    def local_only(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.username or parsed.password or parsed.path != "/complete"
                or parsed.query or parsed.fragment):
            raise ValueError("Native inference must use its private loopback endpoint")
        return value

    @field_validator("token")
    @classmethod
    def capability(cls, value):
        if not re.fullmatch(r"[a-f0-9]{64}", value.get_secret_value()):
            raise ValueError("Invalid native inference grant")
        return value


class NativeInference(HTTPInference):
    def __init__(self, grant: NativeGrant, binding: dict, *, transport=None):
        self.grant = grant
        self.binding = binding
        self.connection = grant.connection.model_dump()
        self._transport = transport
        self.provider = grant.connection.provider
        self.serving_provider = grant.connection.serving_provider

    def complete_controlled(self, prompt, system, model, max_tokens, control):
        if model != self.grant.connection.model:
            raise ProviderError("permission")
        return super().complete(prompt, system, model, max_tokens, control)

    def complete(self, prompt, system="", model="", max_tokens=4096):
        result = self.complete_controlled(prompt, system, model, max_tokens, RequestControl())
        return result.text, result.input_tokens, result.output_tokens

    async def _request(self, prompt, system, model, max_tokens, control):
        async with (
            httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(control.timeout_seconds)) as client,
            client.stream("POST", self.grant.url,
                                     headers={"Authorization": "Bearer " + self.grant.token.get_secret_value()},
                                     json={"binding": self.binding, "prompt": prompt, "system": system,
                                           "max_tokens": max_tokens, "timeout_seconds": control.timeout_seconds}) as response,
        ):
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 2_100_000:
                    raise ProviderError("output_limit")
            import json
            result = json.loads(data)
            if response.status_code != 200:
                raise ProviderError(result.get("code", "invalid_response"), result.get("completion"))
            if (not isinstance(result.get("text"), str) or not result["text"].strip()
                    or not isinstance(result.get("model"), str) or not result["model"] or len(result["model"]) > 256
                    or result.get("provider") != self.provider + (":" + self.serving_provider if self.serving_provider else "")):
                raise ProviderError("invalid_response")
            return Completion(result["text"], result["model"], result["provider"],
                              token_count(result, "input_tokens"), token_count(result, "output_tokens"))


def connection_binding(provider):
    return provider.connection if isinstance(provider, NativeInference) else None
