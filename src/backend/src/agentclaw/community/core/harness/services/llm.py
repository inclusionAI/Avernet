"""Single-file async LLM client for Harness.

No ABC, no local/prod split — just one class. Uses the OpenAI-compatible
/v1/chat/completions endpoint format. HTTP goes through the injected
``HttpClient`` seam (the ``general`` sync client), which sofa_tracer does not
patch — so there is no SpawnProcess ``AsyncClient.send`` hook to work around.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentclaw.community.plugin_api.http_client import HttpClient
    from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = logging.getLogger(__name__)

# ── encoded fallback token ──────────────────────────────────
# Empty by default — the neutral shipped code embeds no credential. A private
# build that wants a baked-in fallback token can base64-encode one here; empty
# string means no fallback (the token comes from the SecretResolver).
_FALLBACK_TOKEN_B64: str = ""

# ── concurrency limiter ────────────────────────────────────
_MAX_CONCURRENT_LLM_CALLS = 5
_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_LLM_CALLS)

# ── retry config ───────────────────────────────────────────
_MAX_RETRIES = 3
_RETRY_DELAYS = [2.0, 5.0, 10.0]  # seconds between retries


def _decode_fallback() -> str:
    if not _FALLBACK_TOKEN_B64:
        return ""
    try:
        return base64.b64decode(_FALLBACK_TOKEN_B64).decode("utf-8")
    except Exception:
        return ""


class LLM:
    """Lightweight LLM utility for harness internal use.

    Endpoint (``base_url``), the token's secret key (``secret_name``), the HTTP
    transport (``http_client``), and the ``SecretResolver`` are all injected by
    the DI provider — this class reads no process environment. ``base_url`` /
    ``secret_name`` come from ``LLMHarnessConfig`` (the ``llm`` yaml block); the
    token is resolved through the ``SecretResolver`` by that ``secret_name``
    (corp → Mist, community → env seam), falling back to the encoded fallback
    (empty in shipped source).

    Token resolution is lazy and self-healing: the token may not be resolvable at
    construction time — e.g. the secret backend is not reachable yet in a
    SpawnProcess worker — but that does NOT latch the LLM off. ``chat()``
    re-resolves on demand, so the utility recovers once the backend becomes
    available without a process restart; only while the token is still empty does
    ``chat()`` short-circuit with the ``[llm disabled]`` sentinel.
    """

    def __init__(
        self,
        base_url: str,
        secret_name: str,
        *,
        secret_resolver: "SecretResolver",
        http_client: "HttpClient",
        model: str = "GLM-5.1",
        timeout_ms: int = 180_000,
    ):
        self._secret_resolver = secret_resolver
        self._http = http_client
        self._model = model
        self._timeout_ms = timeout_ms
        self._base_url = base_url.rstrip("/")

        # The token's secret-registry key. The token is resolved lazily from it
        # (see _resolve_token) so it can be re-fetched later — chat() retries when
        # the value is still missing.
        self._secret_name = secret_name

        # Best-effort eager resolve so the happy path logs "enabled" at init.
        self._token = self._resolve_token()

        if self._token:
            logger.info("[LLM] LLM enabled: base_url=%s, model=%s", self._base_url, self._model)
        else:
            # Token not resolvable yet. Do NOT latch off — chat() re-resolves on
            # demand and self-heals once the secret backend becomes reachable.
            logger.warning(
                "[LLM] token unresolved at init (base_url=%r) — will retry on first use",
                self._base_url,
            )

    def _resolve_token(self) -> str:
        """Resolve the API token through the injected ``SecretResolver``.

        Looked up by ``secret_name`` (the token's secret-registry key, injected
        by the DI provider); resolver errors and ``None`` results both fall
        through to the encoded fallback (empty in shipped source) — never raised —
        so a transient backend failure yields an empty token that a later call can
        retry rather than an exception. Returns ``""`` when nothing resolves."""
        try:
            secret = self._secret_resolver.get_secret(self._secret_name)
            if secret is not None:
                logger.info(
                    "[LLM] loaded token from secret store: %s", self._secret_name
                )
                return str(secret.secret_value)
        except Exception as e:
            logger.warning(
                "[LLM] secret store lookup failed for %s (%s) — falling back",
                self._secret_name,
                type(e).__name__,
            )
        return _decode_fallback()

    async def chat(self, system: str | None, user: str) -> str:
        """Send prompt and return text response (OpenAI-compatible API)."""
        if not self._token:
            # No token yet — the secret backend may have been unreachable at init.
            # Retry now (self-heal); a success is cached for later calls.
            self._token = self._resolve_token()
            if not self._token:
                logger.warning(
                    "[LLM] token unresolved, returning [llm disabled]"
                )
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
