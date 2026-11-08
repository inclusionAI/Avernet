"""Validate the response envelope required by the BaaS Bot POST API."""

from collections.abc import Callable
from typing import Any

from agentclaw.community.core.service_bot.baas_service_errors import BaasServiceError
from agentclaw.community.log import get_logger

logger = get_logger()


class BaasApiResponseError(ValueError):
    """Malformed Bot API JSON or envelope, before operation-specific mapping."""


def read_baas_bot_api_envelope(
    read_json: Callable[[], object], *, action: str
) -> dict[str, Any]:
    """Return an object envelope, normalizing malformed upstream responses."""
    try:
        response_data = read_json()
    except ValueError as exc:
        raise BaasApiResponseError("BaaS API returned invalid JSON") from exc
    if not isinstance(response_data, dict):
        raise BaasApiResponseError("BaaS API returned an invalid response envelope")

    logger.info("[BaasService.%s] BaaS raw response: %s", action, response_data)
    if response_data.get("code") != 0:
        raise BaasServiceError(
            f"BaaS API error: {response_data.get('message', 'Unknown error')}"
        )

    return response_data
