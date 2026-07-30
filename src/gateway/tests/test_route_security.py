"""Unit tests for the route-security table (identity requirement parsing)."""

from __future__ import annotations

from pathlib import Path

import yaml

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import Presence, PrincipalType

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "application.yaml"


def test_shipped_config_loads_and_requires_user() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["route_security"])
    req = rs.resolve("GET", "/openapi/v1/bots/abc")
    assert req is not None
    assert req[PrincipalType.USER] is Presence.REQUIRED


def test_more_specific_rule_wins() -> None:
    rs = RouteSecurity.from_table(
        {
            "/**": {"user": "required"},
            "/openapi/v1/bots/{id}/chat": {"user": "required", "app": "optional"},
        }
    )
    chat = rs.resolve("POST", "/openapi/v1/bots/x/chat")
    assert chat is not None
    assert chat[PrincipalType.USER] is Presence.REQUIRED
    assert chat[PrincipalType.APP] is Presence.OPTIONAL

    other = rs.resolve("GET", "/openapi/v1/other")
    assert other is not None
    assert PrincipalType.APP not in other


def test_method_specific_rule_beats_method_agnostic() -> None:
    rs = RouteSecurity.from_table(
        {
            "/openapi/v1/bots/{id}": {"app": "required"},
            "GET /openapi/v1/bots/{id}": {"user": "required"},
        }
    )
    get_req = rs.resolve("GET", "/openapi/v1/bots/42")
    assert get_req is not None
    assert PrincipalType.USER in get_req

    post_req = rs.resolve("POST", "/openapi/v1/bots/42")
    assert post_req is not None
    assert PrincipalType.APP in post_req


def test_param_segment_matches_one_segment() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/{id}": {"user": "required"}})
    assert rs.resolve("GET", "/openapi/v1/bots/42") is not None
    assert rs.resolve("GET", "/openapi/v1/bots/42/skills") is None


def test_unmatched_route_is_fail_closed() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/**": {"user": "required"}})
    assert rs.resolve("GET", "/openapi/v1/channels") is None
