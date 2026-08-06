import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding import DomainMap, Forwarding
from gateway.community.spi.forwarder import Forwarder
from gateway.community.spi.schema_catalog import SchemaCatalog
from gateway.community.spi.ws_forwarder import WebSocketForwarder

_FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "bots.openapi.json"
_BAAS_ARTIFACT = (
    Path(__file__).resolve().parents[4] / "configs" / "schemas" / "baas.openapi.json"
)
_RULES = RouteSecurity.from_table({"/**": {"user": "required"}})
_METHODS = {"get", "post", "put", "delete", "patch"}


def _domain_map(**domains: tuple[str, str, str]) -> DomainMap:
    raw = {
        "base_path": "/openapi/v1",
        "domains": {
            name: {"server": server, "schema": {"source": source, "path": "."}}
            for name, (server, source, _) in domains.items()
        },
        "servers": {
            server: {"base_url": "https://example.com"}
            for _, (server, _, _) in domains.items()
        },
    }
    return DomainMap.from_config(raw, variables={})


def _forwarding(
    schema_catalogs: Mapping[str, SchemaCatalog],
    *,
    domains: tuple[str, str, str, str] | None = None,
    forwarder: Forwarder | None = None,
    ws_forwarder: WebSocketForwarder | None = None,
) -> Forwarding:
    if domains is None:
        domains = (("bots", "backend", "file", _FIXTURE.read_text()),)
    domain_map = _domain_map(**{d[0]: (d[1], d[2], d[3]) for d in domains})
    return Forwarding(
        domain_map=domain_map,
        forwarder=forwarder or MagicMock(spec=Forwarder),
        schema_catalogs=schema_catalogs,
        ws_forwarder=ws_forwarder or MagicMock(spec=WebSocketForwarder),
    )


class _StubCatalog:
    def __init__(self, docs: dict[str, dict[str, Any]] | None = None) -> None:
        self._docs = docs or {}

    def current(self, domain: str) -> dict[str, Any]:
        return self._docs.get(domain, {})


class TestDescribeWithMultipleCatalogs:
    def test_file_catalog_serves_bots_http_serves_chat(self) -> None:
        bots_doc = json.loads(_FIXTURE.read_text())
        chat_doc = json.loads(_BAAS_ARTIFACT.read_text())

        file_catalog = _StubCatalog({"bots": bots_doc})
        http_catalog = _StubCatalog({"chat": chat_doc})
        fwd = _forwarding(
            {"file": file_catalog, "http": http_catalog},
            domains=(
                ("bots", "backend", "file", _FIXTURE.read_text()),
                ("chat", "baas", "http", _BAAS_ARTIFACT.read_text()),
            ),
        )

        assert fwd._describe("bots") == bots_doc
        assert fwd._describe("chat") == chat_doc

    def test_http_catalog_takes_priority_on_overlap(self) -> None:
        file_catalog = _StubCatalog({"shared": {"from": "file"}})
        http_catalog = _StubCatalog({"shared": {"from": "http"}})
        fwd = _forwarding(
            {"file": file_catalog, "http": http_catalog},
            domains=(("shared", "backend", "file", ""),),
        )

        # _describe iterates values in order; HTTP is registered last by DI,
        # so its result overwrites file's for the same domain.
        assert fwd._describe("shared") == {"from": "http"}

    def test_unknown_domain_returns_empty(self) -> None:
        fwd = _forwarding({"file": _StubCatalog(), "http": _StubCatalog()})
        assert fwd._describe("nonexistent") == {}

    def test_empty_catalog_returns_empty(self) -> None:
        fwd = _forwarding({"file": _StubCatalog({}), "http": _StubCatalog({})})
        assert fwd._describe("bots") == {}

    def test_catalog_returning_falsy_doc_is_skipped(self) -> None:
        file_catalog = _StubCatalog({"bots": {}})
        http_catalog = _StubCatalog({"bots": {"openapi": "3.1.0", "paths": {}}})
        fwd = _forwarding(
            {"file": file_catalog, "http": http_catalog},
            domains=(("bots", "backend", "file", ""),),
        )

        doc = fwd._describe("bots")
        assert "openapi" in doc


