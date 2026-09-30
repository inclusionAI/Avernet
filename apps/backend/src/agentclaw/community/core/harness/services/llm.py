"""Single-file async LLM client for Harness.

No ABC, no local/prod split — just one class. Uses the OpenAI-compatible
/v1/chat/completions endpoint format. HTTP goes through the injected
``HttpClient`` seam (the ``general`` sync client), which sofa_tracer does not
patch — so there is no SpawnProcess ``AsyncClient.send`` hook to work around.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx

from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = logging.getLogger(__name__)

# ── concurrency limiter ────────────────────────────────────
_MAX_CONCURRENT_LLM_CALLS = 10
_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_LLM_CALLS)

# ── retry config ───────────────────────────────────────────
_MAX_RETRIES = 5
_RETRY_DELAYS = [2.0, 5.0, 10.0]  # seconds between retries
# Fraction of each delay added as random jitter (0.0–1.0). Diagnostics and patch
# calls run up to ``_MAX_CONCURRENT_LLM_CALLS`` in parallel; without jitter a
# gateway blip makes every in-flight call retry on the same wall-clock tick and
# the synchronized retry storm re-trips the gateway. Jitter spreads the retries
# so recovery is staggered instead of herd-like.
_RETRY_JITTER = 0.4

# ── generation budget ──────────────────────────────────────
# GLM-5.1 stalls on a huge max_tokens: the old 256_000 pushed it into a slow
# reasoning path that never returned within antchat's ~90s gateway window, so the
# gateway closed the connection → httpx.ReadError (re-sent 3×, ~5min/template).
# 8k covers every harness prompt — short diagnostic analyses AND full-file patch
# rewrites (largest file ~7.7kB ≈ 2.5k tokens) — with headroom. On a
# read-timeout / broken-connection retry we shrink further so the model can finish
# within the window instead of verbatim-repeating the same heavy request.
_DEFAULT_MAX_TOKENS = 32768
# Diagnostics emit a short summary + a patchable draft (mapping table + a few
# MCP call specs) + ``[SCORE:xx]`` — far smaller than a full-file patch
# rewrite. 8k covers that with headroom while keeping GLM out of the
# slow-reasoning path that #307 lowered the old 256k to escape: a 32k budget
# lets the model stall past antchat's ~90s gateway window even when the prompt
# is small. (The matching *input*-side guard lives in mcp_format.py, which
# renders each MCP as a compact text block instead of dumping the nested
# inputSchema JSON.) 8k still leaves the trailing ``[SCORE:xx]`` room, since
# the draft body is only a few KB.
DIAGNOSTIC_MAX_TOKENS = 8192
# Connection-level failures (gateway dropped mid send/read, or the send-hook
# wrapper re-raised) get more retries than before — these are transient blips,
# not "request too heavy" stalls, so giving up after one retry was premature.
# Must stay < ``_MAX_RETRIES`` so the light-retry budget is exhausted via the
# explicit break rather than the for-loop falling through.
_TIMEOUT_MAX_RETRIES = 2
# Light-retry budget: 8k covers full-file patch rewrites (largest file ~2.5k
# tokens) with headroom, so a retried *patch* call can still complete instead of
# being truncated to a useless stub — while staying well under the 32k default
# that pushes GLM into the slow path that exceeds the gateway window.
_TIMEOUT_RETRY_MAX_TOKENS = 8192

# Exceptions where the request never got a usable response back: the gateway
# closed the connection (httpx.ReadError / RemoteProtocolError) or the
# read/connect/pool timed out. These call for a lighter retry, not a verbatim
# repeat of the same heavy body.
_TIMEOUT_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.TransportError,
)


def _retry_delay(attempt: int) -> float:
    """Return the (jittered) backoff delay before retry attempt ``attempt``.

    ``attempt`` is the 0-indexed attempt that just failed. The base delay
    escalates with ``_RETRY_DELAYS`` so the gateway gets progressively more
    time to recover, then up to ``_RETRY_JITTER`` of it is added as random
    jitter so parallel retries don't all land on the same wall-clock tick.
    """
    base = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
    jitter = random.uniform(0.0, base * _RETRY_JITTER)
    return base + jitter


def _client_error_status(exc: BaseException) -> int | None:
    """Return a 4xx status carried on ``exc.response`` if present, else None.

    The general ``HttpClient`` runs under a send-hook wrapper (outside this
    repo) that re-wraps low-level httpx transport failures into an exception
    type we can't subclass-match. So we classify connection-level failures by
    *symptom* — does the exception carry a response object? — not by type:

    - A 4xx (auth/usage) response almost always has ``response`` set: retrying
      verbatim is pointless, the caller must not retry.
    - A connection dropped during send/read never has ``response``: that is a
      connection-level failure the caller should retry *lightly* with a
      shrunken body.

    Returns the 4xx status code, or ``None`` (treat as connection-level).
    """
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if isinstance(status, int) and 400 <= status < 500:
        return status
    return None


def _exc_detail(exc: BaseException) -> str:
    """Format ``exc`` with its underlying cause for diagnostics.

    The deployed ``general`` client runs under a send-hook wrapper (outside this
    repo) that re-wraps the real httpx transport error into an opaque
    ``HttpxCallingException('Error in httpx send hook')``. The wrapped error
    survives on ``__cause__`` / ``__context__``; the bare ``%r`` of the wrapper
    hides whether the underlying failure was a ``ReadError``,
    ``RemoteProtocolError``, ``ConnectTimeout``, etc. — so we surface the cause
    chain (and any attached request URL) here to make retry failures actionable.
    """
    parts: list[str] = [f"{type(exc).__name__}: {exc}"]
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        parts.append(f"caused by {type(cause).__name__}: {cause}")
    req = getattr(exc, "request", None)
    if req is not None:
        url = getattr(req, "url", None)
        if url is not None:
            parts.append(f"request={url}")
    return " | ".join(parts)


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

    async def chat(
        self,
        system: str | None,
        user: str,
        *,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> str:
        """Send prompt and return text response (OpenAI-compatible API).

        ``max_tokens`` defaults to a bounded budget (``_DEFAULT_MAX_TOKENS``):
        GLM-5.1 stalls on the old 256k cap and never returns within antchat's
        gateway window."""
        if not self._token:
            logger.warning("[LLM] chat() called but no token resolved, returning [llm disabled]")
            return "[llm disabled]"

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

        logger.info(
            "[LLM] POST %s/v1/chat/completions model=%s max_tokens=%d",
            self._base_url, self._model, max_tokens,
        )

        async with _SEMAPHORE:
            return await self._request_with_retry(
                messages=messages, headers=headers, max_tokens=max_tokens,
            )

    async def _request_with_retry(
        self,
        *,
        messages: list[dict[str, str]],
        headers: dict[str, str],
        max_tokens: int,
    ) -> str:
        """Execute request with classified retry.

        - 5xx → retry up to ``_MAX_RETRIES`` (transient server fault).
        - 4xx → do not retry (client/auth error; retrying won't help).
        - read timeout / broken connection (``_TIMEOUT_EXCEPTIONS`` or any
          exception without a 4xx response, e.g. the gateway-side send-hook
          wrapper) → retry at most ``_TIMEOUT_MAX_RETRIES`` with a shrunk
          ``max_tokens``: the gateway closed at ~90s because the request was
          too heavy, so a verbatim repeat just wastes another window.

        Connection-level failures are detected by *symptom*, not by exception
        type: the deployed ``general`` client has a send-hook wrapper (outside
        this repo) that re-wraps httpx transport errors into a type we cannot
        subclass-match, so we treat "no 4xx response attached" as
        connection-level and route it through the light retry (see
        :func:`_client_error_status`).

        Every retry sleeps a backoff from ``_retry_delay`` — escalating base
        delay plus random jitter — so a gateway blip that knocks out several
        in-flight parallel calls doesn't turn into a synchronized retry storm
        that re-trips the gateway on the same tick.

        Exhaustion returns the ``[llm disabled]`` sentinel so callers (parser /
        PatchPlanner) treat it uniformly as "LLM unavailable"."""
        budget = max_tokens
        started = time.monotonic()
        for attempt in range(_MAX_RETRIES):
            body: dict[str, Any] = {
                "model": self._model,
                "max_tokens": budget,
                "messages": messages,
            }
            try:
                return await self._do_request(body, headers)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if 500 <= status < 600 and attempt < _MAX_RETRIES - 1:
                    delay = _retry_delay(attempt)
                    logger.warning(
                        "[LLM] HTTP %d (attempt %d/%d), retrying in %.1fs",
                        status, attempt + 1, _MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("[LLM] HTTP %d, not retrying: %r", status, e)
                break
            except _TIMEOUT_EXCEPTIONS as e:
                if attempt < _TIMEOUT_MAX_RETRIES:
                    budget = _TIMEOUT_RETRY_MAX_TOKENS
                    delay = _retry_delay(attempt)
                    logger.warning(
                        "[LLM] %s (attempt %d/%d), shrinking max_tokens=%d, retrying in %.1fs: %s",
                        type(e).__name__, attempt + 1, _MAX_RETRIES, budget, delay,
                        _exc_detail(e),
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "[LLM] %s after timeout retries (max_tokens=%d, elapsed %.1fs, base_url=%s): %s",
                    type(e).__name__, budget, time.monotonic() - started,
                    self._base_url, _exc_detail(e),
                )
                break
            except Exception as e:
                # 4xx carried through a non-HTTPStatusError wrapper → client
                # error, retrying verbatim is pointless.
                client_status = _client_error_status(e)
                if client_status is not None:
                    logger.error(
                        "[LLM] %s (HTTP %d), not retrying: %r",
                        type(e).__name__, client_status, e,
                    )
                    break
                # No response attached → connection-level failure (gateway
                # dropped the connection, or the send-hook wrapper re-raised a
                # transport error). Route to the same light retry as timeouts,
                # bounded by ``_TIMEOUT_MAX_RETRIES`` instead of verbatim-retry.
                if attempt < _TIMEOUT_MAX_RETRIES:
                    budget = _TIMEOUT_RETRY_MAX_TOKENS
                    delay = _retry_delay(attempt)
                    logger.warning(
                        "[LLM] %s (attempt %d/%d), shrinking max_tokens=%d, retrying in %.1fs: %s",
                        type(e).__name__, attempt + 1, _MAX_RETRIES, budget, delay,
                        _exc_detail(e),
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "[LLM] %s after timeout retries (max_tokens=%d, elapsed %.1fs, base_url=%s): %s",
                    type(e).__name__, budget, time.monotonic() - started,
                    self._base_url, _exc_detail(e),
                )
                break
        return "[llm disabled]"

    async def _do_request(self, body: dict[str, Any], headers: dict[str, str]) -> str:
        """Execute the HTTP request via the injected sync ``HttpClient``.

        Uses ``stream=True`` to keep long outputs inside antchat's window: a
        non-streaming call is capped at ~90s by the spanner gateway's read
        timeout (``RemoteProtocolError`` mid-generation for any output that
        takes longer), while streaming extends the budget to ~1800s as long as
        the first token and inter-token gaps stay under 90s. SSE ``data:`` lines
        are accumulated into the full content string; the external ``chat()``
        contract (returns str) is unchanged.

        The client is synchronous (``httpx.Client``, which sofa_tracer does not
        patch); the stream read runs off the event loop via ``asyncio.to_thread``."""
        url = f"{self._base_url}/v1/chat/completions"
        stream_body = {**body, "stream": True}
        try:
            content = await asyncio.to_thread(
                self._stream_read, url, stream_body, headers,
            )
        except httpx.HTTPStatusError:
            # raise_for_status() ran inside the stream context (unlike the old
            # post path where it ran outside this try). Pass HTTPStatusError
            # straight to the retry layer's status-based branch (4xx no-retry /
            # 5xx retry) — do NOT route it through _exc_detail, which may touch
            # ``.request`` on a wrapper-shaped error and mask the status code.
            raise
        except Exception as e:
            # The send-hook wrapper raises an opaque exception whose real cause
            # (the underlying httpx transport error) is on __cause__/__context__.
            # Log the full chain + URL here so the retry layer's type-only
            # warning is paired with the actionable underlying failure.
            logger.warning(
                "[LLM] POST %s raised (max_tokens=%d): %s",
                url, body.get("max_tokens"), _exc_detail(e),
            )
            raise
        return content

    def _stream_read(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> str:
        """Synchronous SSE stream reader (runs off the event loop via to_thread).

        Reads ``data:`` lines from the streaming chat completion, accumulating
        ``delta.content`` into the full text. ``[DONE]`` or end-of-stream stops
        reading. Any transport error (gateway ``RemoteProtocolError`` on a
        >90s gap, read timeout, etc.) propagates so the retry layer can shrink
        ``max_tokens`` and retry.
        """
        chunks: list[str] = []
        with self._http.stream(
            "POST", url, json=body, headers=headers,
            timeout=self._timeout_ms / 1000.0,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    continue
                try:
                    delta = obj["choices"][0]["delta"].get("content", "")
                except (KeyError, IndexError, TypeError):
                    continue
                if delta:
                    chunks.append(delta)
        return "".join(chunks)
