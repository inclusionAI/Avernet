"""Security-matrix tests for the guarded fetcher (W2, #1470).

Every refusal rule gets a negative test; the happy path pins the wire
contract itself — the request goes out addressed to the *validated IP*
while carrying the original Host header, and the streamed body comes back
with its sha256. The transport is a mock and DNS an injected resolver, so
the matrix runs with no network and can observe the address semantics a
real network would hide.

This file is the safety argument reviewers read: treat assertion verbs
here as load-bearing (``transport_not_hit``, digest-mismatch-raises) —
the #1470 acceptance lines live in these tests.
"""

from __future__ import annotations

import hashlib
import pathlib

import httpx
import pytest
import yaml

from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    AuthorizationPolicy,
    CredentialInjector,
    FetchFailedError,
    FetchRefusedError,
    FetchRequest,
    GuardedFetcher,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
    transport_allowlist_from_config,
)

BODY = b"kb-bytes" * 8
BODY_SHA = "sha256:" + hashlib.sha256(BODY).hexdigest()

HOST = "content.example"
PUBLIC_IP = "1.2.3.4"
HTTPS_URL = f"https://{HOST}/team/content.bin"


def _resolver_public(host: str) -> list[str]:
    return [PUBLIC_IP]


class _Requests:
    """Transport bookkeeping: what actually went on the wire, and when."""

    def __init__(self, handler):
        self.seen: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return self._handler(request)


def _fetch(url: str, *, transport, resolver=None, digest: str | None = None,
           injector: CredentialInjector | None = None,
           policy: AuthorizationPolicy | None = None, category: str = "resources_file",
           allow: tuple[str, ...] = ()):
    # The seam is a BaseTransport, not a Client: the fetcher owns client
    # construction (one per hop), which is itself the no-reuse guarantee.
    # ``allow`` is the constructor-injected deployment allowlist — the
    # production path reads it from application.yaml (see the config tests
    # below); env vars play no part.
    fetcher = GuardedFetcher(
        resolver=resolver or _resolver_public,
        _transport=httpx.MockTransport(transport),
        transport_allowlist=allow,
    )
    return fetcher.fetch(
        FetchRequest(
            url=url,
            expected_digest=digest,
            injector=injector,
            policy=policy,
            category=category,
        )
    )


def _ok_handler(body: bytes = BODY, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=body,
            headers={"content-type": "application/octet-stream"},
        )
    return handler


def _redirect_handler(hops: list[str]):
    """Serve ``301`` to each URL in ``hops`` in order, then 200 + BODY."""
    it = iter(hops)

    def handler(request: httpx.Request) -> httpx.Response:
        target = next(it, None)
        if target is None:
            return httpx.Response(200, content=BODY)
        return httpx.Response(301, headers={"location": target})
    return handler


# --- happy path: the wire contract -----------------------------------------


def test_happy_path_pins_the_validated_ip_and_preserves_host():
    transport = _Requests(_ok_handler())
    obj = _fetch(HTTPS_URL, transport=transport)
    assert obj.bytes == BODY
    assert obj.sha256 == BODY_SHA
    assert obj.url == HTTPS_URL
    assert obj.content_type == "application/octet-stream"
    # Pinned connection: the request went to the resolved IP, not the name.
    assert str(transport.seen[0].url.host) == PUBLIC_IP
    # …and the original identity rode along as Host (SNI is the same value).
    assert transport.seen[0].headers["host"] == HOST
    assert len(transport.seen) == 1


def test_happy_path_with_matching_digest():
    transport = _Requests(_ok_handler())
    obj = _fetch(HTTPS_URL, transport=transport, digest=BODY_SHA)
    assert obj.sha256 == BODY_SHA


def test_multiple_public_resolutions_pin_the_lowest_address():
    # resolver 顺序打乱;数值最小者(1.2.3.4)被钉住,证明与解析顺序无关。
    resolver = lambda host: ["8.8.4.4", PUBLIC_IP]  # noqa: E731
    transport = _Requests(_ok_handler())
    _fetch(HTTPS_URL, transport=transport, resolver=resolver)
    assert str(transport.seen[0].url.host) == PUBLIC_IP


