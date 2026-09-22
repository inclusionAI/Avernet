"""BCN HttpClient host selection."""
from __future__ import annotations

import dataclasses

import pytest

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules.http_client_module import HttpClientModule
from agentclaw.community.plugin_api.http_client import QUALIFIER_BAAS, QUALIFIER_BCN
from agentclaw.community.plugins.http_client import HttpxClient


@pytest.mark.parametrize("env", ["prod", "pre", "dev"])
def test_bcn_http_client_uses_the_configured_base_url(monkeypatch, env) -> None:
    """The deployment overlay picked the host — the process env cannot change it."""
    monkeypatch.setenv("SERVER_ENV", env)
    client = HttpClientModule().bcn_http_client(
        cfg.BcnConfig(base_url="https://bcn.example.test"),
        cfg.HttpClientPoolConfig(),
    )

    assert isinstance(client, HttpxClient)
    assert client._base_url == "https://bcn.example.test"


def test_bcn_config_has_no_legacy_env_suffixed_fields() -> None:
    """Guard: the pre/prod field pairs are gone for good (SOFAPy 1.3 overlays)."""
    fields = {f.name for f in dataclasses.fields(cfg.BcnConfig)}
    assert fields.isdisjoint(
        {
            "base_url_pre",
            "provider_id_prod",
            "provider_id_pre",
            "provider_admin_token_prod",
            "provider_admin_token_pre",
        }
    )
    assert {"base_url", "provider_id", "provider_admin_token"} <= fields


# ── transport policy resolution ──────────────────────────────────────────────


def _bcn() -> cfg.BcnConfig:
    return cfg.BcnConfig(base_url="https://bcn.example.test")


def test_shared_defaults_reach_the_constructed_client() -> None:
    """A non-default shared policy must reach the client, limits and http2."""
    pool = cfg.HttpClientPoolConfig(
        defaults=cfg.HttpClientPoolPolicy(
            max_connections=31,
            max_keepalive_connections=7,
            keepalive_expiry=1.5,
            http2=True,
        )
    )
    client = HttpClientModule().bcn_http_client(_bcn(), pool)

    assert client._limits.max_connections == 31
    assert client._limits.max_keepalive_connections == 7
    assert client._limits.keepalive_expiry == 1.5
    assert client._http2 is True


def test_override_for_this_qualifier_wins() -> None:
    """``for_qualifier`` returns the override *whole* — every field of it, not
    a merge with ``defaults``. Values here are chosen to differ from BOTH the
    shared defaults and the dataclass defaults, so a regression cannot hide
    behind a coincidence."""
    pool = cfg.HttpClientPoolConfig(
        # defaults sets max_keepalive_connections to a NON-dataclass value that
        # the override leaves unset. A merging implementation would surface 44
        # here; whole-policy resolution surfaces the override's own 20.
        defaults=cfg.HttpClientPoolPolicy(
            max_connections=10, keepalive_expiry=9.0, max_keepalive_connections=44
        ),
        overrides={
            QUALIFIER_BCN: cfg.HttpClientPoolPolicy(
                max_connections=99, keepalive_expiry=3.5, http2=True
            )
        },
    )
    client = HttpClientModule().bcn_http_client(_bcn(), pool)
    assert client._limits.max_connections == 99
    assert client._limits.keepalive_expiry == 3.5
    assert client._http2 is True
    assert client._limits.max_keepalive_connections == 20, (
        "for_qualifier must return the override whole, not merged with defaults"
    )


def test_override_for_a_different_qualifier_is_ignored() -> None:
    """Each provider resolves its own qualifier — a `baas` override must not
    leak onto the `bcn` binding."""
    pool = cfg.HttpClientPoolConfig(
        defaults=cfg.HttpClientPoolPolicy(max_connections=10, http2=False),
        overrides={
            QUALIFIER_BAAS: cfg.HttpClientPoolPolicy(max_connections=99, http2=True)
        },
    )
    client = HttpClientModule().bcn_http_client(_bcn(), pool)
    assert client._limits.max_connections == 10
    assert client._http2 is False