class TestServedOpenAPIWithCombinedCatalogs:
    def test_combined_paths_from_both_catalogs(self) -> None:
        bots_doc = json.loads(_FIXTURE.read_text())
        chat_doc = json.loads(_BAAS_ARTIFACT.read_text())

        file_catalog = _StubCatalog({"bots": bots_doc})
        http_catalog = _StubCatalog({"chat": chat_doc})
        fwd = _forwarding(
            {"file": file_catalog, "http": http_catalog},
            domains=(
                ("bots", "backend", "file", _FIXTURE.read_text()),
                ("chat", "baas", "http", _BAAS_ARTIFACT.read_text()),
            ),
        )

        result = fwd.served_openapi(_RULES, title="gateway", version="0.1.0")
        paths = result["paths"]

        assert "/openapi/v1/bots" in paths
        assert "/openapi/v1/chat/sessions/{session_id}" in paths

    def test_security_markers_applied_to_both_catalogs_paths(self) -> None:
        bots_doc = json.loads(_FIXTURE.read_text())

        file_catalog = _StubCatalog({"bots": bots_doc})
        http_catalog = _StubCatalog({"chat": json.loads(_BAAS_ARTIFACT.read_text())})
        fwd = _forwarding(
            {"file": file_catalog, "http": http_catalog},
            domains=(
                ("bots", "backend", "file", _FIXTURE.read_text()),
                ("chat", "baas", "http", _BAAS_ARTIFACT.read_text()),
            ),
        )

        result = fwd.served_openapi(_RULES, title="gateway", version="0.1.0")
        for path, item in result["paths"].items():
            for method, operation in item.items():
                if method in _METHODS:
                    assert operation["x-avernet-security"] == {"user": "required"}, (
                        f"{method} {path}"
                    )

    def test_empty_catalogs_produce_valid_openapi(self) -> None:
        fwd = _forwarding({"file": _StubCatalog(), "http": _StubCatalog()})
        doc = fwd.served_openapi(_RULES, title="gateway", version="0.1.0")

        assert doc["openapi"].startswith("3.")
        assert doc["paths"] == {}


class TestStartRefreshWithMultipleCatalogs:
    async def test_both_catalogs_started(self) -> None:
        import asyncio

        file_started = False
        http_started = False

        class _RefreshableCatalog(_StubCatalog):
            async def refresh_loop(self, interval: float, stop: asyncio.Event) -> None:
                nonlocal file_started, http_started
                await asyncio.sleep(0)
                stop.wait()

        file_catalog = _RefreshableCatalog()
        http_catalog = _RefreshableCatalog()

        # Patch to track which catalog's refresh_loop is called
        async def _file_loop(interval: float, stop: asyncio.Event) -> None:
            nonlocal file_started
            file_started = True
            stop.set()

        async def _http_loop(interval: float, stop: asyncio.Event) -> None:
            nonlocal http_started
            http_started = True
            stop.set()

        file_catalog.refresh_loop = _file_loop
        http_catalog.refresh_loop = _http_loop

        fwd = _forwarding(
            {"file": file_catalog, "http": http_catalog},
            domains=(("bots", "backend", "file", _FIXTURE.read_text()),),
        )

        await fwd.start_refresh()
        await asyncio.sleep(0.01)
        await fwd.stop_refresh()

        assert file_started
        assert http_started

    async def test_idempotent_start_refresh(self) -> None:
        import asyncio

        fwd = _forwarding(
            {"file": _StubCatalog(), "http": _StubCatalog()},
            domains=(("bots", "backend", "file", _FIXTURE.read_text()),),
        )

        await fwd.start_refresh()
        first_task = fwd._task
        await fwd.start_refresh()
        assert fwd._task is first_task
        await fwd.stop_refresh()

    async def test_catalog_without_refresh_loop_is_skipped(self) -> None:
        file_catalog = _StubCatalog()
        http_catalog = _StubCatalog()
        fwd = _forwarding(
            {"file": file_catalog, "http": http_catalog},
            domains=(("bots", "backend", "file", _FIXTURE.read_text()),),
        )

        await fwd.start_refresh()
        assert fwd._task is not None
        await fwd.stop_refresh()


