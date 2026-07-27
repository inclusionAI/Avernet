"""Unit tests for the route-security table (config parsing + matching)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import PrincipalType

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "route_security.yaml"


def test_shipped_config_loads_and_covers_bots() -> None:
    rs = RouteSecurity.from_yaml(_CONFIG)
    req = rs.resolve("GET", "/openapi/v1/bots/abc")
    assert req is not None
    assert PrincipalType.USER in req


def test_more_specific_rule_wins() -> None:
    rs = RouteSecurity.from_table(
        {
            "/**": ["user"],
            "/openapi/v1/bots/**": ["user"],
        }
    )
    assert rs.resolve("POST", "/openapi/v1/bots/x") == frozenset({PrincipalType.USER})


def test_multi_type_requirement() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/{id}/chat": ["bot", "user"]})
    req = rs.resolve("POST", "/openapi/v1/bots/x/chat")
    assert req == frozenset({PrincipalType.BOT, PrincipalType.USER})


def test_method_specific_rule_beats_method_agnostic() -> None:
    rs = RouteSecurity.from_table(
        {
            "/openapi/v1/bots/{id}": ["user"],
            "GET /openapi/v1/bots/{id}": ["user"],
        }
    )
    assert rs.resolve("GET", "/openapi/v1/bots/42") == frozenset({PrincipalType.USER})
    assert rs.resolve("POST", "/openapi/v1/bots/42") == frozenset({PrincipalType.USER})


def test_param_segment_matches_one_segment() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/{id}": ["user"]})
    assert rs.resolve("GET", "/openapi/v1/bots/42") is not None
    assert rs.resolve("GET", "/openapi/v1/bots/42/skills") is None


def test_unmatched_route_is_fail_closed() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/**": ["user"]})
    assert rs.resolve("GET", "/openapi/v1/channels") is None


def test_unknown_type_string_is_rejected() -> None:
    # Fail-closed against typos in the route table at parse time.
    with pytest.raises(ValueError):
        RouteSecurity.from_table({"/**": ["nonsense"]})
