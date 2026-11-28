"""Search or browse the acli CLI market from the backend.

The market's catalogue is the same for every bot, so searching it needs no
container: this calls the market's API directly. The full search URL
comes from configuration (``AcliMarketConfig.search_url``); an empty one means
this deployment has no market, and every search is refused as not configured.

The search API answers ``{"success": true, "data": [ … ]}`` with every match
at once and no paging, so paging happens here, as acli itself does it. It
also requires a keyword: a request without ``q`` is not a whole-market
search but an error, so browsing the catalogue (empty keyword) goes to the
market's list API instead — the same address without the final ``/search``
segment — which pages by ``pageNo``/``pageSize`` at most 100 rows at a time.
A browse gathers those pages until an empty one and answers from the merged
rows, so both paths keep the same one-page-of-everything contract.

The market is a live catalogue: rows move while a browse walks its pages,
so a snapshot can duplicate or miss the few rows that were published during
it — the search API's one-shot answer has the same freshness.
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
#: The list API is the search address with this last segment dropped
#: (``…/cli-tools/search`` → ``…/cli-tools``).
_SEARCH_SEGMENT = "/search"
#: The list API caps one page at 100 rows; asking for more is an error.
_LIST_PAGE_SIZE = 100
#: Enough list pages for any catalogue today; a browse that fills them all
#: serves what it read, logs, and answers ``has_more`` for the shorter
#: truth instead of chasing a market that never ends.
_LIST_MAX_PAGES = 30
SEARCH_TIMEOUT_SECONDS = 15.0


class AcliMarketSearch:
    """One search or browse of the acli market's API; stateless and thread-safe."""

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
        try:
            rows = (
                self._matching_rows(keyword, started)
                if keyword
                else self._catalogue_rows(started)
            )
        except (HttpClientStatusError, HttpClientRequestError, ValueError) as error:
            logger.warning(
                "acli_market_search_failed failure=upstream error_type=%s duration_ms=%s",
                type(error).__name__, int((time.monotonic() - started) * 1000),
            )
            raise AcliMarketUnavailableError from error
        tools = [tool for tool in map(_tool, rows) if tool is not None]
        start = (page_no - 1) * page_size
        return AcliMarketPage(
            items=tuple(tools[start:start + page_size]),
            total=len(tools),
            has_more=start + page_size < len(tools),
        )

    def _matching_rows(self, keyword: str, started: float) -> list[object]:
        """Every published row matching ``keyword``, from one search request."""
        params: dict[str, Any] = {"status": _PUBLISHED, "q": keyword}
        return self._rows(self._search_url, params, started)

    def _catalogue_rows(self, started: float) -> list[object]:
        """Every published row, from the list API's pages, until an empty page."""
        # COSEC: the list address is derived from the configured search
        # address; only query parameters are supplied, never a host or path.
        page_rows: list[object] = []
        for page_no in range(1, _LIST_MAX_PAGES + 1):
            rows = self._rows(
                self._list_url(),
                {"status": _PUBLISHED, "pageNo": page_no, "pageSize": _LIST_PAGE_SIZE},
                started,
            )
            page_rows.extend(rows)
            if not rows:
                break
        else:
            logger.warning(
                "acli_market_browse_truncated pages=%s page_size=%s",
                _LIST_MAX_PAGES,
                _LIST_PAGE_SIZE,
            )
        return page_rows

    def _rows(self, url: str, params: dict[str, Any], started: float) -> list[object]:
        # COSEC: the configured URL is the only target; the caller supplies
        # query parameters only, never the host or path.
        response = self._http.get(url, params=params, timeout=SEARCH_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        if not _answers_rows(payload):
            logger.warning(
                "acli_market_search_failed failure=invalid_response duration_ms=%s",
                int((time.monotonic() - started) * 1000),
            )
            raise AcliMarketUnavailableError
        return payload["data"]

    def _list_url(self) -> str:
        """The list API's address: the search address with ``/search`` dropped.

        A search address that does not end in that segment names a different
        market API, so no list address can be derived from it: a browse of
        such a deployment is refused as not configured, while a keyword
        search keeps working on the configured address.
        """
        if not self._search_url.endswith(_SEARCH_SEGMENT):
            raise AcliMarketNotConfiguredError
        return self._search_url[: -len(_SEARCH_SEGMENT)]


def _answers_rows(payload: object) -> bool:
    """Whether the market answered usable rows (``{"success": true, "data": [ … ]}``)."""
    return (
        isinstance(payload, Mapping)
        and payload.get("success") is True
        and isinstance(payload.get("data"), list)
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
