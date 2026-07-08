"""DeviceStatusClient — leaf BaaS device-status reader for the by-owner list.

The by-owner list (``BotService.list_bots_by_owner_or_collaborator``) shows
desktop bots' *live* status: the DB status lags, so the list consumes BaaS
directly. That read is a single httpx GET — it needs no service graph — so it
lives here as a leaf holding only ``baas_api_base`` + ``tenant``.

Constructing it directly (instead of reaching through ``DesktopBotService``)
keeps the list off the ``DesktopBotService → DeviceService → BotService`` edge,
so no DI cycle forms. The client carries its own lightweight error type for the
same reason: importing ``DesktopBotServiceError`` would pull in that service.
"""
from __future__ import annotations

from typing import Any

import httpx

from agentclaw.community.log import get_logger

logger = get_logger()


class DeviceStatusQueryError(Exception):
    """BaaS device-status query failed (transport/HTTP/payload error)."""


class DeviceStatusClient:
    """Thin httpx BaaS device-status reader — a leaf with no service deps."""

    # Generous enough for a healthy BaaS, short enough that the list's overall
    # wall-clock budget (enforced by the caller's ThreadPool) stays meaningful.
    _TIMEOUT_SECONDS = 5.0

    def __init__(self, baas_api_base: str, tenant: str) -> None:
        self._baas_api_base = baas_api_base
        self._tenant = tenant

    @classmethod
    def from_baas_config(cls, baas_config: Any) -> "DeviceStatusClient":
        """Build from ``BaasConfig``, selecting the env-appropriate base URL.

        Single home for the pre/prod base-URL choice so the DI provider that
        constructs this leaf doesn't re-derive it.
        """
        # Local import: env_utils is a leaf util, but importing it at module
        # top would pull env plumbing into every status-mapping consumer.
        from agentclaw.community.utils.env_utils import get_current_env

        base = (
            baas_config.api_base_url_pre
            if get_current_env() == "pre"
            else baas_config.api_base_url
        )
        return cls(baas_api_base=base, tenant=baas_config.tenant)

    def query_device_status(self, device_id: str) -> dict[str, Any]:
        """Query BaaS device-status for one bot.

        GET /api/v1/bots/{device_id}/device-status?tenant={tenant}

        Returns the BaaS ``data`` dict (typically ``bot_status`` / ``device_status``).
        Raises :class:`DeviceStatusQueryError` on any transport/HTTP/payload error
        — the caller (list merge) treats that as "leave the DB status untouched".
        """
        url = f"{self._baas_api_base}/api/v1/bots/{device_id}/device-status"
        try:
            with httpx.Client(timeout=self._TIMEOUT_SECONDS) as client:
                response = client.get(url, params={"tenant": self._tenant})
                response.raise_for_status()
                data = response.json()
                if data.get("code") != 0:
                    raise DeviceStatusQueryError(
                        f"device-status API error: {data.get('message', 'Unknown')}"
                    )
                return data.get("data", {})
        except DeviceStatusQueryError:
            raise
        except Exception as e:
            raise DeviceStatusQueryError(
                f"device-status query failed for {device_id}: {e}"
            ) from e
