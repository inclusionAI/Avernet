"""Conformance test for HttpSchemaCatalog (extends SchemaCatalog SPI Rule 25)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx

from gateway.community.plugins.schema_catalog.http import HttpSchemaCatalog
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
