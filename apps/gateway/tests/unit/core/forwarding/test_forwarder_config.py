"""Tests for configurable HttpxForwarder timeouts."""

from __future__ import annotations

import httpx

from gateway.community.plugins.forwarder.httpx import HttpxForwarder


def test_default_timeouts_match_hardcoded_values() -> None:
    """Without explicit timeouts, values match the previous hard-coded defaults."""
    forwarder = HttpxForwarder()
    client = forwarder._get_client()
    timeout = client.timeout
    assert timeout.connect == 5.0
    assert timeout.read == 120.0
    assert timeout.write == 5.0
    assert timeout.pool == 5.0


def test_custom_timeouts_are_applied() -> None:
    """Keyword-only timeout arguments are applied to the owned httpx client."""
    forwarder = HttpxForwarder(
        connect_timeout=10.0,
        read_timeout=300.0,
        write_timeout=15.0,
        pool_timeout=10.0,
    )
    client = forwarder._get_client()
    timeout = client.timeout
    assert timeout.connect == 10.0
    assert timeout.read == 300.0
    assert timeout.write == 15.0
    assert timeout.pool == 10.0


def test_injected_client_ignores_timeout_args() -> None:
    """When a client is injected (e.g. in tests), timeout args do not affect it."""
    injected = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0)
    )
    forwarder = HttpxForwarder(client=injected, read_timeout=999.0)
    assert forwarder._get_client() is injected
    assert injected.timeout.read == 2.0


def test_partial_override() -> None:
    """Only overridden timeouts change; unspecified ones stay at defaults."""
    forwarder = HttpxForwarder(read_timeout=300.0)
    client = forwarder._get_client()
    timeout = client.timeout
    assert timeout.connect == 5.0
    assert timeout.read == 300.0
    assert timeout.write == 5.0
    assert timeout.pool == 5.0
