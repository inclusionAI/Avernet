from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding import DomainMap, Server
from gateway.community.core.forwarding._domains import _expand_vars, _parse_servers
from gateway.community.spi.authn import Presence, PrincipalType

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "application.yaml"

_VARS = {
    "backend_server_url": "http://backend:8080",
    "baas_server_url": "http://baas:9090",
    "bcs_server_url": "http://bcs:8081",
    "engine_proxy_server_url": "https://engineproxy:20003",
}


def _server(domain: object) -> Server | None:
    """The resolved domain's upstream, or ``None`` if nothing resolved."""
    return getattr(domain, "server", None)


def _map() -> DomainMap:
    return DomainMap.from_config(
        {
            "base_path": "/openapi/v1",
            "domains": {
                "bots": {
                    "server": "backend",
                    "schema": {"source": "file", "path": "schemas/bots.openapi.json"},
                }
            },
            "servers": {"backend": {"base_url": "http://backend:8080"}},
        },
        variables={},
    )


def test_resolves_configured_domain() -> None:
    server = _server(_map().http_domain_for("/openapi/v1/bots/123/restart"))
    assert server is not None
    assert server.name == "backend"
    assert server.base_url == "http://backend:8080"


def test_domain_at_root_resolves() -> None:
    assert _map().http_domain_for("/openapi/v1/bots") is not None


def test_unknown_domain_returns_none() -> None:
    assert _map().http_domain_for("/openapi/v1/unknown/x") is None


def test_path_outside_version_base_returns_none() -> None:
    assert _map().http_domain_for("/api/bots/123") is None


def test_bare_version_base_has_no_domain() -> None:
    assert _map().http_domain_for("/openapi/v1") is None


def test_schema_source_parsed() -> None:
    domain = _map().http_domain_for("/openapi/v1/bots/1")
    assert domain is not None
    assert domain.schema.source == "file"
    assert domain.schema.location == "schemas/bots.openapi.json"
    assert domain.schema.refresh_seconds == 300


def test_shipped_config_loads() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    dm = DomainMap.from_config(raw["user_config"]["upstreams"], variables=_VARS)
    assert dm.http_domain_for("/openapi/v1/bots") is not None


def test_shipped_config_routes_collaboration_verbatim_to_bcs() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    dm = DomainMap.from_config(raw["user_config"]["upstreams"], variables=_VARS)

    collaboration = dm.domain_for("/openapi/v1/collaboration/groups/group-1")
    assert collaboration is not None
    assert collaboration.server.name == "bcs"
    assert collaboration.server.base_url == "http://bcs:8081"
    assert collaboration.serves_http
    assert collaboration.serves_websocket
    assert collaboration.rewrite is None
    assert collaboration.upstream_path("/openapi/v1/collaboration/groups/group-1") == (
        "/openapi/v1/collaboration/groups/group-1"
    )
    assert collaboration.schema.location == "schemas/bcn.openapi.json"

    security = RouteSecurity.from_table(raw["user_config"]["route_security"])
    requirement = security.resolve("GET", "/openapi/v1/collaboration/groups/group-1")
    assert requirement is not None
    assert requirement[PrincipalType.USER] is Presence.REQUIRED

    websocket_requirement = security.resolve(
        "GET", "/openapi/v1/collaboration/messages/ws"
    )
    assert websocket_requirement == {}


# ── protocols ────────────────────────────────────────────────────────────────


def _protocol_map() -> DomainMap:
    return DomainMap.from_config(
        {
            "domains": {
                "bots": {"server": "backend"},
                "engine": {
                    "server": "proxy",
                    "protocols": ["websocket"],
                    "rewrite": {"from": "/openapi/v1/engine", "to": "/proxypass"},
                },
                "both": {"server": "backend", "protocols": ["http", "websocket"]},
            },
            "servers": {
                "backend": {"base_url": "http://backend:8080"},
                "proxy": {"base_url": "https://proxy:20003"},
            },
        },
        variables={},
    )


