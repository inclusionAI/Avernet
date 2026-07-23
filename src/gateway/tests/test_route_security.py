"""Unit tests for the route-security table (config parsing + matching)."""

from __future__ import annotations

from pathlib import Path

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import Delegation

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "route_security.yaml"


def test_shipped_config_loads_and_covers_bots() -> None:
    rs = RouteSecurity.from_yaml(_CONFIG)
    req = rs.resolve("GET", "/openapi/v1/bots/abc")
    assert req is not None
    assert "first_party_user" in req[0]


def test_more_specific_rule_wins() -> None:
    rs = RouteSecurity.from_table(
        {
            "/**": ["first_party_user"],
            "/openapi/v1/bots/**": [{"first_party_user": {"delegation": "forbidden"}}],
        }
    )
    bots = rs.resolve("POST", "/openapi/v1/bots/x")
    assert bots is not None
    assert bots[0]["first_party_user"].delegation is Delegation.FORBIDDEN

    other = rs.resolve("GET", "/openapi/v1/other")
    assert other is not None
    assert other[0]["first_party_user"].delegation is Delegation.OPTIONAL


def test_method_specific_rule_beats_method_agnostic() -> None:
    rs = RouteSecurity.from_table(
        {
            "/openapi/v1/bots/{id}": [{"first_party_user": {}}],
            "GET /openapi/v1/bots/{id}": [
                {"first_party_user": {"scopes": ["bots:read"]}}
            ],
        }
    )
    get_req = rs.resolve("GET", "/openapi/v1/bots/42")
    assert get_req is not None
    assert get_req[0]["first_party_user"].scopes == frozenset({"bots:read"})

    post_req = rs.resolve("POST", "/openapi/v1/bots/42")
    assert post_req is not None
    assert post_req[0]["first_party_user"].scopes == frozenset()


def test_param_segment_matches_one_segment() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/{id}": ["first_party_user"]})
    assert rs.resolve("GET", "/openapi/v1/bots/42") is not None
    # {id} matches exactly one segment, not a deeper path.
    assert rs.resolve("GET", "/openapi/v1/bots/42/skills") is None


def test_unmatched_route_is_fail_closed() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/**": ["first_party_user"]})
    assert rs.resolve("GET", "/openapi/v1/channels") is None
