"""``AcliMarketSearch`` — the market API's answer becomes a page or a clear error."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from agentclaw.community.core.acli_clis.errors import (
    AcliMarketNotConfiguredError,
    AcliMarketUnavailableError,
)
from agentclaw.community.core.acli_clis.market import (
    _LIST_MAX_PAGES,
    SEARCH_TIMEOUT_SECONDS,
    AcliMarketSearch,
)

URL = "https://market.example.test/api/v1/cli-tools/search"
#: A browse (empty keyword) cannot use search — the market answers it on
#: the list API, the search address with the last ``/search`` dropped.
LIST_URL = "https://market.example.test/api/v1/cli-tools"

#: Shaped like the real API's answer (2026-10, trimmed): every match at once,
#: with fields the backend drops.
_ROWS = [
    {"id": 13301132, "name": "dataphin", "command": "dataphin",
     "description": "dataphinCLI工具", "author": "fw392091", "version": "1.4.14",
     "status": "published", "readme": "# Dataphin CLI …", "downloads": 37304},
    {"id": 13104033, "name": "deepinsight-cli", "command": "di", "description": "1",
     "author": "sunweipeng.swp", "version": "1.0.9"},
    {"id": 7900068, "name": "tracehub-data-construct-cli",
     "command": "tracehub-data-construct-cli", "description": None, "version": "1.4.1"},
]


class _Http:
    def __init__(
        self,
        response: Any = None,
        error: Exception | None = None,
        responses: list[Any] | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.responses = responses
        self.calls: list[tuple] = []

    def get(self, path, *, params=None, headers=None, timeout=30.0):
        self.calls.append((path, params, timeout))
        if self.responses is not None:
            answer = self.responses.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        if self.error:
            raise self.error
        return self.response


def _ok(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("GET", URL))


def _search(http: _Http, *, keyword="data", page_no=1, page_size=2, url=URL):
    return AcliMarketSearch(http_client=http, search_url=url).search(  # type: ignore[arg-type]
        keyword=keyword, page_no=page_no, page_size=page_size
    )


def test_search_asks_for_published_tools_at_the_configured_url() -> None:
    http = _Http(_ok({"success": True, "errorMsg": None, "data": _ROWS}))

    page = _search(http)

    assert http.calls == [(URL, {"status": "published", "q": "data"}, SEARCH_TIMEOUT_SECONDS)]
    assert [(t.name, t.command, t.description, t.version) for t in page.items] == [
        ("dataphin", "dataphin", "dataphinCLI工具", "1.4.14"),
        ("deepinsight-cli", "di", "1", "1.0.9"),
    ]
    assert (page.total, page.has_more) == (3, True)


def test_pages_are_cut_from_the_full_answer() -> None:
    http = _Http(_ok({"success": True, "data": _ROWS}))

    page = _search(http, page_no=2, page_size=2)

    assert [t.name for t in page.items] == ["tracehub-data-construct-cli"]
    assert page.items[0].description == ""
    assert (page.total, page.has_more) == (3, False)


def test_an_empty_keyword_lists_the_whole_market() -> None:
    # The list API pages its answer, so a browse reads one 100-row page at a
    # time and stops at the empty page that ends the catalogue.
    http = _Http(
        responses=[
            _ok({"success": True, "errorMsg": None, "data": _ROWS}),
            _ok({"success": True, "errorMsg": None, "data": []}),
        ]
    )

    page = _search(http, keyword="")

    assert http.calls == [
        (
            LIST_URL,
            {"status": "published", "pageNo": 1, "pageSize": 100},
            SEARCH_TIMEOUT_SECONDS,
        ),
        (
            LIST_URL,
            {"status": "published", "pageNo": 2, "pageSize": 100},
            SEARCH_TIMEOUT_SECONDS,
        ),
    ]
    assert [t.name for t in page.items] == ["dataphin", "deepinsight-cli"]
    assert (page.total, page.has_more) == (3, True)


def test_a_browse_that_fills_every_list_page_stops_short_and_logs(caplog) -> None:
    full = _ok(
        {"success": True, "data": [{"name": f"tool-{row}"} for row in range(100)]}
    )
    http = _Http(responses=[full] * _LIST_MAX_PAGES)

    page = _search(http, keyword="")

    assert len(http.calls) == _LIST_MAX_PAGES
    assert page.total == 100 * _LIST_MAX_PAGES
    assert "acli_market_browse_truncated" in caplog.text


def test_an_empty_keyword_without_the_search_segment_is_not_configured() -> None:
    # No list address can be derived from a search address that does not end
    # in ``/search``, so a browse of that deployment is not configured —
    # while a keyword search keeps working on the same address.
    http = _Http(_ok({"success": True, "data": _ROWS}))
    url = "https://market.example.test/api/v1/tools"

    with pytest.raises(AcliMarketNotConfiguredError):
        _search(http, keyword="", url=url)

    assert http.calls == []

    assert _search(http, url=url).total == 3
    assert [call[0] for call in http.calls] == [url]


@pytest.mark.parametrize(
    "http",
    [
        _Http(_ok({"success": False, "errorMsg": "query.pageNo: Field required"})),
        _Http(error=httpx.ConnectError("refused")),
    ],
)
def test_an_unusable_browse_answer_is_unavailable(http) -> None:
    with pytest.raises(AcliMarketUnavailableError):
        _search(http, keyword="")


def test_rows_without_a_name_are_left_out() -> None:
    http = _Http(_ok({"success": True, "data": [3, {}, {"name": ""}, {"name": "ok"}]}))

    assert [t.name for t in _search(http).items] == ["ok"]


def test_no_configured_url_is_not_configured_and_calls_nothing() -> None:
    http = _Http()

    with pytest.raises(AcliMarketNotConfiguredError):
        _search(http, url="")

    assert http.calls == []


@pytest.mark.parametrize(
    "http",
    [
        _Http(error=httpx.ConnectError("refused")),
        _Http(error=httpx.ReadTimeout("slow")),
        _Http(_ok({"success": False}, status=500)),
        _Http(httpx.Response(200, text="not json", request=httpx.Request("GET", URL))),
        _Http(_ok({"success": False, "errorMsg": "boom", "data": None})),
        _Http(_ok({"success": True, "data": {"items": []}})),
        _Http(_ok(["not", "an", "object"])),
    ],
)
def test_an_unusable_answer_is_unavailable(http) -> None:
    with pytest.raises(AcliMarketUnavailableError):
        _search(http)
