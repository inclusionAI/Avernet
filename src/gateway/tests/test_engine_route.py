"""Unit tests for the root-anchored `/engine` socket route."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gateway.community.core.forwarding import EngineRoute, build_engine_route

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "application.yaml"

_RAW = {
    "engine": {"server": "engine_proxy"},
    "servers": {"engine_proxy": {"base_url": "${engine_proxy_url}"}},
}
_VARS = {"engine_proxy_url": "https://engineproxy.example.com"}


def _route(base_url: str = "https://engineproxy.example.com") -> EngineRoute:
    route = build_engine_route(_RAW, variables={"engine_proxy_url": base_url})
    assert route is not None
    return route


# ── the rewrite ──────────────────────────────────────────────────────────────


def test_engine_prefix_is_swapped_for_proxypass() -> None:
    assert _route().upstream_url("/engine/ARCA_x@0:20003/api/openclaw/ws", "") == (
        "wss://engineproxy.example.com/proxypass/ARCA_x@0:20003/api/openclaw/ws"
    )


def test_query_is_carried_through() -> None:
    url = _route().upstream_url(
        "/engine/target/api/openclaw/ws", "x-proxypass-token=abc.def-gh_i"
    )
    assert url == (
        "wss://engineproxy.example.com/proxypass/target/api/openclaw/ws"
        "?x-proxypass-token=abc.def-gh_i"
    )


def test_percent_encoding_survives_verbatim() -> None:
    """The tail is sliced, never decoded — the hop published these bytes."""
    url = _route().upstream_url("/engine/ARCA%5Fx%400%3A20003/api/x%20y/ws", "a=%2B1")
    assert url == (
        "wss://engineproxy.example.com/proxypass/ARCA%5Fx%400%3A20003/api/x%20y/ws"
        "?a=%2B1"
    )


def test_path_outside_the_prefix_is_not_routed() -> None:
    route = _route()
    assert route.upstream_url("/openapi/v1/bots", "") is None
    assert route.upstream_url("/engineering/thing", "") is None
    assert route.upstream_url("/x/engine/thing", "") is None


def test_bare_prefix_has_no_tail_to_route() -> None:
    assert _route().upstream_url("/engine", "") is None


def test_trailing_slash_on_the_prefix_is_routed_as_an_empty_target() -> None:
    """`/engine/` is on-prefix; the hop behind the gateway rejects it, not us."""
    assert _route().upstream_url("/engine/", "") == (
        "wss://engineproxy.example.com/proxypass/"
    )


# ── the upstream origin ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://proxy.example", "wss://proxy.example"),
        ("http://proxy.example:8080", "ws://proxy.example:8080"),
        ("wss://proxy.example", "wss://proxy.example"),
        ("ws://proxy.example", "ws://proxy.example"),
        ("HTTPS://proxy.example", "wss://proxy.example"),
    ],
)
def test_scheme_is_mapped_to_a_websocket_scheme(base_url: str, expected: str) -> None:
    assert _route(base_url).ws_base_url == expected


def test_trailing_slash_on_the_base_url_does_not_double_up() -> None:
    assert _route("https://proxy.example/").upstream_url("/engine/t/ws", "") == (
        "wss://proxy.example/proxypass/t/ws"
    )


def test_base_url_without_a_scheme_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="no scheme a websocket can be opened with"):
        _route("engineproxy.example.com")


def test_base_url_with_an_unopenable_scheme_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="no scheme a websocket can be opened with"):
        _route("ftp://engineproxy.example.com")


# ── configuration ────────────────────────────────────────────────────────────


def test_absent_engine_block_means_no_route() -> None:
    """A deployment that fronts no engine proxy serves no socket."""
    assert build_engine_route({"servers": {}}, variables={}) is None


def test_unknown_server_reference_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown server"):
        build_engine_route({"engine": {"server": "ghost"}, "servers": {}}, variables={})


def test_non_mapping_engine_block_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        build_engine_route({"engine": "engine_proxy"}, variables={})


def test_shipped_config_serves_no_engine_route() -> None:
    """Community fronts no engine proxy — the block is documented, not set."""
    raw = yaml.safe_load(_CONFIG.read_text())["user_config"]["upstreams"]
    assert build_engine_route(raw, variables=_VARS) is None
