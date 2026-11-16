"""Tests for configurable HttpxForwarder timeouts."""

from __future__ import annotations

import httpx

from gateway.community.config._models import ForwarderPluginConfig
from gateway.community.plugins.forwarder.httpx import HttpxForwarder


def test_default_config_matches_hardcoded_values() -> None:
    """Without an explicit config, timeouts match the previous hard-coded defaults."""
    forwarder = HttpxForwarder()
    client = forwarder._get_client()
    timeout = client.timeout
    assert timeout.connect == 5.0
    assert timeout.read == 120.0
    assert timeout.write == 5.0
    assert timeout.pool == 5.0


def test_custom_config_overrides_timeouts() -> None:
    """A ForwarderPluginConfig is applied to the owned httpx client."""
    cfg = ForwarderPluginConfig(
        connect_timeout=10.0,
        read_timeout=300.0,
        write_timeout=15.0,
        pool_timeout=10.0,
    )
    forwarder = HttpxForwarder(config=cfg)
    client = forwarder._get_client()
    timeout = client.timeout
    assert timeout.connect == 10.0
    assert timeout.read == 300.0
    assert timeout.write == 15.0
    assert timeout.pool == 10.0


def test_injected_client_ignores_config() -> None:
    """When a client is injected (e.g. in tests), config does not affect it."""
    injected = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0)
    )
    cfg = ForwarderPluginConfig(read_timeout=999.0)
    forwarder = HttpxForwarder(client=injected, config=cfg)
    # _get_client returns the injected one as-is
    assert forwarder._get_client() is injected
    assert injected.timeout.read == 2.0


def test_config_model_defaults() -> None:
    """ForwarderPluginConfig defaults match the SPI contract's historical values."""
    cfg = ForwarderPluginConfig()
    assert cfg.connect_timeout == 5.0
    assert cfg.read_timeout == 120.0
    assert cfg.write_timeout == 5.0
    assert cfg.pool_timeout == 5.0


def test_config_model_extra_allowed() -> None:
    """ForwarderPluginConfig allows extra fields for forward compatibility."""
    cfg = ForwarderPluginConfig(read_timeout=300.0, future_field="ignored")
    assert cfg.read_timeout == 300.0