def test_protocols_default_to_http_only() -> None:
    """Every domain predating the declaration keeps behaving identically."""
    bots = _protocol_map().http_domain_for("/openapi/v1/bots/x")
    assert bots is not None
    assert bots.serves_http
    assert not bots.serves_websocket


def test_a_socket_domain_does_not_serve_http() -> None:
    engine = _protocol_map().websocket_domain_for("/openapi/v1/engine/t/ws")
    assert engine is not None
    assert engine.serves_websocket
    assert not engine.serves_http


def test_a_domain_may_declare_both_planes() -> None:
    both = _protocol_map().http_domain_for("/openapi/v1/both/x")
    assert both is not None
    assert both.serves_http and both.serves_websocket


def test_websocket_domains_lists_only_socket_domains() -> None:
    assert {d.name for d in _protocol_map().websocket_domains()} == {"engine", "both"}


def test_unknown_protocol_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown protocol"):
        DomainMap.from_config(
            {
                "domains": {"x": {"server": "s", "protocols": ["grpc"]}},
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


# ── rewrite ──────────────────────────────────────────────────────────────────


def test_no_rewrite_means_the_path_travels_verbatim() -> None:
    bots = _protocol_map().http_domain_for("/openapi/v1/bots/x")
    assert bots is not None
    assert bots.rewrite is None
    assert bots.upstream_path("/openapi/v1/bots/a%2Fb") == "/openapi/v1/bots/a%2Fb"


def test_a_declared_rewrite_substitutes_only_the_prefix() -> None:
    engine = _protocol_map().websocket_domain_for("/openapi/v1/engine/t/ws")
    assert engine is not None
    assert (
        engine.upstream_path("/openapi/v1/engine/ARCA_x@0:20003/api/openclaw/ws")
        == "/proxypass/ARCA_x@0:20003/api/openclaw/ws"
    )


def test_a_rewrite_never_re_encodes_the_tail() -> None:
    engine = _protocol_map().websocket_domain_for("/openapi/v1/engine/t/ws")
    assert engine is not None
    assert (
        engine.upstream_path("/openapi/v1/engine/ARCA%5Fx%400%3A2/api/x%20y")
        == "/proxypass/ARCA%5Fx%400%3A2/api/x%20y"
    )


def _rewrite_map(from_prefix: str) -> DomainMap:
    return DomainMap.from_config(
        {
            "domains": {
                "engine": {
                    "server": "s",
                    "rewrite": {"from": from_prefix, "to": "/proxypass"},
                }
            },
            "servers": {"s": {"base_url": "http://s"}},
        },
        variables={},
    )


@pytest.mark.parametrize(
    "from_prefix",
    [
        "/somewhere/else",
        # Textually a prefix of `/openapi/v1/engine`, but not on a segment
        # boundary: `/openapi/v1/engine-v2/...` resolves to a domain *named*
        # `engine-v2`, never to this one, so the rule would be accepted at
        # startup and then silently never fire.
        "/openapi/v1/engine-v2",
        "/openapi/v1/engineering",
    ],
)
def test_a_rewrite_anchored_off_the_domain_is_rejected(from_prefix: str) -> None:
    """A rule that could never fire is a config mistake, not a silent no-op."""
    with pytest.raises(ValueError, match="can never match"):
        _rewrite_map(from_prefix)


@pytest.mark.parametrize(
    "from_prefix",
    ["/openapi/v1/engine", "/openapi/v1/engine/", "/openapi/v1/engine/v2"],
)
def test_a_rewrite_on_the_domain_boundary_is_accepted(from_prefix: str) -> None:
    """The domain's own prefix, and any path beneath it, can both fire."""
    engine = _rewrite_map(from_prefix).http_domain_for("/openapi/v1/engine/t")
    assert engine is not None
    assert engine.rewrite is not None


def test_a_rewrite_matches_on_segment_boundaries_not_characters() -> None:
    """A nested `from` must not eat into a longer sibling segment.

    `/openapi/v1/engine/v20/ws` under a `from` of `/openapi/v1/engine/v2` is a
    *different* segment, but a character-wise prefix match would rewrite it to
    `/proxypass0/ws` — a path on the upstream nobody asked for.
    """
    engine = _rewrite_map("/openapi/v1/engine/v2").http_domain_for(
        "/openapi/v1/engine/v20"
    )
    assert engine is not None
    assert engine.upstream_path("/openapi/v1/engine/v20/ws") == (
        "/openapi/v1/engine/v20/ws"
    )
    # The real nested path still rewrites.
    assert engine.upstream_path("/openapi/v1/engine/v2/ws") == "/proxypass/ws"
    # And the prefix on its own maps to the target exactly.
    assert engine.upstream_path("/openapi/v1/engine/v2") == "/proxypass"


@pytest.mark.parametrize("to_prefix", ["proxypass", "proxypass/x", ""])
def test_a_rewrite_target_must_be_an_absolute_path(to_prefix: str) -> None:
    """A relative `to` is absorbed into the upstream *host*, not the path.

    The relay joins it straight onto the origin, so `to: proxypass` against
    `wss://proxy.internal` yields `wss://proxy.internalproxypass/…` — a
    different host, or an unparseable URI.
    """
    with pytest.raises(ValueError, match="absolute path|needs both"):
        DomainMap.from_config(
            {
                "domains": {
                    "engine": {
                        "server": "s",
                        "rewrite": {"from": "/openapi/v1/engine", "to": to_prefix},
                    }
                },
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


@pytest.mark.parametrize(
    "from_prefix", ["/openapi/v1/engine/v2?fixed=1", "/openapi/v1/engine/v2#fragment"]
)
def test_a_rewrite_source_may_not_carry_a_query_or_fragment(from_prefix: str) -> None:
    """A request path carries neither, so such a rule could never fire.

    The anchor check does not catch this on its own: `/openapi/v1/engine/v2?x`
    *does* begin with the domain prefix followed by `/`, so it passes that test
    and lands as a silent no-op — the very thing the anchor check exists to
    prevent.
    """
    with pytest.raises(ValueError, match="no query or fragment"):
        DomainMap.from_config(
            {
                "domains": {
                    "engine": {
                        "server": "s",
                        "rewrite": {"from": from_prefix, "to": "/proxypass"},
                    }
                },
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


@pytest.mark.parametrize("to_prefix", ["/proxypass?fixed=1", "/proxypass#fragment"])
def test_a_rewrite_target_may_not_carry_a_query_or_fragment(to_prefix: str) -> None:
    """Absolute is not sufficient — the request tail is appended to this prefix.

    `to: /proxypass?fixed=1` sends the upstream a path of just `/proxypass` and
    folds the rest of the request, credential included, into the query string.
    """
    with pytest.raises(ValueError, match="no query or fragment"):
        DomainMap.from_config(
            {
                "domains": {
                    "engine": {
                        "server": "s",
                        "rewrite": {"from": "/openapi/v1/engine", "to": to_prefix},
                    }
                },
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


def test_a_rewrite_target_of_root_strips_the_prefix() -> None:
    """`to: /` is a legitimate "mount at the upstream root" rewrite."""
    dm = DomainMap.from_config(
        {
            "domains": {
                "engine": {
                    "server": "s",
                    "rewrite": {"from": "/openapi/v1/engine", "to": "/"},
                }
            },
            "servers": {"s": {"base_url": "http://s"}},
        },
        variables={},
    )
    engine = dm.http_domain_for("/openapi/v1/engine/t")
    assert engine is not None
    assert engine.upstream_path("/openapi/v1/engine/t/ws") == "/t/ws"


def test_a_rewrite_needs_both_ends() -> None:
    with pytest.raises(ValueError, match="needs both"):
        DomainMap.from_config(
            {
                "domains": {
                    "engine": {"server": "s", "rewrite": {"from": "/openapi/v1/engine"}}
                },
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


# ── the base-url standard (one rule for every server) ────────────────────────


@pytest.mark.parametrize(
    ("base_url", "http_url", "ws_url"),
    [
        ("https://up.example", "https://up.example", "wss://up.example"),
        ("http://up.example:8080", "http://up.example:8080", "ws://up.example:8080"),
        # A socket spelling is accepted and re-spelled for the other plane too:
        # the scheme says TLS-or-not, not which planes the upstream serves.
        ("wss://up.example", "https://up.example", "wss://up.example"),
        ("ws://up.example", "http://up.example", "ws://up.example"),
        ("HTTPS://up.example/", "https://up.example", "wss://up.example"),
    ],
)
def test_a_server_is_addressable_on_either_plane(
    base_url: str, http_url: str, ws_url: str
) -> None:
    server = Server(name="up", base_url=base_url)
    assert server.http_base_url == http_url
    assert server.websocket_base_url == ws_url


@pytest.mark.parametrize(
    "base_url",
    ["up.example.com", "up.example.com:8080", "ftp://up.example"],
)
def test_a_base_url_without_a_usable_scheme_is_refused(base_url: str) -> None:
    """Held to the same standard for every server, HTTP-only ones included.

    Not style: the forwarder concatenates the base url with the request path,
    and ``up.example.com/openapi/v1`` is a *relative* URL with an empty host, so
    a scheme-less value would fail at the first call rather than at boot.
    """
    with pytest.raises(ValueError, match="must carry a scheme"):
        Server(name="up", base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://",  # a scheme and nothing else
        "https:///engine-proxy",  # an empty authority; the path only *looks* like a host
        "wss:///",
        "https://:8080/x",  # a port with no host in front of it
        "https://a b/x",  # whitespace never resolves
    ],
)
def test_a_base_url_that_names_no_host_is_refused(base_url: str) -> None:
    """A scheme alone does not make a URL dialable.

    ``https:///engine-proxy`` passes any scheme check — the remainder is
    non-empty — but its authority is empty, so the derived
    ``wss:///engine-proxy/...`` has no hostname and every dial fails at call
    time. That is the failure this validation exists to move to the boot.
    """
    with pytest.raises(ValueError, match="must name a host"):
        Server(name="up", base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://foo..bar",  # an empty label
        "https://-/x",  # a label that is only a hyphen
        "https://a-/x",  # a label ending in a hyphen
        "https://./x",  # the root dot alone is not a name
        "https://-lead.example/x",
    ],
)
def test_a_base_url_whose_dns_labels_are_malformed_is_refused(base_url: str) -> None:
    """Checked per label, not across the whole name.

    A single character class spanning the whole hostname admits an empty label
    and a hyphen-only one, neither of which can ever resolve. Structure is
    knowable at boot; *reachability* is not, which is why nothing here asks
    whether the name currently resolves.
    """
    with pytest.raises(ValueError, match="must name a host"):
        Server(name="up", base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    ["https://up.example/base?fixed=1", "https://up.example/base#fragment"],
)
def test_a_base_url_carrying_a_query_or_fragment_is_refused(base_url: str) -> None:
    """The request path is appended to this value, so either one swallows it.

    ``https://up.example/base?fixed=1`` would dial
    ``…/base?fixed=1/proxypass/...``, putting the entire upstream path inside
    the query string.
    """
    with pytest.raises(ValueError, match="origin and optional base path"):
        Server(name="up", base_url=base_url)


def test_a_base_url_with_an_unusable_port_is_refused() -> None:
    """``urlsplit(...).port`` raises on a non-numeric port; boot is where it should."""
    with pytest.raises(ValueError, match="unusable port"):
        Server(name="up", base_url="wss://up.example:notaport/x")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://up.example",
        "https://up.example:8443",
        "http://up_internal:8080",  # underscores appear in internal service names
        "https://127.0.0.1:9000",
        "wss://[::1]:9000/x",  # an IPv6 literal, brackets and all
        "https://up.example/base/path",  # a base path is still allowed
        "https://up.example./x",  # a trailing dot is a rooted FQDN, not an empty label
        "https://a-b.example/x",  # hyphens are legal *inside* a label
    ],
)
def test_a_base_url_that_names_a_host_is_accepted(base_url: str) -> None:
    assert Server(name="up", base_url=base_url).base_url == base_url


def test_the_standard_applies_through_config_too() -> None:
    with pytest.raises(ValueError, match="must carry a scheme"):
        DomainMap.from_config(
            {
                "domains": {"bots": {"server": "s"}},
                "servers": {"s": {"base_url": "backend.sample.com"}},
            },
            variables={},
        )


def test_the_host_requirement_applies_through_config_too() -> None:
    with pytest.raises(ValueError, match="must name a host"):
        DomainMap.from_config(
            {
                "domains": {"engine": {"server": "s"}},
                "servers": {"s": {"base_url": "https:///engine-proxy"}},
            },
            variables={},
        )


def test_an_http_only_domain_is_still_reachable_as_a_socket_origin() -> None:
    """Nothing about the server decides its planes — the domain does."""
    dm = DomainMap.from_config(
        {
            "domains": {"bots": {"server": "s"}},
            "servers": {"s": {"base_url": "https://backend.sample.com"}},
        },
        variables={},
    )
    bots = dm.http_domain_for("/openapi/v1/bots")
    assert bots is not None
    assert not bots.serves_websocket
    assert bots.server.websocket_base_url == "wss://backend.sample.com"


def test_unknown_server_reference_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown server"):
        DomainMap.from_config(
            {"domains": {"bots": {"server": "ghost"}}, "servers": {}},
            variables={},
        )


def test_expand_vars_resolves_from_dict() -> None:
    result = _expand_vars("${backend_server_url}", _VARS)
    assert result == "http://backend:8080"


def test_expand_vars_missing_key_yields_empty() -> None:
    result = _expand_vars("${missing_key}", {})
    assert result == ""


def test_expand_vars_no_placeholders_returns_unchanged() -> None:
    result = _expand_vars("http://plain.example.com", {})
    assert result == "http://plain.example.com"


def test_expand_vars_multiple_placeholders() -> None:
    result = _expand_vars("${backend_server_url}:${baas_server_url}", _VARS)
    assert result == "http://backend:8080:http://baas:9090"


def test_parse_servers_with_valid_placeholder() -> None:
    servers = _parse_servers({"backend": {"base_url": "${backend_server_url}"}}, _VARS)
    assert servers["backend"].name == "backend"
    assert servers["backend"].base_url == "http://backend:8080"


def test_parse_servers_hardcoded_url() -> None:
    servers = _parse_servers(
        {"backend": {"base_url": "http://hardcoded.example.com"}}, {}
    )
    assert servers["backend"].base_url == "http://hardcoded.example.com"


def test_parse_servers_empty_base_url_raises() -> None:
    with pytest.raises(ValueError, match="resolved to empty") as excinfo:
        _parse_servers({"backend": {"base_url": "${backend_server_url}"}}, {})
    assert "backend" in str(excinfo.value)
    assert "backend_server_url" in str(excinfo.value)


def test_parse_servers_one_empty_one_valid_raises_for_empty() -> None:
    with pytest.raises(ValueError, match="baas"):
        _parse_servers(
            {
                "backend": {"base_url": "http://ok.example.com"},
                "baas": {"base_url": "${baas_server_url}"},
            },
            {},
        )


def test_parse_servers_empty_string_base_url_raises() -> None:
    with pytest.raises(ValueError, match="resolved to empty"):
        _parse_servers({"backend": {"base_url": ""}}, {})


def test_from_config_with_variables_resolves_all() -> None:
    dm = DomainMap.from_config(
        {
            "domains": {
                "bots": {"server": "backend"},
                "runs": {"server": "baas"},
            },
            "servers": {
                "backend": {"base_url": "${backend_server_url}"},
                "baas": {"base_url": "${baas_server_url}"},
            },
        },
        variables=_VARS,
    )
    bots = _server(dm.http_domain_for("/openapi/v1/bots"))
    assert bots is not None
    assert bots.base_url == "http://backend:8080"
    runs = _server(dm.http_domain_for("/openapi/v1/runs"))
    assert runs is not None
    assert runs.base_url == "http://baas:9090"


def test_from_config_missing_variable_raises() -> None:
    with pytest.raises(ValueError, match="backend_server_url"):
        DomainMap.from_config(
            {
                "domains": {"bots": {"server": "backend"}},
                "servers": {"backend": {"base_url": "${backend_server_url}"}},
            },
            variables={"baas_server_url": "http://baas:9090"},
        )


def test_from_yaml_loads_upstreams(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "user_config:\n"
        "  upstreams:\n"
        "    base_path: /openapi/v1\n"
        "    domains:\n"
        "      bots:\n"
        "        server: backend\n"
        "        schema:\n"
        "          source: file\n"
        "          path: schemas/bots.openapi.json\n"
        "    servers:\n"
        "      backend:\n"
        "        base_url: http://backend:8080\n"
    )
    dm = DomainMap.from_yaml(cfg, variables={})
    assert dm.http_domain_for("/openapi/v1/bots/123") is not None


def test_from_yaml_non_dict_root_uses_empty(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text("- just a list")
    dm = DomainMap.from_yaml(cfg, variables={})
    assert dm.http_domain_for("/openapi/v1/bots/123") is None


def test_from_yaml_user_config_not_dict_uses_empty(tmp_path) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text("user_config: not-a-dict\n")
    dm = DomainMap.from_yaml(cfg, variables={})
    assert dm.http_domain_for("/openapi/v1/bots/123") is None


# ── unknown configuration keys ───────────────────────────────────────────────


def test_a_misspelled_domain_key_is_refused_not_ignored() -> None:
    """The reason this matters is which *plane* the default exposes.

    `protcols` is dropped, `protocols` reads as unset, and the domain falls back
    to `[http]`. For the shipped `engine` domain that silently converts a
    socket-only route into an HTTP forwarding one — on the single prefix
    `route_security` exempts from authentication, and on the plane that has no
    traversal guard. A one-character typo, and no error anywhere.
    """
    with pytest.raises(ValueError, match=r"unknown key\(s\) \['protcols'\]"):
        DomainMap.from_config(
            {
                "domains": {"engine": {"server": "s", "protcols": ["websocket"]}},
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            {
                "domains": {"e": {"server": "s", "schema": {"sourcee": "file"}}},
                "servers": {"s": {"base_url": "http://s"}},
            },
            r"unknown key\(s\) \['sourcee'\]",
        ),
        (
            {
                "domains": {
                    "e": {
                        "server": "s",
                        "rewrite": {"from": "/openapi/v1/e", "too": "/x"},
                    }
                },
                "servers": {"s": {"base_url": "http://s"}},
            },
            r"unknown key\(s\) \['too'\]",
        ),
        (
            {
                "domains": {"e": {"server": "s"}},
                "servers": {"s": {"base_urll": "http://s"}},
            },
            r"unknown key\(s\) \['base_urll'\]",
        ),
    ],
)
def test_unknown_keys_are_refused_in_every_upstreams_block(
    config: dict, expected: str
) -> None:
    """Not only the domain block — the same silent-default trap applies to each."""
    with pytest.raises(ValueError, match=expected):
        DomainMap.from_config(config, variables={})


def test_every_key_the_shipped_config_uses_is_recognised() -> None:
    """Guards the tightening itself: no shipped key may trip the new check.

    `test_shipped_config_loads` covers `bots`; this one pins the socket domain,
    whose `match`, `protocols` and `rewrite` keys are exactly what the check is
    about.
    """
    raw = yaml.safe_load(_CONFIG.read_text())
    domain_map = DomainMap.from_config(raw["user_config"]["upstreams"], variables=_VARS)
    socket = domain_map.websocket_domain_for("/openapi/v1/bots/messages/ws/t/api/ws")
    assert socket is not None
    assert socket.serves_websocket and not socket.serves_http
    assert socket.mount_prefix == "/openapi/v1/bots/messages/ws"
    # The claim stops at the socket's own segment: a sibling under `messages` is
    # not this domain's, on either plane.
    assert domain_map.websocket_domain_for("/openapi/v1/bots/messages/t/api") is None


# ── path patterns: match first, then most specific ───────────────────────────


def _pattern_map() -> DomainMap:
    """The shipped shape: a socket prefix nested inside an HTTP domain."""
    return DomainMap.from_config(
        {
            "domains": {
                "bots": {"server": "backend"},
                "bots-messages-ws": {
                    "match": "/openapi/v1/bots/messages/**",
                    "server": "proxy",
                    "protocols": ["websocket"],
                    "rewrite": {
                        "from": "/openapi/v1/bots/messages",
                        "to": "/proxypass",
                    },
                },
            },
            "servers": {
                "backend": {"base_url": "http://backend:8080"},
                "proxy": {"base_url": "https://proxy:20003"},
            },
        },
        variables={},
    )


def test_a_domain_without_a_match_claims_its_own_name() -> None:
    """The implicit pattern is exactly the leading-segment routing it replaces."""
    bots = _map().domains["bots"]
    assert bots.pattern.segments == ("openapi", "v1", "bots", "**")
    assert bots.mount_prefix == "/openapi/v1/bots"


def test_the_most_specific_matching_pattern_wins() -> None:
    domain = _pattern_map().websocket_domain_for("/openapi/v1/bots/messages/T/api/ws")
    assert domain is not None
    assert domain.name == "bots-messages-ws"


def test_an_http_request_under_a_socket_prefix_reaches_the_broader_domain() -> None:
    """The trap this design exists to avoid.

    ``messages`` is reserved for a future *HTTP* endpoint. Were the plane checked
    after ranking rather than before, the socket domain would win the path, fail
    the check, and the endpoint would be unreachable the day it is added — the
    prefix would have eaten exactly the thing it was reserved for.
    """
    domain = _pattern_map().http_domain_for("/openapi/v1/bots/messages/some-bot")
    assert domain is not None
    assert domain.name == "bots"
    assert domain.server.name == "backend"


def test_a_socket_prefix_is_not_relayed_outside_its_own_pattern() -> None:
    """The narrower claim does not spill onto the rest of the parent prefix."""
    assert _pattern_map().websocket_domain_for("/openapi/v1/bots/abc") is None


def test_resolution_does_not_retry_after_a_plane_mismatch() -> None:
    """A domain refusing the plane must not hand the request to another.

    With no HTTP domain configured at all, an HTTP request under the socket
    prefix has nowhere to fall — it resolves to ``None`` rather than being
    served by the socket domain's upstream over the wrong plane.
    """
    dm = DomainMap.from_config(
        {
            "domains": {
                "bots-messages-ws": {
                    "match": "/openapi/v1/bots/messages/**",
                    "server": "proxy",
                    "protocols": ["websocket"],
                }
            },
            "servers": {"proxy": {"base_url": "https://proxy:20003"}},
        },
        variables={},
    )
    assert dm.http_domain_for("/openapi/v1/bots/messages/x") is None


def test_a_pattern_serves_its_own_bare_prefix() -> None:
    """`**` matches no remaining segments, so the prefix itself is claimed."""
    domain = _pattern_map().websocket_domain_for("/openapi/v1/bots/messages")
    assert domain is not None
    assert domain.name == "bots-messages-ws"


# ── refusing a pattern that would widen the gateway ──────────────────────────


def _match_map(pattern: str, **extra: object) -> DomainMap:
    return DomainMap.from_config(
        {
            "domains": {"x": {"match": pattern, "server": "s", **extra}},
            "servers": {"s": {"base_url": "http://s"}},
        },
        variables={},
    )


@pytest.mark.parametrize("pattern", ["/**", "/openapi/**", "/openapi/v1/**"])
def test_an_over_broad_pattern_is_refused_at_boot(pattern: str) -> None:
    """Before patterns, an open proxy could not be typed; now it must be refused.

    A domain used to *be* its leading segment, so an unknown segment resolved to
    ``None`` and the caller denied. `match: /**` is an open proxy into that
    domain's upstream, so the invariant is now enforced rather than structural.
    """
    with pytest.raises(ValueError, match="too broad"):
        _match_map(pattern)


@pytest.mark.parametrize(
    "pattern", ["/openapi/v1/{tenant}/**", "/openapi/v1/**/bots/**"]
)
def test_a_pattern_that_pins_no_prefix_is_refused(pattern: str) -> None:
    """A leading parameter matches every domain's traffic while looking specific,
    and neither it nor an inner glob yields a prefix to mount a route on."""
    with pytest.raises(ValueError, match="literal segments"):
        _match_map(pattern)


def test_a_pattern_must_declare_its_glob() -> None:
    """Required rather than inferred: the width of the claim stays visible."""
    with pytest.raises(ValueError, match=r"must end in"):
        _match_map("/openapi/v1/bots")


def test_two_domains_claiming_one_pattern_and_plane_are_refused() -> None:
    with pytest.raises(ValueError, match="undefined"):
        DomainMap.from_config(
            {
                "domains": {
                    "a": {"match": "/openapi/v1/z/**", "server": "s"},
                    "b": {"match": "/openapi/v1/z/**", "server": "s"},
                },
                "servers": {"s": {"base_url": "http://s"}},
            },
            variables={},
        )


def test_one_pattern_may_be_declared_once_per_plane() -> None:
    """Two deliberate per-plane declarations are the supported way to serve both.

    This is what keeps the design honest: reserving a prefix for one plane never
    forecloses the other, it only requires saying so.
    """
    dm = DomainMap.from_config(
        {
            "domains": {
                "z-http": {"match": "/openapi/v1/z/**", "server": "s"},
                "z-ws": {
                    "match": "/openapi/v1/z/**",
                    "server": "s",
                    "protocols": ["websocket"],
                },
            },
            "servers": {"s": {"base_url": "http://s"}},
        },
        variables={},
    )
    http = dm.http_domain_for("/openapi/v1/z/x")
    ws = dm.websocket_domain_for("/openapi/v1/z/x")
    assert http is not None and http.name == "z-http"
    assert ws is not None and ws.name == "z-ws"


def test_the_rewrite_anchor_follows_the_declared_pattern() -> None:
    """A nested domain's rewrite anchors at its own prefix, not at its name."""
    domain = _pattern_map().websocket_domain_for("/openapi/v1/bots/messages/T/api/ws")
    assert domain is not None
    assert domain.rewrite is not None
    assert domain.upstream_path("/openapi/v1/bots/messages/T/api/ws") == (
        "/proxypass/T/api/ws"
    )


def test_a_rewrite_anchored_off_the_declared_pattern_is_refused() -> None:
    """The anchor is the pattern's literal head — not `base_path` + the name."""
    with pytest.raises(ValueError, match="can never match"):
        _match_map(
            "/openapi/v1/bots/messages/**",
            rewrite={"from": "/openapi/v1/x", "to": "/proxypass"},
        )
