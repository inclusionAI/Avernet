"""``ConfigModule.http_client_pool`` — transport policy + override resolution.

The provider is where a sparse ``overrides`` entry becomes a total policy: each
override is built *starting from the resolved shared defaults*, so naming one
key inherits the rest rather than falling back to the dataclass. That is the
behaviour ``HttpClientPoolConfig.for_qualifier`` relies on to return a whole
policy without merging at the call site, and it is what these pin.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules.config_module import ConfigModule


@pytest.fixture
def resolve():
    """Resolve ``http_client`` from a given ``user_config`` block."""

    def _resolve(block: dict | None) -> cfg.HttpClientPoolConfig:
        user_config = {} if block is None else {"http_client": block}
        with patch(
            "agentclaw.community.di.modules.config_module._user_config",
            return_value=user_config,
        ):
            return ConfigModule().http_client_pool()

    return _resolve


def test_missing_block_yields_dataclass_defaults_for_every_binding(resolve):
    """No config ⇒ nothing to set: a deployment adopts pooling without touching
    its YAML."""
    conf = resolve(None)
    assert conf.defaults == cfg.HttpClientPoolPolicy()
    for qualifier in ("baas", "bcn", "general", "masa_agent_eval"):
        assert conf.for_qualifier(qualifier) == cfg.HttpClientPoolPolicy()


def test_top_level_keys_set_the_shared_defaults(resolve):
    conf = resolve(
        {
            "max_connections": 250,
            "max_keepalive_connections": 40,
            "keepalive_expiry": 12.5,
            "http2": True,
        }
    )
    assert conf.defaults == cfg.HttpClientPoolPolicy(
        max_connections=250,
        max_keepalive_connections=40,
        keepalive_expiry=12.5,
        http2=True,
    )
    # With no overrides, every binding sees those defaults.
    assert conf.for_qualifier("general") == conf.defaults


def test_partial_top_level_block_keeps_the_untouched_dataclass_defaults(resolve):
    conf = resolve({"http2": True})
    assert conf.defaults.http2 is True
    assert conf.defaults.max_connections == 100
    assert conf.defaults.keepalive_expiry == 5.0


def test_sparse_override_inherits_the_shared_defaults_not_the_dataclass(resolve):
    """The point of resolving overrides against ``defaults``: an override naming
    only ``http2`` keeps the *configured* ceilings, so a value left unset keeps
    tracking the shared defaults if those later change."""
    conf = resolve(
        {
            "max_connections": 250,
            "keepalive_expiry": 12.5,
            "overrides": {"baas": {"http2": True}},
        }
    )
    baas = conf.for_qualifier("baas")
    assert baas.http2 is True
    assert baas.max_connections == 250, "override must inherit the shared default"
    assert baas.keepalive_expiry == 12.5
    # Other bindings are untouched by that override.
    assert conf.for_qualifier("bcn").http2 is False
    assert conf.for_qualifier("bcn").max_connections == 250


def test_override_can_set_every_field(resolve):
    conf = resolve(
        {
            "overrides": {
                "general": {
                    "max_connections": 200,
                    "max_keepalive_connections": 50,
                    "keepalive_expiry": 3.0,
                    "http2": True,
                }
            }
        }
    )
    assert conf.for_qualifier("general") == cfg.HttpClientPoolPolicy(
        max_connections=200,
        max_keepalive_connections=50,
        keepalive_expiry=3.0,
        http2=True,
    )


def test_unlisted_qualifier_falls_back_to_defaults(resolve):
    """An unrecognised override key is inert by design — the provider has no
    binding list to validate against, and failing boot over a typo in a pool
    ceiling is the worse trade. ``HttpClientModule`` logs the resolved policy
    per binding, which is where such a typo surfaces."""
    conf = resolve({"max_connections": 77, "overrides": {"bass": {"http2": True}}})
    assert conf.for_qualifier("baas").http2 is False
    assert conf.for_qualifier("baas").max_connections == 77


def test_non_mapping_override_body_is_ignored(resolve):
    """A scalar where a mapping belongs must not crash boot."""
    conf = resolve({"overrides": {"baas": "true", "bcn": {"http2": True}}})
    assert conf.for_qualifier("baas") == conf.defaults
    assert conf.for_qualifier("bcn").http2 is True


def test_non_mapping_overrides_block_is_ignored(resolve):
    conf = resolve({"max_connections": 42, "overrides": ["baas"]})
    assert dict(conf.overrides) == {}
    assert conf.for_qualifier("baas").max_connections == 42


def test_values_are_coerced_from_yaml_scalars(resolve):
    """YAML can hand back strings; the policy fields are typed."""
    conf = resolve({"max_connections": "64", "keepalive_expiry": "2"})
    assert conf.defaults.max_connections == 64
    assert conf.defaults.keepalive_expiry == 2.0
    assert isinstance(conf.defaults.keepalive_expiry, float)
