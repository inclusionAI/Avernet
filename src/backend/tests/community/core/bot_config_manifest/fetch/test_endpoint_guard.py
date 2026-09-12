"""The credential endpoint guard's address matrix (W2, #1470).

``endpoint_refusal`` is what the credential surface runs before it will
persist an object-store endpoint, so these are write-time refusals: the
platform will connect to whatever endpoint a stored credential names, and
this is the last point at which refusing costs nobody an apply.

DNS is an injected resolver throughout — the matrix runs with no network and
can state the address semantics a real resolver would hide.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.fetch.endpoint_guard import (
    endpoint_refusal,
)

HOST = "store.example"
ENDPOINT = f"https://{HOST}/"
PUBLIC_IP = "1.2.3.4"


@pytest.mark.parametrize("private_ip", [
    "127.0.0.1", "10.17.0.9", "192.168.1.4", "172.20.10.3",
    "169.254.169.254", "fd00::1", "fc00::2", "fe80::1", "::1",
    "224.0.0.1", "240.0.0.8",
])
def test_non_global_addresses_are_refused(private_ip):
    refusal = endpoint_refusal(ENDPOINT, resolver=lambda host: [private_ip])
    assert refusal is not None
    assert "non-public" in refusal


def test_one_private_among_public_answers_refuses_the_host():
    # All resolved addresses are validated, not a lucky one.
    refusal = endpoint_refusal(
        ENDPOINT, resolver=lambda host: [PUBLIC_IP, "127.0.0.1"]
    )
    assert refusal is not None
    assert "non-public" in refusal


def test_a_literal_private_address_is_refused_without_any_dns():
    # The metadata service by IP: no name to resolve, and still refused.
    def no_dns(host):
        raise AssertionError("a literal address must not need a resolver")

    assert endpoint_refusal("http://169.254.169.254/", resolver=no_dns) is not None


def test_an_unresolvable_host_is_stored_not_crashed():
    """Divergence from the fetch-time rule, and it is deliberate.

    A resolution failure at fetch time is a statement of fact — the read
    cannot happen. Here it would be a prediction: the pod storing a
    credential is not the pod that later reads with it, and split-horizon
    DNS, a private zone or a minute's outage are all ordinary reasons a good
    endpoint does not answer here and now. So it is logged, not refused —
    and, load-bearing either way, the raising resolver does not escape.
    """
    def no_such(host):
        raise OSError("no DNS")

    assert endpoint_refusal(ENDPOINT, resolver=no_such) is None


def test_the_transport_allowlist_exempts_address_validation():
    # An internal object store: exact-host exemption, declared by the
    # deployment through user_config.bot_config_manifest.
    internal = "https://store.internal/"
    assert endpoint_refusal(
        internal, resolver=lambda host: ["10.0.0.52"]
    ) is not None
    assert endpoint_refusal(
        internal,
        allow_hosts=("store.internal",),
        resolver=lambda host: ["10.0.0.52"],
    ) is None


def test_the_allowlist_does_not_cover_other_hosts():
    refusal = endpoint_refusal(
        ENDPOINT,
        allow_hosts=("store.internal",),
        resolver=lambda host: ["10.0.0.52"],
    )
    assert refusal is not None