# --- scheme and URL shape ---------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://content.example/x.bin",
    "ftp://content.example/x.bin",
    "https://user:pass@content.example/x.bin",
    "//content.example/x.bin",
    "https:///x.bin",
])
def test_unsafe_schemes_and_shapes_are_refused_before_the_wire(url):
    transport = _Requests(_ok_handler())
    with pytest.raises(FetchRefusedError):
        _fetch(url, transport=transport)
    assert transport.seen == []


def test_http_is_allowed_only_for_a_deployment_allowlisted_host():
    resolver = lambda host: [PUBLIC_IP]  # noqa: E731
    transport = _Requests(_ok_handler())
    obj = _fetch("http://mirror.example/x.bin", transport=transport,
                 resolver=resolver, allow=("mirror.example",))
    assert obj.bytes == BODY


def test_the_http_allowlist_does_not_cover_other_hosts():
    transport = _Requests(_ok_handler())
    with pytest.raises(FetchRefusedError):
        _fetch("http://other.example/x.bin", transport=transport,
               allow=("mirror.example",))
    assert transport.seen == []


# --- address validation ------------------------------------------------------


@pytest.mark.parametrize("private_ip", [
    "127.0.0.1", "10.17.0.9", "192.168.1.4", "172.20.10.3",
    "169.254.169.254", "fd00::1", "fc00::2", "fe80::1", "::1",
    "224.0.0.1", "240.0.0.8",
])
def test_non_global_addresses_are_refused_before_the_wire(private_ip):
    transport = _Requests(_ok_handler())
    with pytest.raises(FetchRefusedError, match="non-public"):
        _fetch(HTTPS_URL, transport=transport, resolver=lambda host: [private_ip])
    assert transport.seen == []


def test_one_private_among_public_answers_refuses_the_host():
    # All resolved addresses are validated, not a lucky one.
    resolver = lambda host: [PUBLIC_IP, "127.0.0.1"]  # noqa: E731
    transport = _Requests(_ok_handler())
    with pytest.raises(FetchRefusedError):
        _fetch(HTTPS_URL, transport=transport, resolver=resolver)
    assert transport.seen == []


def test_the_transport_allowlist_exempts_address_validation():
    # A corporate mirror on an internal net: exact-host exemption.
    transport = _Requests(_ok_handler())
    obj = _fetch(HTTPS_URL.replace(HOST, "mirror.internal"),
                 transport=transport, resolver=lambda host: ["10.0.0.52"],
                 allow=("mirror.internal",))
    assert obj.bytes == BODY


def test_unresolvable_host_is_refused_not_crashed():
    def no_such(host):
        raise OSError("no DNS")
    with pytest.raises(FetchRefusedError, match="resolve"):
        _fetch(HTTPS_URL, transport=_Requests(_ok_handler()), resolver=no_such)


# --- the deployment allowlist is config-borne, not environment ---------------


def test_the_allowlist_parses_from_the_user_config_block():
    # The yaml shape shipped in configs/application.yaml:
    # user_config.bot_config_manifest.fetch_transport_allowlist.
    allow = transport_allowlist_from_config({
        "bot_config_manifest": {"fetch_transport_allowlist": [
            "mirror.example", " mirror.internal ", "", "mirror.example",
        ]},
    })
    assert allow == frozenset({"mirror.example", "mirror.internal"})


def test_an_absent_block_or_key_means_no_exception():
    assert transport_allowlist_from_config({}) == frozenset()
    assert transport_allowlist_from_config(
        {"bot_config_manifest": None}
    ) == frozenset()
    assert transport_allowlist_from_config(
        {"bot_config_manifest": {"fetch_transport_allowlist": None}}
    ) == frozenset()


