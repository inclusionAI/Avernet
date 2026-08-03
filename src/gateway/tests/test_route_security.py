"""Unit tests for the route-security table (identity requirement parsing)."""

from __future__ import annotations

from pathlib import Path

import yaml

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import Presence, PrincipalType

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "application.yaml"


def test_shipped_config_loads_and_requires_user() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    req = rs.resolve("GET", "/openapi/v1/bots/abc")
    assert req is not None
    assert req[PrincipalType.USER] is Presence.REQUIRED


def test_shipped_config_exempts_the_engine_socket_prefix() -> None:
    """The socket's credential is checked by the hop behind the gateway.

    An *empty* requirement, not a missing rule: an omitted rule falls through to
    ``/**`` and fails closed, so the exemption has to be written down — which is
    also what keeps the table an honest description of the gateway's posture.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    req = rs.resolve("GET", "/openapi/v1/engine/ARCA_x@0:20003/api/openclaw/ws")
    assert req == {}


def test_shipped_config_exempts_only_the_bcn_session_websocket_get() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])

    assert rs.resolve("GET", "/openapi/v1/collaboration/group/ws") == {}

    token_post = rs.resolve(
        "POST", "/openapi/v1/collaboration/sessions/session-1/token"
    )
    assert token_post is not None
    assert token_post[PrincipalType.USER] is Presence.REQUIRED

    ordinary_get = rs.resolve("GET", "/openapi/v1/collaboration/groups/group-1")
    assert ordinary_get is not None
    assert ordinary_get[PrincipalType.USER] is Presence.REQUIRED


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


def test_from_yaml_loads_route_security(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text("user_config:\n  route_security:\n    /**:\n      user: required\n")
    rs = RouteSecurity.from_yaml(cfg)
    req = rs.resolve("GET", "/anything")
    assert req is not None
    assert req[PrincipalType.USER] is Presence.REQUIRED


def test_from_yaml_empty_file_uses_empty_table(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text("")
    rs = RouteSecurity.from_yaml(cfg)
    assert rs.resolve("GET", "/anything") is None


def test_from_yaml_non_dict_root_uses_empty_table(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text("- just a list")
    rs = RouteSecurity.from_yaml(cfg)
    assert rs.resolve("GET", "/anything") is None


def test_from_yaml_user_config_not_dict_uses_empty_table(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text("user_config: not-a-dict\n")
    rs = RouteSecurity.from_yaml(cfg)
    assert rs.resolve("GET", "/anything") is None
