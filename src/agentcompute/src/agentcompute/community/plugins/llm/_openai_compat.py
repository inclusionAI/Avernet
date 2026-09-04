"""OpenAI-compatible LLM provider (optional; stdlib-only HTTP)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any, cast

from ...spi._llm import LLMProviderPlugin

__all__ = ["OpenAICompatibleProvider"]


class OpenAICompatibleProvider(LLMProviderPlugin):
    """Chat-completions provider for any OpenAI-compatible endpoint.

    Reads ``base_url``, ``api_key``, and ``model`` from constructor args (or
    the ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` env vars). Uses only the
    stdlib so importing it never pulls a network dependency.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        retries: int = 2,
    ) -> None:
        self._base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._model = model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        self._timeout = timeout
        self._retries = retries
        if not self._base_url or not self._api_key:
            raise ValueError("OpenAICompatibleProvider requires base_url and api_key")

    def complete(self, prompt: str, **kwargs: Any) -> str:
        url = f"{self._base_url}/chat/completions"
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.get("temperature", 0.0),
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                return cast(str, data["choices"][0]["message"]["content"])
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"chat completion failed: HTTP {exc.code} {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
        raise RuntimeError(
            f"chat completion timed out after {self._retries + 1} attempt(s)"
        ) from last_exc

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        url = f"{self._base_url}/chat/completions"
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.get("temperature", 0.0),
            "stream": True,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            for line in response:
                line = line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
