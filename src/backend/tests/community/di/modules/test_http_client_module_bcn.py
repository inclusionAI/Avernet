"""BCN HttpClient host selection."""
from __future__ import annotations

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules.http_client_module import HttpClientModule
from agentclaw.community.plugin_api.http_client import QUALIFIER_BAAS, QUALIFIER_BCN
from agentclaw.community.plugins.http_client import HttpxClient


def test_bcn_http_client_prod_uses_base_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentclaw.community.di.modules.http_client_module.get_current_env",
        lambda: "prod",
    )
    client = HttpClientModule().bcn_http_client(
        cfg.BcnConfig(
            base_url="https://bcn.example.test",
            base_url_pre="https://bcn-pre.example.test",
        ),
        cfg.HttpClientPoolConfig(),
    )

    assert isinstance(client, HttpxClient)
    assert client._base_url == "https://bcn.example.test"


def test_bcn_http_client_pre_uses_base_url_pre(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentclaw.community.di.modules.http_client_module.get_current_env",
        lambda: "pre",
    )
    client = HttpClientModule().bcn_http_client(
        cfg.BcnConfig(
            base_url="https://bcn.example.test",
            base_url_pre="https://bcn-pre.example.test",
        ),
        cfg.HttpClientPoolConfig(),
    )

    assert isinstance(client, HttpxClient)
    assert client._base_url == "https://bcn-pre.example.test"


# ── transport policy resolution ──────────────────────────────────────────────


def _bcn() -> cfg.BcnConfig:
    return cfg.BcnConfig(
        base_url="https://bcn.example.test",
        base_url_pre="https://bcn-pre.example.test",
    )


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
