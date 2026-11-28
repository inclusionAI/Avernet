"""Text-only inference; credentials never leave the Bot runtime.

Only standalone JSON, configured HTTPS OpenAI-compatible providers and literal
API keys are supported. Unsupported auth/dialects fail without fallback.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from engine.community.core.adapters.openclaw.default_model import (
    configured_default_model,
)
from engine.community.core.models.completion import CompletionRequest, ModelCompletion


def _route(snapshot: dict, config: dict, requested: str | None):
    # Use the gateway's current default/route, not a boot template or session.
    model = requested or configured_default_model(snapshot)
    if snapshot.get("exists") is not True or snapshot.get("valid") is not True:
        raise RuntimeError("live model configuration unavailable")
    if not model or "/" not in model:
        raise NotImplementedError("qualified model identifier required")
    provider_id, model_id = model.split("/", 1)
    parsed = snapshot.get("parsed")
    if not isinstance(parsed, dict):
        raise TypeError("live model configuration unavailable")
    provider = parsed.get("models", {}).get("providers", {}).get(provider_id)
    raw_provider = config.get("models", {}).get("providers", {}).get(provider_id)
    if not isinstance(provider, dict) or not isinstance(raw_provider, dict):
        raise NotImplementedError("configured provider required")
    # The raw file is read ONLY to obtain the secret redacted by config.get.
    # Refuse routing transformed by includes/env substitution or a racing edit.
    for field in ("baseUrl", "api", "auth", "authHeader", "headers", "models"):
        if provider.get(field) != raw_provider.get(field):
            raise NotImplementedError(
                "standalone resolved provider configuration required"
            )
    entries = provider.get("models", [])
    entry = next(
        (m for m in entries if isinstance(m, dict) and m.get("id") == model_id), None
    )
    if entry is None or entry.get("api", provider.get("api")) != "openai-completions":
        raise NotImplementedError("configured OpenAI-compatible model required")
    if (
        provider.get("auth") not in (None, "api-key")
        or provider.get("authHeader") is False
    ):
        raise NotImplementedError("API key authentication required")
    if provider.get("headers") or entry.get("headers"):
        raise NotImplementedError("custom provider headers are not supported")
    url = provider.get("baseUrl", "")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or "${" in url
    ):
        raise NotImplementedError("HTTPS provider required")
    key = raw_provider.get("apiKey")
    if (
        not isinstance(key, str)
        or not key.strip()
        or "${" in key
        or key.startswith("__OPENCLAW_")
    ):
        raise NotImplementedError("literal API key required")
    return model, model_id, url.rstrip("/"), key.strip()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result or key == "$include":
            raise NotImplementedError(
                "standalone unambiguous JSON configuration required"
            )
        result[key] = value
    return result


class _CompletionPortMixin:
    @staticmethod
    def _read_config(path: str) -> dict:
        with Path(path).open("rb") as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("model configuration exceeds limit")
        config = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(config, dict):
            raise NotImplementedError("standalone JSON model configuration required")
        return config

    async def complete_model(self, request: CompletionRequest) -> ModelCompletion:
        request = CompletionRequest.model_validate(request)
        try:
            # Bound the whole operation, not only individual socket reads.
            async with asyncio.timeout(180):
                snapshot = await self.get_model_config()
                path = snapshot.get("path")
                if not isinstance(path, str) or not Path(path).is_absolute():
                    raise RuntimeError("live configuration path unavailable")
                config = await asyncio.to_thread(self._read_config, path)
                model, model_id, base_url, key = _route(snapshot, config, request.model)
                async with (
                    httpx.AsyncClient(
                        timeout=180, trust_env=False, follow_redirects=False
                    ) as client,
                    client.stream(
                        "POST",
                        base_url + "/chat/completions",
                        headers={
                            "Authorization": key
                            if key.lower().startswith("bearer ")
                            else "Bearer " + key
                        },
                        json={
                            "model": model_id,
                            "messages": [m.model_dump() for m in request.messages],
                            "max_tokens": request.max_tokens,
                            "stream": False,
                        },
                    ) as response,
                ):
                    if response.status_code != 200:
                        raise RuntimeError("model provider refused completion")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            raise RuntimeError("model response exceeds limit")
                        chunks.append(chunk)
                payload = json.loads(b"".join(chunks))
                return ModelCompletion(
                    text=payload["choices"][0]["message"]["content"],
                    model=model,
                    tokens=payload["usage"]["total_tokens"],
                )
        except NotImplementedError:
            raise NotImplementedError(
                "model routing or authentication is not supported"
            ) from None
        except Exception:  # noqa: BLE001 — never propagate secrets/upstream bodies
            raise RuntimeError("model provider completion unavailable") from None
