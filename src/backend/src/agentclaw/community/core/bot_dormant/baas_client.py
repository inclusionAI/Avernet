"""BaasDormantClient — BaaS health-checker HTTP client for dormant bot recycling.

Wraps one endpoint on the BaaS bot-health-checker service:

  - GET /api/v1/bot-health-checker/alive

check_alive retries up to 3 times with exponential backoff (1 s / 2 s / 4 s).
After 3 consecutive failures it returns AliveResult(result='unknown') rather
than raising; the caller treats unknown as "not dormant" to avoid false-positive
recycling.

/alive is the sole activity signal.  OpenAPI invocations land in session
records and are reflected in alive.last_session_time (limo, 2026-06-22).

Response envelope (verified against
``src/baas/src/secbaas/adapters/web/bot_health_checker_router.py:396`` and
``api/health_check/bot/_models.py:BotAliveCheckResult``)::

    {
      "code": 0,
      "message": "success",
      "data": {
        "bot_id": "...",
        "overall_alive": true | false,
        "alive_count": 1, "idle_count": 0, "unsupported_count": 0,
        "devices": [
          {
            "paas_device_id": "...",
            "alive": true | false,
            "status": "live" | "idle" | null,
            "last_session_time": "YYYY-MM-DD HH:MM:SS" | null
          }, ...
        ]
      }
    }

Endpoint: ``GET /internal/bot-health-checker/alive`` — internal endpoint
**without** API-key auth (limo, 2026-06-26). Security is provided by MOSN
service mesh (same convention as other backend → BaaS internal endpoints);
no ``Authorization`` header is sent. The external, API-key-protected
endpoint (``/api/v1/bot-health-checker/alive``) is for outside callers.

Transport
---------
Routes through the project-wide ``HttpClient`` Plugin (``Annotated[HttpClient,
QUALIFIER_BAAS]``) — same transport seam BaasService uses. This means:

  - base_url is injected by ``InfrastructureModule.baas_http_client`` from
    YAML ``baas.api_base_url_pre`` / ``api_base_url`` (no env fallback).
  - prod uses sync ``httpx.Client`` per call (``HttpxClient``), so the
    sofa_tracer SpawnProcess hook problem on ``AsyncClient.send`` does not
    apply (the tracer patches AsyncClient, not Client).
  - singlebox / pytest use ``LocalHttpClient`` — unstubbed calls raise rather
    than silently hitting the network.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, Optional

from injector import inject

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS

logger = get_logger()


# ---------------------------------------------------------------------------
# Public data transfer objects
# ---------------------------------------------------------------------------

_RETRY_DELAYS = (1.0, 2.0, 4.0)  # exponential backoff seconds for 3 retries
_REQUEST_TIMEOUT_S = 10.0


@dataclass
class AliveResult:
    """Result of a single check_alive call.

    result: 'true' | 'false' | 'unknown'
        Derived from BaaS ``overall_alive`` boolean. 'unknown' is returned
        when all retry attempts failed or the response was malformed; the
        caller should treat an unknown bot as *not dormant* to avoid
        false-positive recycling.
    last_session_time: timestamp string ("YYYY-MM-DD HH:MM:SS") or None.
        The MAX last_session_time across all devices in the response
        (multi-sandbox aggregation, treats any device's recent session as
        the bot being recently active). None when no device has a value.
    """

    result: str  # 'true' | 'false' | 'unknown'
    last_session_time: Optional[str]


def _max_session_time(devices: list[dict[str, Any]]) -> Optional[str]:
    """Pick the MAX non-null last_session_time across devices.

    String comparison works because the format is fixed (YYYY-MM-DD HH:MM:SS).
    """
    values = [
        d.get("last_session_time")
        for d in devices
        if d.get("last_session_time")
    ]
    if not values:
        return None
    return max(values)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class BaasDormantClient:
    """Thin wrapper over the project-wide BaaS-qualified ``HttpClient``.

    The wrapper only owns the dormant-specific logic — endpoint path, retry
    policy, envelope parsing — and delegates all transport to the injected
    ``HttpClient``. Same pattern BaasService uses.

    Auth: the BaaS endpoint we call is ``/internal/bot-health-checker/alive``,
    an internal endpoint without API-key auth (MOSN-mesh-secured). No
    ``Authorization`` header is sent.
    """

    @inject
    def __init__(
        self,
        http_client: Annotated[HttpClient, QUALIFIER_BAAS],
    ) -> None:
        self._http = http_client

    # ------------------------------------------------------------------
    # check_alive
    # ------------------------------------------------------------------

    async def check_alive(
        self, *, bot_id: str, entity_id: str, minutes: int
    ) -> AliveResult:
        """Query whether a bot has had recent session activity.

        Sends (via the injected HttpClient with base_url=BaaS gateway):
            GET /internal/bot-health-checker/alive
                ?bot_id=...&entity_id=...&minutes=...

        No Authorization header — the internal endpoint is MOSN-secured.

        Returns AliveResult with result='unknown' if all 3 retries fail or
        the response envelope is malformed (defensive: a future API change
        should not silently mark everyone dormant).

        Args:
            bot_id: The bot to check.
            entity_id: The entity (user/group) context.
            minutes: Look-back window in minutes.

        Returns:
            AliveResult with result in {'true', 'false', 'unknown'}.
        """
        params = {"bot_id": bot_id, "entity_id": entity_id, "minutes": minutes}
        last_exc: Optional[Exception] = None

        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            try:
                # HttpClient is sync; run in thread so we don't block the
                # event loop running the dormant scan.
                response = await asyncio.to_thread(
                    self._http.get,
                    "/internal/bot-health-checker/alive",
                    params=params,
                    timeout=_REQUEST_TIMEOUT_S,
                )
                response.raise_for_status()
                envelope = response.json()
                return self._parse_envelope(envelope, bot_id)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[BaasDormantClient.check_alive] attempt %d/%d failed "
                    "bot_id=%s: %s",
                    attempt,
                    len(_RETRY_DELAYS),
                    bot_id,
                    exc,
                )
                if attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(delay)

        logger.error(
            "[BaasDormantClient.check_alive] all retries exhausted for bot_id=%s "
            "— returning unknown. last error: %s",
            bot_id,
            last_exc,
        )
        return AliveResult(result="unknown", last_session_time=None)

    @staticmethod
    def _parse_envelope(envelope: Any, bot_id: str) -> AliveResult:
        """Decode the ApiResponse envelope into an AliveResult.

        Treats anything that doesn't match the expected shape as 'unknown';
        we'd rather skip a bot than misclassify it dormant on a schema drift.
        """
        if not isinstance(envelope, dict):
            logger.warning(
                "[BaasDormantClient.check_alive] non-dict envelope for bot_id=%s: %r",
                bot_id,
                type(envelope),
            )
            return AliveResult(result="unknown", last_session_time=None)

        # Non-zero code from BaaS = error; surface as unknown rather than crash.
        code = envelope.get("code", 0)
        if code != 0:
            logger.warning(
                "[BaasDormantClient.check_alive] BaaS error code=%s message=%r "
                "for bot_id=%s — returning unknown",
                code,
                envelope.get("message"),
                bot_id,
            )
            return AliveResult(result="unknown", last_session_time=None)

        data = envelope.get("data")
        if not isinstance(data, dict):
            logger.warning(
                "[BaasDormantClient.check_alive] missing/invalid data field for "
                "bot_id=%s — returning unknown",
                bot_id,
            )
            return AliveResult(result="unknown", last_session_time=None)

        overall_alive = data.get("overall_alive")
        # Reject non-bool values; this is a contract field, drift = unknown.
        if not isinstance(overall_alive, bool):
            logger.warning(
                "[BaasDormantClient.check_alive] overall_alive not bool (got %r) "
                "for bot_id=%s — returning unknown",
                overall_alive,
                bot_id,
            )
            return AliveResult(result="unknown", last_session_time=None)

        devices = data.get("devices") or []
        if not isinstance(devices, list):
            devices = []
        last_session_time = _max_session_time(devices)

        return AliveResult(
            result="true" if overall_alive else "false",
            last_session_time=last_session_time,
        )
