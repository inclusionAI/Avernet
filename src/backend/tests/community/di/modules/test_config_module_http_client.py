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


def test_unknown_qualifier_is_rejected(resolve):
    """A misspelled qualifier must fail loudly, not vanish.

    The valid set is closed, so `overrides.bass` cannot be honoured by any
    binding — leaving it inert would let an operator believe a ceiling had been
    raised while the binding quietly ran on the shared defaults. ci.enforce.md
    §E requires startup to fail early on invalid config, and
    `baas.deploy_runtime` already rejects an unknown value the same way.
    """
    with pytest.raises(ValueError, match="unknown http_client.overrides qualifier"):
        resolve({"max_connections": 77, "overrides": {"bass": {"http2": True}}})


def test_unknown_qualifier_error_names_the_offender_and_the_valid_set(resolve):
    """The message has to be actionable — an operator reading a boot failure
    needs the typo and the alternatives, not just 'invalid config'."""
    with pytest.raises(ValueError) as exc:
        resolve({"overrides": {"bass": {}, "genrl": {}}})
    msg = str(exc.value)
    assert "'bass'" in msg and "'genrl'" in msg
    for valid in ("'baas'", "'bcn'", "'general'", "'masa_agent_eval'"):
        assert valid in msg


def test_all_four_qualifiers_are_accepted(resolve):
    """Guard against the valid set drifting from the injector keys."""
    conf = resolve({
        "overrides": {
            "baas": {"http2": True},
            "bcn": {"max_connections": 11},
            "general": {"max_connections": 200},
            "masa_agent_eval": {"keepalive_expiry": 1.0},
        }
    })
    assert conf.for_qualifier("baas").http2 is True
    assert conf.for_qualifier("bcn").max_connections == 11
    assert conf.for_qualifier("general").max_connections == 200
    assert conf.for_qualifier("masa_agent_eval").keepalive_expiry == 1.0


def test_non_mapping_override_body_is_ignored(resolve):
    """A scalar where a mapping belongs must not crash boot. Unlike an unknown
    *key* — which cannot be honoured at all — a malformed body still names a
    real binding, so falling back to the shared defaults is a sane reading."""
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


# ── malformed values must stay inert, never fatal ────────────────────────────


@pytest.mark.parametrize(
    "block",
    [
        {"max_connections": None},          # `max_connections:` with no value
        {"keepalive_expiry": None},         # `keepalive_expiry: ~`
        {"max_connections": "lots"},
        {"keepalive_expiry": "soon"},
        {"max_keepalive_connections": []},
        {"http2": "maybe"},
        {"http2": None},
    ],
)
def test_malformed_values_fall_back_instead_of_raising(resolve, block):
    """A provider exception here is not a loud failure: lifecycle discovery
    swallows it, so all four HttpClient bindings would vanish at boot with no
    log line and the first outbound call would die somewhere unrelated. A bad
    pool ceiling must stay inert, as the block's docs promise."""
    conf = resolve(block)
    assert conf.defaults == cfg.HttpClientPoolPolicy()


def test_malformed_override_value_falls_back_to_the_shared_default(resolve):
    conf = resolve({"max_connections": 55, "overrides": {"baas": {"max_connections": None}}})
    assert conf.for_qualifier("baas").max_connections == 55


@pytest.mark.parametrize("raw", ["false", "False", "no", "off", "0", False, 0])
def test_falsey_http2_scalars_do_not_enable_http2(resolve, raw):
    """``bool("false")`` is ``True``. Getting this wrong silently turns on a
    wire-protocol change the design requires to be opt-in."""
    assert resolve({"http2": raw}).defaults.http2 is False


@pytest.mark.parametrize("raw", ["true", "True", "yes", "on", "1", True, 1])
def test_truthy_http2_scalars_enable_http2(resolve, raw):
    assert resolve({"http2": raw}).defaults.http2 is True


@pytest.mark.parametrize(
    "block",
    [
        {"max_connections": float("inf")},   # `max_connections: .inf` -> OverflowError
        {"max_connections": 0},              # every request would PoolTimeout forever
        {"max_connections": -5},
        {"max_keepalive_connections": -1},
        {"keepalive_expiry": -1.0},
        {"keepalive_expiry": float("nan")},
        {"keepalive_expiry": float("inf")},
    ],
)
def test_unusable_values_fall_back_instead_of_breaking_the_binding(resolve, block):
    """Values that cast cleanly but cannot work are as damaging as ones that
    raise: `max_connections: 0` makes every request on the binding wait for a
    connection that can never exist and fail as a timeout, for the life of the
    process."""
    conf = resolve(block)
    assert conf.defaults == cfg.HttpClientPoolPolicy()


def test_zero_keepalive_connections_is_allowed(resolve):
    """0 is legitimate here — it disables keep-alive without disabling the
    pool, so it must not be swept up by the range guard."""
    assert resolve({"max_keepalive_connections": 0}).defaults.max_keepalive_connections == 0


@pytest.mark.parametrize(
    "raw", [1.7, 2.5, "1.7", True, False, 100.5]
)
def test_fractional_or_boolean_connection_counts_are_rejected(resolve, raw):
    """`int(1.7)` is 1 — a legal-looking ceiling that serialises every burst,
    and one the range guard would wave through. `int(True)` is 1 for the same
    reason. Neither is a plausible intent, so both fall back."""
    conf = resolve({"max_connections": raw})
    assert conf.defaults.max_connections == 100


def test_whole_float_connection_count_is_accepted(resolve):
    """`max_connections: 64.0` is unambiguous — only fractions are rejected."""
    assert resolve({"max_connections": 64.0}).defaults.max_connections == 64


@pytest.mark.parametrize("raw", [2, -1, 99, 0.5, float("nan"), float("inf")])
def test_non_binary_numeric_http2_is_rejected(resolve, raw):
    """`bool()` reads every non-zero numeric as True, including .nan — so a
    typo like `http2: 2` would silently enable a wire change documented as
    opt-in, while every other malformed value falls back safely."""
    assert resolve({"http2": raw}).defaults.http2 is False


@pytest.mark.parametrize("raw,expected", [(0, False), (1, True), (0.0, False), (1.0, True)])
def test_binary_numeric_http2_is_honoured(resolve, raw, expected):
    """0/1 stay accepted — YAML users do write them."""
    assert resolve({"http2": raw}).defaults.http2 is expected


def test_non_binary_numeric_http2_does_not_override_a_configured_true(resolve):
    """The fallback is the *base* value, not the dataclass default: a bad
    override must not silently turn http2 off where it was deliberately on."""
    conf = resolve({"http2": True, "overrides": {"baas": {"http2": 7}}})
    assert conf.for_qualifier("baas").http2 is True
