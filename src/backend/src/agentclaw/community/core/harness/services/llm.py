"""Single-file async LLM client for Harness.

No ABC, no local/prod split — just one class. Uses the OpenAI-compatible
/v1/chat/completions endpoint format. HTTP goes through the injected
``HttpClient`` seam (the ``general`` sync client), which sofa_tracer does not
patch — so there is no SpawnProcess ``AsyncClient.send`` hook to work around.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = logging.getLogger(__name__)

# ── concurrency limiter ────────────────────────────────────
_MAX_CONCURRENT_LLM_CALLS = 5
_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_LLM_CALLS)

# ── retry config ───────────────────────────────────────────
_MAX_RETRIES = 3
_RETRY_DELAYS = [2.0, 5.0, 10.0]  # seconds between retries


class LLM:
    """Lightweight LLM utility for harness internal use.

    Endpoint (``base_url``), the token's secret key (``secret_name``), the HTTP
    transport (``http_client``), and the ``SecretResolver`` are all injected by
    the DI provider — this class reads no process environment and bakes in no
    credential. ``base_url`` / ``secret_name`` come from ``LLMHarnessConfig`` (the
    ``llm`` yaml block); the token is resolved once, at construction, through the
    ``SecretResolver`` by that ``secret_name`` (corp → Mist, community → env seam).

    The provider binds this as a lazy ``@singleton``, so construction happens on
    first use — well after boot, when the ``SecretResolver`` is ready — not during
    early startup. If the secret resolves, ``chat()`` sends; if it does not
    (``token is None``), ``chat()`` short-circuits with the ``[llm disabled]``
    sentinel. There is no re-resolution and no baked fallback: the injected
    resolver behaves the same on every call, so retrying can only repeat the same
    answer.
    """

    def __init__(
        self,
        base_url: str,
        secret_name: str,
        *,
        secret_resolver: SecretResolver,
        http_client: HttpClient,
        model: str = "GLM-5.1",
        timeout_ms: int = 180_000,
    ):
        self._secret_resolver = secret_resolver
        self._http = http_client
        self._model = model
        self._timeout_ms = timeout_ms
        self._base_url = base_url.rstrip("/")
        self._secret_name = secret_name

        # Resolve the token once, here. None ⇒ the LLM is disabled (chat() returns
        # the sentinel); there is no fallback and no later retry.
        self._token = self._resolve_token()

        if self._token:
            logger.info("[LLM] LLM enabled: base_url=%s, model=%s", self._base_url, self._model)
        else:
            logger.warning(
                "[LLM] LLM disabled: no token resolved for secret %r (base_url=%r)",
                self._secret_name,
                self._base_url,
            )

    def _resolve_token(self) -> str | None:
        """Resolve the API token through the injected ``SecretResolver``.

        Looked up by ``secret_name`` (the token's secret-registry key, injected by
        the DI provider). Returns the token string, or ``None`` when the secret is
        absent or the lookup raises — no baked fallback (a committed credential is
        never shipped), so an unresolved token simply disables the LLM."""
        try:
            secret = self._secret_resolver.get_secret(self._secret_name)
            if secret is not None:
                logger.info(
                    "[LLM] loaded token from secret store: %s", self._secret_name
                )
                return str(secret.secret_value)
        except Exception as e:
            logger.warning(
                "[LLM] secret store lookup failed for %s (%s)",
                self._secret_name,
                type(e).__name__,
            )
        return None

    async def chat(self, system: str | None, user: str) -> str:
        """Send prompt and return text response (OpenAI-compatible API)."""
        if not self._token:
            logger.warning("[LLM] chat() called but no token resolved, returning [llm disabled]")
            return "[llm disabled]"

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 256000,
            "messages": messages,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

        logger.info("[LLM] POST %s/v1/chat/completions model=%s", self._base_url, self._model)

        async with _SEMAPHORE:
            return await self._request_with_retry(body, headers)

    async def _request_with_retry(self, body: dict[str, Any], headers: dict[str, str]) -> str:
        """Execute request with retry — any failure triggers up to 3 retries."""
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return await self._do_request(body, headers)
            except Exception as e:
                last_err = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                label = f"HTTP {status}" if status else type(e).__name__

                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "[LLM] %s (attempt %d/%d), retrying in %.1fs",
                        label, attempt + 1, _MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "[LLM] %s after %d retries: %s",
                        label, _MAX_RETRIES, e,
                    )
        logger.error("[LLM] all retries exhausted: %s", last_err)
        return "[llm disabled]"

    async def _do_request(self, body: dict[str, Any], headers: dict[str, str]) -> str:
        """Execute the HTTP request via the injected sync ``HttpClient``.

        The client is synchronous (``httpx.Client``, which sofa_tracer does not
        patch); run it off the event loop via ``asyncio.to_thread`` so the
        (potentially long) call does not block. The ``general`` client has an
        empty base_url, so we pass the full absolute URL."""
        url = f"{self._base_url}/v1/chat/completions"
        resp = await asyncio.to_thread(
            self._http.post,
            url,
            json=body,
            headers=headers,
            timeout=self._timeout_ms / 1000.0,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")

        return ""
