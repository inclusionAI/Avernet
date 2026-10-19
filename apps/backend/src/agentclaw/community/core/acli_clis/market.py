"""Search the acli CLI market from the backend.

The market's catalogue is the same for every bot, so searching it needs no
container: this calls the market's search API directly. The full search URL
comes from configuration (``AcliMarketConfig.search_url``); an empty one means
this deployment has no market, and every search is refused as not configured.

The API answers ``{"success": true, "data": [ … ]}`` with every match at once
and no paging, so paging happens here, as acli itself does it.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.acli_clis.errors import (
    AcliMarketNotConfiguredError,
    AcliMarketUnavailableError,
)
from agentclaw.community.core.acli_clis.port import AcliMarketPage, AcliTool
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import (
    HttpClient,
    HttpClientRequestError,
    HttpClientStatusError,
)

logger = get_logger()

#: Only published tools can be installed, so only they are listed.
_PUBLISHED = "published"
SEARCH_TIMEOUT_SECONDS = 15.0


class AcliMarketSearch:
    """One search against the acli market's API; stateless and thread-safe."""

    def __init__(self, *, http_client: HttpClient, search_url: str) -> None:
        # ``http_client`` is the general client (no base URL): ``search_url``
        # is the whole configured address, so no market host is in code.
        self._http = http_client
        self._search_url = search_url

    def search(self, *, keyword: str, page_no: int, page_size: int) -> AcliMarketPage:
        """One page of published tools matching ``keyword`` (empty lists all)."""
        if not self._search_url:
            raise AcliMarketNotConfiguredError
        started = time.monotonic()
        params: dict[str, Any] = {"status": _PUBLISHED}
        if keyword:
            params["q"] = keyword
        try:
            # COSEC: the configured URL is the only target; the caller supplies
            # query parameters only, never the host or path.
            response = self._http.get(
                self._search_url, params=params, timeout=SEARCH_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            payload = response.json()
        except (HttpClientStatusError, HttpClientRequestError, ValueError) as error:
            logger.warning(
                "acli_market_search_failed failure=upstream error_type=%s duration_ms=%s",
                type(error).__name__, int((time.monotonic() - started) * 1000),
            )
            raise AcliMarketUnavailableError from error
        if (
            not isinstance(payload, Mapping)
            or payload.get("success") is not True
            or not isinstance(payload.get("data"), list)
        ):
            logger.warning(
                "acli_market_search_failed failure=invalid_response duration_ms=%s",
                int((time.monotonic() - started) * 1000),
            )
            raise AcliMarketUnavailableError
        tools = [tool for tool in map(_tool, payload["data"]) if tool is not None]
        start = (page_no - 1) * page_size
        return AcliMarketPage(
            items=tuple(tools[start:start + page_size]),
            total=len(tools),
            has_more=start + page_size < len(tools),
        )


def _tool(row: object) -> AcliTool | None:
    """A market row as an ``AcliTool``, or ``None`` when it names no tool.

    A row without a usable ``name`` could not be added anyway, so it is left
    out rather than failing the whole search. Extra fields (``readme``,
    ``downloads``, ``author`` …) are dropped.
    """
    if not isinstance(row, Mapping):
        return None
    name = row.get("name")
    if not isinstance(name, str) or not name:
        return None
    return AcliTool(
        name=name,
        command=_text(row.get("command")),
        description=_text(row.get("description")),
        version=_text(row.get("version")),
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


__all__ = ["AcliMarketSearch", "SEARCH_TIMEOUT_SECONDS"]
