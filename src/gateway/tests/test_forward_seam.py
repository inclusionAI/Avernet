"""Regression test for the forwarder identity seam (auth design §7.1).

Components must never trust a bare Principal header. Until the PrincipalSigner
workstream lands, the seam must NOT inject any identity-bearing header into the
forwarded request. This test pins that invariant so a future signing swap-in is
the only thing that changes it.
"""

from __future__ import annotations

from gateway.community.adapters.web._forward import _attach_identities
from gateway.community.spi.forwarder import ForwardRequest


def test_seam_does_not_inject_identity_headers() -> None:
    forward = ForwardRequest(
        method="GET",
        url="http://up/x",
        headers={"x-existing": "keep"},
        content=b"",
    )
    # A non-empty identity set must still not leak any header.
    out = _attach_identities(forward, {"user": object()})
    assert out.headers == {"x-existing": "keep"}
    assert out.method == "GET"
    assert out.url == "http://up/x"


def test_seam_returns_forward_unchanged_with_empty_identities() -> None:
    forward = ForwardRequest(method="GET", url="http://up/x", headers={}, content=b"")
    out = _attach_identities(forward, {})
    assert out.headers == {}
    assert out.method == "GET"
    assert out.url == "http://up/x"