class TestTagsPreservedFromCombinedCatalogs:
    def test_tags_flow_through_unchanged_from_either_catalog(self) -> None:
        bots_with_tags = {
            "openapi": "3.1.0",
            "info": {"title": "bots", "version": "1.0"},
            "paths": {
                "/openapi/v1/bots": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
            "tags": [
                {
                    "name": "Bots / Management",
                    "description": "Bot lifecycle operations.",
                }
            ],
        }
        chat_with_tags = {
            "openapi": "3.1.0",
            "info": {"title": "chat", "version": "1.0"},
            "paths": {
                "/openapi/v1/chat/sessions": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
            "tags": [
                {"name": "Chat / Sessions", "description": "Chat session operations."}
            ],
        }

        fwd = _forwarding(
            {
                "file": _StubCatalog({"bots": bots_with_tags}),
                "http": _StubCatalog({"chat": chat_with_tags}),
            },
            domains=(
                ("bots", "backend", "file", ""),
                ("chat", "baas", "http", ""),
            ),
        )

        result = fwd.served_openapi(_RULES, title="gateway", version="0.1.0")
        tags = {t["name"] for t in result["tags"]}
        assert "Bots / Management" in tags
        assert "Chat / Sessions" in tags

    def test_documents_without_tags_do_not_affect_tag_order(self) -> None:
        no_tags = {
            "openapi": "3.1.0",
            "info": {"title": "no-tags", "version": "1.0"},
            "paths": {
                "/openapi/v1/bots": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        }
        with_tags = {
            "openapi": "3.1.0",
            "info": {"title": "with-tags", "version": "1.0"},
            "paths": {
                "/openapi/v1/collaboration/groups": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
            "tags": [{"name": "Collaboration / Groups"}],
        }

        fwd = _forwarding(
            {
                "file": _StubCatalog({"bots": no_tags, "collaboration": with_tags}),
                "http": _StubCatalog(),
            },
            domains=(
                ("bots", "backend", "file", ""),
                ("collaboration", "bcs", "file", ""),
            ),
        )

        result = fwd.served_openapi(_RULES, title="gateway", version="0.1.0")
        tags = [t["name"] for t in result["tags"]]
        assert tags == ["Collaboration / Groups"]

    def test_duplicate_tag_names_appear_once_in_first_domain_order(self) -> None:
        first_doc = {
            "openapi": "3.1.0",
            "info": {"title": "first", "version": "1.0"},
            "paths": {
                "/openapi/v1/bots": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
            "tags": [{"name": "Shared", "description": "From first domain."}],
        }
        second_doc = {
            "openapi": "3.1.0",
            "info": {"title": "second", "version": "1.0"},
            "paths": {
                "/openapi/v1/chat/sessions": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
            "tags": [
                {"name": "Shared", "description": "From second domain (ignored)."}
            ],
        }

        fwd = _forwarding(
            {
                "file": _StubCatalog({"bots": first_doc}),
                "http": _StubCatalog({"chat": second_doc}),
            },
            domains=(
                ("bots", "backend", "file", ""),
                ("chat", "baas", "http", ""),
            ),
        )

        result = fwd.served_openapi(_RULES, title="gateway", version="0.1.0")
        assert result["tags"] == [
            {"name": "Shared", "description": "From first domain."}
        ]
