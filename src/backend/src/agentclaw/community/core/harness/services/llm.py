"""Single-file async LLM client for Harness.

No ABC, no local/prod split — just one class. Disabled when env vars are missing.
Uses OpenAI-compatible /v1/chat/completions endpoint format.
Bypasses sofa_tracer monkey-patch to avoid "Error in httpx send hook" in SpawnProcess.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentclaw.community.plugin_api.secret_resolver import SecretResolver

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ── encoded fallback token ──────────────────────────────────
# Empty by default — the neutral shipped code embeds no credential. A deployment
# that wants a baked-in fallback token can set the LLM_AUTH_TOKEN env var (or, for
# a private build, base64-encode a token here). Empty string means no fallback.
_FALLBACK_TOKEN_B64: str = ""

# ── bypass sofa_tracer monkey-patch ────────────────────────
# sofa_tracer patches httpx.AsyncClient.send globally, which fails in
# SpawnProcess contexts with "Error in httpx send hook". We save the
# original send before any patches are applied and use it directly.
_ORIGINAL_ASYNC_SEND = None
if httpx is not None:
    _ORIGINAL_ASYNC_SEND = httpx.AsyncClient.send
    try:
        from sofa_tracer.client_hooks.httpx import (
            _HTTPX_AsyncClient_send as _tracer_original_send,
        )
        _ORIGINAL_ASYNC_SEND = _tracer_original_send
    except ImportError:
        pass

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

    Token sources (highest priority first): explicit constructor param →
    injected ``SecretResolver`` → ``LLM_AUTH_TOKEN`` env var → encoded fallback
    (empty in shipped source). The neutral shipped code embeds no endpoint /
    secret name / token.

    Token resolution is lazy and self-healing: a missing endpoint (or absent
    ``httpx``) disables the LLM permanently, but a token that cannot be resolved
    yet — e.g. the secret backend is not reachable at construction time — does
    NOT latch the LLM off. ``chat()`` re-resolves on demand, so the utility
    recovers once the backend becomes available without a process restart.

    Env vars:
        LLM_BASE_URL — API base URL
        LLM_AUTH_TOKEN — fallback API key
        LLM_MODEL      — default model, e.g. GLM-5.1
        LLM_TIMEOUT_MS — request timeout in ms, default 60000
        LLM_SECRET_NAME — secret-store name for the API key
    """

    def __init__(
        self,
        base_url: str | None = None,
        auth_token: str | None = None,
        model: str | None = None,
        timeout_ms: int | None = None,
        secret_name: str | None = None,
        *,
        secret_resolver: "SecretResolver",
    ):
        self._secret_resolver = secret_resolver
        self._model = model or os.getenv("LLM_MODEL", "GLM-5.1")
        self._timeout_ms = timeout_ms or int(os.getenv("LLM_TIMEOUT_MS", "180000"))
        self._base_url = (base_url or os.getenv("LLM_BASE_URL", "")).rstrip("/")

        # Resolution inputs kept on the instance so the token can be re-resolved
        # later (see chat()). Priority is explicit arg > secret store > env var >
        # encoded fallback; _resolve_token() applies it.
        self._explicit_token = auth_token or ""
        self._secret_name = secret_name or os.getenv("LLM_SECRET_NAME", "")

        # Permanent, config-level disable — no endpoint or no httpx means the
        # feature is off and there is nothing to recover to. A *missing token* is
        # NOT part of this: the secret backend may simply be unreachable right now
        # (e.g. layotto not yet ready in a SpawnProcess worker), which is
        # recoverable and must not latch the LLM off for the worker's lifetime.
        self._config_disabled = httpx is None or not self._base_url

        # Best-effort eager resolve so the happy path logs "enabled" at init.
        self._token = self._resolve_token()

        if self._config_disabled:
            logger.warning(
                "[LLM] LLM is DISABLED (feature-off): base_url=%r, httpx=%s",
                self._base_url,
                "ok" if httpx is not None else "MISSING",
            )
        elif self._token:
            logger.info("[LLM] LLM enabled: base_url=%s, model=%s", self._base_url, self._model)
        else:
            # Endpoint is configured but the token is not resolvable yet. Do not
            # latch disabled — chat() re-resolves on demand and self-heals once the
            # secret backend becomes reachable.
            logger.warning(
                "[LLM] token unresolved at init (base_url=%r) — will retry on first use",
                self._base_url,
            )

    def _resolve_token(self) -> str:
        """Resolve the API token from the first available source.

        Priority: explicit constructor arg > injected ``SecretResolver`` (when a
        base_url and secret name are configured) > ``LLM_AUTH_TOKEN`` env var >
        encoded fallback (empty in shipped source). Resolver errors and ``None``
        results both fall through to the env/fallback tail — never raised — so a
        transient backend failure yields an empty token that a later call can
        retry rather than an exception. Returns ``""`` when nothing resolves."""
        if self._explicit_token:
            return self._explicit_token
        if self._base_url and self._secret_name:
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
        return os.getenv("LLM_AUTH_TOKEN", "") or _decode_fallback()

    @property
    def _disabled(self) -> bool:
        """Whether ``chat()`` will short-circuit *right now*.

        Reflects current resolvability (config-off, or token not yet resolved),
        not a latched snapshot — a token that becomes resolvable flips this back
        to ``False`` on the next check."""
        return self._config_disabled or not self._token

    async def chat(self, system: str | None, user: str) -> str:
        """Send prompt and return text response (OpenAI-compatible API)."""
        if self._config_disabled:
            logger.warning("[LLM] chat() called but LLM is disabled, returning [llm disabled]")
            return "[llm disabled]"

        if not self._token:
            # Endpoint is configured but no token yet — the secret backend may have
            # been unreachable at init. Retry now (self-heal); a success is cached.
            self._token = self._resolve_token()
            if not self._token:
                logger.warning(
                    "[LLM] token still unresolved, returning [llm disabled]"
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

    async def _do_request(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
        use_original_send: bool = False,
    ) -> str:
        """Execute the HTTP request, bypassing sofa_tracer when possible."""
        timeout = httpx.Timeout(self._timeout_ms / 1000.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            url = f"{self._base_url}/v1/chat/completions"
            # Always prefer original send to bypass sofa_tracer in SpawnProcess
            if _ORIGINAL_ASYNC_SEND is not None:
                request = client.build_request("POST", url, json=body, headers=headers)
                resp = await _ORIGINAL_ASYNC_SEND(client, request)
            else:
                resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")

        return ""
