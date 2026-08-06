"""Conformance test for HttpSchemaCatalog (extends SchemaCatalog SPI Rule 25)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx

from gateway.community.plugins.schema_catalog.http import HttpSchemaCatalog
from gateway.community.plugins.schema_catalog.http._plugin import (
    _build_conditional_headers,
    _store_conditional_headers,
)
from gateway.community.spi.schema_catalog import SchemaCatalog


def _make_response(
    status: int = 200, body: object = None, headers: dict | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body if isinstance(body, (dict, list)) else None,
        content=json.dumps(body).encode() if isinstance(body, (dict, list)) else b"",
        headers=headers or {},
        request=httpx.Request("GET", "http://example.com"),
    )


def _yaml_response(body: str, headers: dict | None = None) -> httpx.Response:
    merged = {"content-type": "application/yaml"}
    if headers:
        merged.update(headers)
    return httpx.Response(
        status_code=200,
        content=body.encode(),
        headers=merged,
        request=httpx.Request("GET", "http://example.com"),
    )


def _new_catalog(sources: dict | None = None) -> HttpSchemaCatalog:
    return HttpSchemaCatalog(sources or {})


def _mock_client(response: httpx.Response) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return client


def test_reads_remote_schema():
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})
    catalog._client = _mock_client(
        _make_response(200, {"openapi": "3.1.0", "paths": {"/openapi/v1/bots": {}}})
    )

    assert catalog.refresh("bots") is True
    served: SchemaCatalog = catalog
    assert served.current("bots")["openapi"] == "3.1.0"


def test_unknown_domain_returns_empty():
    catalog = _new_catalog({})
    assert catalog.current("bots") == {}


def test_unknown_domain_refresh_returns_false():
    catalog = _new_catalog({})
    assert catalog.refresh("bots") is False


def test_keeps_last_known_good_on_http_error():
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})
    catalog._client = _mock_client(_make_response(200, {"version": 1}))
    assert catalog.refresh("bots") is True
    assert catalog.current("bots") == {"version": 1}

    catalog._client = _mock_client(_make_response(500))
    assert catalog.refresh("bots") is False
    assert catalog.current("bots") == {"version": 1}


def test_keeps_last_known_good_on_malformed():
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})
    catalog._client = _mock_client(_make_response(200, {"version": 1}))
    assert catalog.refresh("bots") is True

    bad = httpx.Response(
        status_code=200,
        content=b"{not valid json",
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://example.com"),
    )
    catalog._client = _mock_client(bad)
    assert catalog.refresh("bots") is False
    assert catalog.current("bots") == {"version": 1}


def test_non_mapping_document_is_rejected():
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})
    catalog._client = _mock_client(_make_response(200, [1, 2, 3]))
    assert catalog.refresh("bots") is False
    assert catalog.current("bots") == {}


def test_304_not_modified_reuses_cache():
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})
    catalog._client = _mock_client(
        _make_response(200, {"version": 1}, {"etag": '"abc123"'})
    )
    assert catalog.refresh("bots") is True
    assert catalog._etags["bots"] == '"abc123"'

    catalog._client = _mock_client(
        httpx.Response(
            status_code=304, request=httpx.Request("GET", "http://example.com")
        )
    )
    assert catalog.refresh("bots") is True
    assert catalog.current("bots") == {"version": 1}


def test_etag_change_triggers_refetch():
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})
    catalog._client = _mock_client(
        _make_response(200, {"version": 1}, {"etag": '"abc123"'})
    )
    catalog.refresh("bots")
    assert catalog._etags["bots"] == '"abc123"'

    catalog._client = _mock_client(
        _make_response(200, {"version": 2}, {"etag": '"def456"'})
    )
    assert catalog.refresh("bots") is True
    assert catalog._etags["bots"] == '"def456"'
    assert catalog.current("bots") == {"version": 2}


def test_yaml_content_type_parsed():
    catalog = _new_catalog({"bots": "http://example.com/bots.yaml"})
    catalog._client = _mock_client(_yaml_response("openapi: '3.1.0'\npaths: {}\n"))
    assert catalog.refresh("bots") is True
    assert catalog.current("bots")["openapi"] == "3.1.0"


async def test_refresh_loop_updates_cache_and_stops():
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=_make_response(200, {"version": 1}))

    with patch("httpx.AsyncClient", return_value=client):
        stop = asyncio.Event()
        task = asyncio.create_task(catalog.refresh_loop(0.01, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task

    assert catalog.current("bots")["version"] == 1


def test_set_sources_replaces_existing() -> None:
    catalog = _new_catalog({"old": "http://example.com/old.json"})
    assert "old" in catalog._sources

    catalog.set_sources({"new": "http://example.com/new.json"})
    assert "old" not in catalog._sources
    assert catalog._sources == {"new": "http://example.com/new.json"}


def test_refresh_all_calls_refresh_for_each_domain() -> None:
    catalog = _new_catalog(
        {"a": "http://example.com/a.json", "b": "http://example.com/b.json"}
    )
    catalog._client = _mock_client(_make_response(200, {"version": 1}))

    catalog.refresh_all()
    assert catalog.current("a") == {"version": 1}
    assert catalog.current("b") == {"version": 1}


def test_set_sources_does_not_mutate_input() -> None:
    sources = {"x": "http://example.com/x.json"}
    catalog = _new_catalog(sources)
    sources["y"] = "http://example.com/y.json"
    assert "y" not in catalog._sources
    assert catalog._sources == {"x": "http://example.com/x.json"}


def test_httpx_error_on_refresh_returns_false() -> None:
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    catalog._client = client

    assert catalog.refresh("bots") is False
    assert catalog.current("bots") == {}


async def test_refresh_all_async_catches_per_domain_exception() -> None:
    catalog = _new_catalog(
        {"good": "http://example.com/good.json", "bad": "http://example.com/bad.json"}
    )
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[
            _make_response(200, {"version": 1}),
            httpx.ConnectError("refused"),
        ]
    )
    catalog._client = client

    await catalog._refresh_all_async()
    assert catalog.current("good") == {"version": 1}
    assert catalog.current("bad") == {}


async def test_refresh_all_async_bare_exception_is_caught() -> None:
    catalog = _new_catalog({"x": "http://example.com/x.json"})
    client = AsyncMock()
    client.get = AsyncMock(side_effect=ValueError("unexpected"))
    catalog._client = client

    await catalog._refresh_all_async()
    assert catalog.current("x") == {}


def test_get_client_creates_new_when_none_set() -> None:
    catalog = _new_catalog({})
    assert catalog._client is None

    client = catalog._get_client()
    assert isinstance(client, httpx.AsyncClient)
    assert client.timeout == httpx.Timeout(30.0)


def test_build_conditional_headers_with_both() -> None:
    headers = _build_conditional_headers('"abc"', "Thu, 01 Jan 2026 00:00:00 GMT")
    assert headers == {
        "Accept": "application/json, application/yaml",
        "If-None-Match": '"abc"',
        "If-Modified-Since": "Thu, 01 Jan 2026 00:00:00 GMT",
    }


def test_build_conditional_headers_with_neither() -> None:
    headers = _build_conditional_headers(None, None)
    assert headers == {"Accept": "application/json, application/yaml"}


def test_store_conditional_headers_stores_etag_and_last_modified() -> None:
    etags: dict[str, str] = {}
    last_modified: dict[str, str] = {}
    response = _make_response(
        200,
        {"version": 1},
        {"etag": '"xyz"', "last-modified": "Wed, 02 Jan 2026 00:00:00 GMT"},
    )

    _store_conditional_headers(response, "bots", etags, last_modified)
    assert etags == {"bots": '"xyz"'}
    assert last_modified == {"bots": "Wed, 02 Jan 2026 00:00:00 GMT"}


def test_store_conditional_headers_skips_missing() -> None:
    etags: dict[str, str] = {}
    last_modified: dict[str, str] = {}
    response = _make_response(200, {"version": 1})

    _store_conditional_headers(response, "bots", etags, last_modified)
    assert etags == {}
    assert last_modified == {}


def test_store_conditional_headers_only_etag() -> None:
    etags: dict[str, str] = {}
    last_modified: dict[str, str] = {}
    response = _make_response(200, {"version": 1}, {"etag": '"abc"'})

    _store_conditional_headers(response, "bots", etags, last_modified)
    assert etags == {"bots": '"abc"'}
    assert last_modified == {}


async def test_httpx_error_on_async_refresh_returns_false() -> None:
    """httpx.HTTPError during _refresh_async returns False, keeps old cache."""
    catalog = _new_catalog({"bots": "http://example.com/bots.json"})
    catalog._cache["bots"] = {"version": 1}
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    catalog._client = client

    result = await catalog._refresh_async("bots", "http://example.com/bots.json")
    assert result is False
    assert catalog.current("bots") == {"version": 1}
