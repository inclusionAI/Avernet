"""Unit tests for the route-security table (identity requirement parsing)."""

from __future__ import annotations

from pathlib import Path

import yaml

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import Presence, PrincipalType

_CONFIG = Path(__file__).resolve().parents[4] / "configs" / "application.yaml"


def test_shipped_config_loads_and_requires_user() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    req = rs.resolve("GET", "/openapi/v1/bots/abc")
    assert req is not None
    assert req[PrincipalType.USER] is Presence.REQUIRED


_SOCKET_PATH = "/openapi/v1/bots/messages/ws/ARCA_x@0:20003/api/openclaw/ws"
_COLLABORATION_SOCKET_PATH = "/openapi/v1/collaboration/messages/ws"


def test_shipped_config_exempts_the_bot_socket_handshake() -> None:
    """The socket's credential is checked by the hop behind the gateway.

    An *empty* requirement, not a missing rule: an omitted rule falls through to
    ``/**`` and fails closed, so the exemption has to be written down — which is
    also what keeps the table an honest description of the gateway's posture.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    assert rs.resolve("WEBSOCKET", _SOCKET_PATH) == {}


def test_the_socket_exemption_beats_the_bots_user_requirement() -> None:
    """It is nested inside an authenticated prefix and must win there.

    Ranked by literal segment count: five to three. Were it the other way, the
    handshake would be challenged for an identity a browser cannot present.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    assert rs.resolve("WEBSOCKET", _SOCKET_PATH) == {}
    bots = rs.resolve("WEBSOCKET", "/openapi/v1/bots/abc")
    assert bots is not None
    assert bots[PrincipalType.USER] is Presence.REQUIRED


def test_the_socket_exemption_stops_at_the_ws_segment() -> None:
    """It exempts the socket's own subtree, not the whole ``messages`` channel.

    ``messages`` names a channel that HTTP endpoints are expected to grow under.
    Anchoring the exemption one segment deeper keeps those endpoints behind the
    user requirement on *every* plane, so a handshake is not a way to reach a
    sibling path unauthenticated.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    sibling = rs.resolve("WEBSOCKET", "/openapi/v1/bots/messages/history")
    assert sibling is not None
    assert sibling[PrincipalType.USER] is Presence.REQUIRED


def test_the_socket_exemption_does_not_reach_the_http_plane() -> None:
    """The table is plane-blind, so the exemption is qualified by plane.

    An ordinary request to this prefix is *not* refused as an unknown route —
    the socket domain is not a candidate on the HTTP plane, so it falls through
    to the ``bots`` domain and is forwarded to the backend. An unqualified
    exemption would therefore let an unauthenticated caller through, and a ``..``
    in the path normalises away en route, landing anywhere under the namespace.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    for path in (_SOCKET_PATH, "/openapi/v1/bots/messages/../../admin/keys"):
        req = rs.resolve("GET", path)
        assert req is not None, path
        assert req[PrincipalType.USER] is Presence.REQUIRED, path


def test_shipped_config_exempts_the_collaboration_socket_handshake() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    assert rs.resolve("WEBSOCKET", _COLLABORATION_SOCKET_PATH) == {}


def test_collaboration_socket_exemption_does_not_reach_the_http_plane() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    req = rs.resolve("GET", _COLLABORATION_SOCKET_PATH)
    assert req is not None
    assert req[PrincipalType.USER] is Presence.REQUIRED


_AUTHORIZED_APPS_PATH = "/openapi/v1/bots/bot-123/authorized-apps"
_AUTHORIZED_BOTS_PATH = "/openapi/v1/bots/authorized"


def test_shipped_config_requires_user_and_app_to_grant_a_bot_authorization() -> None:
    """Granting is a consent moment, so both parties must be on the wire.

    The rule is method-qualified and sits under the globbed
    ``/openapi/v1/bots/**``; it wins because a glob-free pattern outranks a
    globbed one before literal count is compared. Asserted against the shipped
    config rather than a fixture table, so a typo in what actually deploys
    fails here.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    req = rs.resolve("POST", _AUTHORIZED_APPS_PATH)
    assert req is not None
    assert req[PrincipalType.USER] is Presence.REQUIRED
    assert req[PrincipalType.APP] is Presence.REQUIRED


def test_shipped_config_lets_the_owner_list_and_withdraw_without_an_app() -> None:
    """The asymmetry that makes a withdrawal worth having.

    An owner must be able to withdraw after the application's credential is
    lost or rotated, and must be able to ask "which apps can reach my bot?"
    without holding any application's key. Both inherit ``user: required`` from
    ``/openapi/v1/bots/**`` — this pins that the POST rule above did not drag
    them along with it.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    for method, path in (
        ("GET", _AUTHORIZED_APPS_PATH),
        ("DELETE", f"{_AUTHORIZED_APPS_PATH}/42"),
    ):
        req = rs.resolve(method, path)
        assert req is not None, (method, path)
        assert req[PrincipalType.USER] is Presence.REQUIRED, (method, path)
        assert PrincipalType.APP not in req, (method, path)


def test_shipped_config_requires_user_and_app_for_the_application_view() -> None:
    """The App here is not just required, it is what the answer is scoped by.

    The runner resolves only the identities a route declares, so were this rule
    to lose ``app``, the upstream would never see the App principal and its
    query would have nothing to filter on. That failure would widen a listing
    rather than break it, which is why it is pinned.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    rs = RouteSecurity.from_table(raw["user_config"]["route_security"])
    req = rs.resolve("GET", _AUTHORIZED_BOTS_PATH)
    assert req is not None
    assert req[PrincipalType.USER] is Presence.REQUIRED
    assert req[PrincipalType.APP] is Presence.REQUIRED


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
