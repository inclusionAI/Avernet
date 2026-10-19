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
    SEARCH_TIMEOUT_SECONDS,
    AcliMarketSearch,
)

URL = "https://market.example.test/api/v1/cli-tools/search"

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
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple] = []

    def get(self, path, *, params=None, headers=None, timeout=30.0):
        self.calls.append((path, params, timeout))
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
    http = _Http(_ok({"success": True, "data": []}))

    page = _search(http, keyword="")

    assert http.calls[0][1] == {"status": "published"}
    assert (page.items, page.total, page.has_more) == ((), 0, False)


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