@pytest.mark.parametrize("settings", [
    {"bot_config_manifest": "mirror.example"},
    {"bot_config_manifest": {"fetch_transport_allowlist": "mirror.example"}},
    {"bot_config_manifest": {"fetch_transport_allowlist": ["mirror.example", 42]}},
])
def test_a_malformed_block_is_a_configuration_error(settings):
    # A typo in the yaml block must fail its reader loudly, never
    # silently fetch strictly (or worse: silently widen).
    with pytest.raises(ValueError):
        transport_allowlist_from_config(settings)


_SHIPPED_APP_YAML = (
    pathlib.Path(__file__).resolve().parents[5]
    / "src" / "agentclaw" / "community" / "configs" / "application.yaml"
)


def test_the_shipped_yaml_carries_a_neutral_empty_allowlist():
    # The knob must exist and must ship empty: whatever a deployment
    # exempts appears in its own overlay diff, never in community source.
    tree = yaml.safe_load(_SHIPPED_APP_YAML.read_text(encoding="utf-8"))
    settings = tree["user_config"]
    assert settings["bot_config_manifest"]["fetch_transport_allowlist"] == []
    assert transport_allowlist_from_config(settings) == frozenset()


# --- redirects ---------------------------------------------------------------


def test_each_redirect_hop_is_revalidated_and_the_budget_bites():
    # First hop public, redirect target private: refused at the hop.
    transport = _Requests(
        _redirect_handler(["https://intranet.example/x.bin"])
    )
    with pytest.raises(FetchRefusedError, match="non-public"):
        _fetch(HTTPS_URL, transport=transport,
               resolver=lambda host: [PUBLIC_IP] if host == HOST else ["10.9.9.9"])
    assert len(transport.seen) == 1  # the redirect was never fetched


def test_redirect_to_http_is_refused():
    transport = _Requests(_redirect_handler(["http://content.example/x.bin"]))
    with pytest.raises(FetchRefusedError):
        _fetch(HTTPS_URL, transport=transport)
    assert len(transport.seen) == 1


def test_a_legitimate_redirect_chain_is_followed():
    second = "https://cdn.example/other.bin"
    transport = _Requests(_redirect_handler([second]))
    resolver = lambda host: [PUBLIC_IP]  # noqa: E731
    obj = _fetch(HTTPS_URL, transport=transport, resolver=resolver)
    assert obj.bytes == BODY
    assert obj.url == second  # final URL recorded, not the first hop
    assert len(transport.seen) == 2


def test_the_hop_cap_is_enforced():
    hop = f"https://{HOST}/h"
    transport = _Requests(_redirect_handler([hop] * 6))
    resolver = lambda host: [PUBLIC_IP]  # noqa: E731
    with pytest.raises(FetchRefusedError, match="redirect"):
        _fetch(HTTPS_URL, transport=transport, resolver=resolver)
    assert len(transport.seen) <= 6


# --- sizing and hashing -------------------------------------------------------


def test_the_streamed_byte_cap_bites_even_when_content_length_lies():
    # Declared small, actually big — the cap counts bytes as they arrive.
    # identity 桶(1 MiB)使测试无需构造 100 MiB 内存。
    big = b"x" * (FETCH_ENTRY_LIMITS["identity"] + 17)

    def liar(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big, headers={"content-length": "16"})
    transport = _Requests(liar)
    with pytest.raises(FetchRefusedError, match="exceeds"):
        _fetch(HTTPS_URL, transport=transport, category="identity")
    assert len(transport.seen) == 1


def test_the_per_category_cap_varies():
    small = b"x" * (FETCH_ENTRY_LIMITS["identity"] + 1)
    transport = _Requests(_ok_handler(body=small))
    # Same bytes, identity bucket: refusal; generic bucket: pass.
    with pytest.raises(FetchRefusedError, match="exceeds"):
        _fetch(HTTPS_URL, transport=transport, category="identity")
    assert transport.seen and transport.seen[-1] is not None
    # 同样字节交大桶:通过。
    transport2 = _Requests(_ok_handler(body=small))
    obj = _fetch(HTTPS_URL, transport=transport2, category="resources_file")
    assert obj.size_bytes == len(small)


# --- digest receipt -----------------------------------------------------------


def test_a_digest_mismatch_is_a_failure_not_a_corrupted_success():
    transport = _Requests(_ok_handler())
    bogus = "sha256:" + "00" * 32
    with pytest.raises(FetchFailedError, match="digest"):
        _fetch(HTTPS_URL, transport=transport, digest=bogus)


def test_a_malformed_declared_digest_is_refused():
    transport = _Requests(_ok_handler())
    with pytest.raises(FetchRefusedError):
        _fetch(HTTPS_URL, transport=transport, digest="md5:zz")
    assert transport.seen == []


def test_a_digest_with_a_trailing_newline_is_refused_as_malformed():
    # `$` also matches immediately before a trailing newline, so this value
    # previously passed the vocabulary check and failed downstream as a
    # FETCH failure ("digest mismatch") — the wrong taxonomy for malformed
    # config, leaked a '\n' into a mismatch message, and on the store side
    # masqueraded as a missing address. \Z closes all three.
    transport = _Requests(_ok_handler())
    pinned = "sha256:" + "a" * 64 + "\n"
    with pytest.raises(FetchRefusedError):
        _fetch(HTTPS_URL, transport=transport, digest=pinned)
    assert transport.seen == []


# --- credential injection (Protocol declared; W3 binds) ------------------------


def _token_injector():
    class Injector:
        def headers_for(self, url):
            return {"private-token": "corp-git"}

        def reauthorize(self, url):
            return None
    return Injector()


def test_injector_headers_ride_the_request():
    transport = _Requests(_ok_handler())
    _fetch(HTTPS_URL, transport=transport, injector=_token_injector())
    assert transport.seen[0].headers["private-token"] == "corp-git"


def test_the_policy_sees_every_hop_and_crossing_it_refuses():
    class OnlyFirstHop:
        def reauthorize(self, url):
            if str(url.host) == "cdn.example":
                raise FetchRefusedError("leaves authorized prefixes")
    transport = _Requests(_redirect_handler(["https://cdn.example/x.bin"]))
    # Name the injector/policy seam: policy is consulted per hop with the
    # about-to-be-fetched URL.
    with pytest.raises(FetchRefusedError, match="prefixes"):
        _fetch(HTTPS_URL, transport=transport,
               resolver=lambda host: [PUBLIC_IP], policy=OnlyFirstHop())


def test_non_2xx_terminal_status_is_a_fetch_failure():
    transport = _Requests(_ok_handler(status=503))
    with pytest.raises(FetchFailedError):
        _fetch(HTTPS_URL, transport=transport)


# --- no cross-request/cross-hop connection reuse (终审 finding 1) ------------


def test_the_resolver_runs_again_on_every_fetch():
    """一个 fetcher 服务多个条目时,每次都重新解析+重校验——解析结果
    绝不从上一条目继承(Per-hop client 决定了连接也绝不复用)。"""
    calls: list[str] = []

    def counting(host: str) -> list[str]:
        calls.append(host)
        return [PUBLIC_IP]

    transport = _Requests(_ok_handler())
    _fetch(HTTPS_URL, transport=transport, resolver=counting)
    _fetch(HTTPS_URL, transport=transport, resolver=counting)
    assert calls == [HOST, HOST]
    assert len(transport.seen) == 2


def test_connection_errors_become_fetch_failures():
    """传输层失败归化为 FetchFailedError——W4 的条目 分类依赖这个类型。"""

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")
    with pytest.raises(FetchFailedError, match="transport"):
        _fetch(HTTPS_URL, transport=_Requests(dead))


def test_a_non_default_port_rides_the_host_header():
    transport = _Requests(_ok_handler())
    _fetch(f"https://{HOST}:8443/team/content.bin", transport=transport)
    assert transport.seen[0].headers["host"] == f"{HOST}:8443"


def test_a_default_port_stays_out_of_the_host_header():
    transport = _Requests(_ok_handler())
    _fetch(f"https://{HOST}:443/team/content.bin", transport=transport)
    assert transport.seen[0].headers["host"] == HOST
