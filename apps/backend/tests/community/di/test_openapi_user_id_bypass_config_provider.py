"""``openapi.user_id_bypass`` — read strictly, shipped empty, refused in pre/prod.

The block lists ``user_id`` values the public surface accepts without the
"a person must name themselves" comparison (see
``adapters/http/openapi_v1/principal.py::require_user_id``). Each entry lets any
authenticated person act as that user on every user-scoped ``/openapi/v1``
operation, so three things are pinned here:

- **the provider reads the block strictly** — a malformed list fails rather than
  silently reading as empty;
- **every shipped configuration leaves it empty** — the base and every overlay,
  and the merged result of every deploy profile;
- **``pre`` and ``prod`` refuse a non-empty list** — the provider raises, and the
  startup eager check turns that into a failed boot. The corp pre/prod overlays
  live outside this repository, so this rule, not a file, is what keeps those
  environments empty; the tests here cover the rule on every spelling
  ``get_current_env()`` normalizes to those two.
"""
from __future__ import annotations

import pytest
import yaml

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di import config as cfg
from agentclaw.community.di.container import eager_check_critical_bindings
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.openapi_user_id_bypass_config_module import (
    BLOCK,
    KEY,
    PROTECTED_ENVS,
    OpenApiUserIdBypassConfigModule,
    validate_for_environment,
)
from agentclaw.community.di.openapi_user_id_bypass_config import (
    OpenApiUserIdBypassConfig,
)
from tests.community.config.effective_config import (
    COMMUNITY_CONFIGS,
    PROFILE_PAIRS,
    compute_effective_config,
)

_ENTRIES = ["shared-account", "service-user"]

# Every value ``get_current_env()`` folds into ``pre`` / ``prod``, in the
# spellings an operator actually exports.
_PROTECTED_SPELLINGS = ("pre", "prepub", "PRE", "prod", "gray", "PROD")
_OPEN_SPELLINGS = ("", "dev", "stable", "local", "DEV")


def _build(monkeypatch, user_config: dict, env: str = "dev") -> OpenApiUserIdBypassConfig:
    monkeypatch.setattr(config_module, "read_user_config", lambda: dict(user_config))
    monkeypatch.setenv("SERVER_ENV", env)
    return OpenApiUserIdBypassConfigModule().openapi_user_id_bypass()


# ── what the provider reads ────────────────────────────────────────────────


@pytest.mark.unit
def test_an_absent_block_means_no_bypass(monkeypatch):
    assert _build(monkeypatch, {}).user_ids == frozenset()


@pytest.mark.unit
@pytest.mark.parametrize("block", [None, {}, {KEY: None}, {KEY: []}])
def test_an_empty_block_means_no_bypass(monkeypatch, block):
    assert _build(monkeypatch, {BLOCK: block}).user_ids == frozenset()


@pytest.mark.unit
def test_the_list_is_read_into_the_config(monkeypatch):
    out = _build(monkeypatch, {BLOCK: {KEY: [*_ENTRIES, _ENTRIES[0]]}})

    assert out.user_ids == frozenset(_ENTRIES)


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["shared-account", {"id": "x"}, 42, True])
def test_a_non_list_value_is_refused(monkeypatch, raw):
    """A scalar must not be read as "one id" — nor silently as "no ids"."""
    with pytest.raises(ValueError, match=f"{BLOCK}.{KEY} must be a list"):
        _build(monkeypatch, {BLOCK: {KEY: raw}})


@pytest.mark.unit
@pytest.mark.parametrize("entry", ["", "   ", None, 42, ["nested"]])
def test_a_blank_or_non_string_entry_is_refused(monkeypatch, entry):
    with pytest.raises(ValueError, match="must be a non-blank string"):
        _build(monkeypatch, {BLOCK: {KEY: ["ok", entry]}})


@pytest.mark.unit
def test_an_unknown_key_is_refused(monkeypatch):
    """A misspelt key would otherwise be inert — and look like a working bypass."""
    with pytest.raises(ValueError, match=f"unknown {BLOCK} key"):
        _build(monkeypatch, {BLOCK: {"user_id_bypas": _ENTRIES}})


@pytest.mark.unit
def test_a_non_mapping_block_is_refused(monkeypatch):
    with pytest.raises(ValueError, match=f"{BLOCK} must be a mapping"):
        _build(monkeypatch, {BLOCK: _ENTRIES})


# ── where a non-empty list is allowed ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("env", _OPEN_SPELLINGS)
def test_a_dev_family_environment_may_carry_a_bypass(monkeypatch, env):
    out = _build(monkeypatch, {BLOCK: {KEY: _ENTRIES}}, env=env)

    assert out.user_ids == frozenset(_ENTRIES)


@pytest.mark.unit
@pytest.mark.parametrize("env", _PROTECTED_SPELLINGS)
def test_pre_and_prod_refuse_a_non_empty_bypass(monkeypatch, env):
    """The rule itself: in pre/prod the list is empty or the config fails to build."""
    with pytest.raises(ValueError, match=f"{BLOCK}.{KEY} must be empty"):
        _build(monkeypatch, {BLOCK: {KEY: _ENTRIES}}, env=env)


@pytest.mark.unit
@pytest.mark.parametrize("env", _PROTECTED_SPELLINGS)
def test_pre_and_prod_accept_an_empty_bypass(monkeypatch, env):
    """The shipped default builds everywhere — the rule bites only on entries."""
    assert _build(monkeypatch, {BLOCK: {KEY: []}}, env=env).user_ids == frozenset()
    assert _build(monkeypatch, {}, env=env).user_ids == frozenset()


@pytest.mark.unit
def test_the_protected_environments_are_exactly_pre_and_prod():
    """Pinned so that a widening or narrowing of the rule is a deliberate edit."""
    assert PROTECTED_ENVS == frozenset({"pre", "prod"})

    listed = OpenApiUserIdBypassConfig(user_ids=frozenset(_ENTRIES))
    for env in PROTECTED_ENVS:
        with pytest.raises(ValueError):
            validate_for_environment(listed, env)
    assert validate_for_environment(listed, "dev") is listed
    assert validate_for_environment(OpenApiUserIdBypassConfig(), "prod").user_ids == frozenset()


@pytest.mark.unit
def test_the_startup_eager_check_turns_the_refusal_into_a_failed_boot(monkeypatch):
    """``pre``/``prod`` boot runs ``eager_check_critical_bindings``; the config is in it.

    Without this entry the provider would still raise, but only on the first
    user-scoped request — a 500 on a deployment that reported healthy.
    """
    real = config_module.read_user_config
    monkeypatch.setattr(
        config_module,
        "read_user_config",
        lambda: {**real(), BLOCK: {KEY: _ENTRIES}},
    )
    injector = build_injector(profile=DeployProfile.TEST)

    monkeypatch.setenv("SERVER_ENV", "prod")
    with pytest.raises(RuntimeError, match="DI eager-check failed") as excinfo:
        eager_check_critical_bindings(injector)

    assert OpenApiUserIdBypassConfig.__name__ in str(excinfo.value)
    assert f"{BLOCK}.{KEY} must be empty" in str(excinfo.value)


# ── what ships ─────────────────────────────────────────────────────────────

_SHIPPED = sorted(p.name for p in COMMUNITY_CONFIGS.glob("application*.yaml"))


def _shipped_bypass(name: str):
    loaded = yaml.safe_load((COMMUNITY_CONFIGS / name).read_text(encoding="utf-8")) or {}
    block = (loaded.get("user_config") or {}).get(BLOCK) or {}
    return block.get(KEY)


@pytest.mark.unit
def test_the_base_config_declares_the_key_empty():
    """The base names the knob so an operator finds it — and finds it empty."""
    assert _shipped_bypass("application.yaml") == []


@pytest.mark.unit
@pytest.mark.parametrize("name", _SHIPPED)
def test_no_shipped_config_lists_a_bypass(name):
    """Every shipped file — base and every overlay — leaves the list empty."""
    assert _shipped_bypass(name) in (None, []), (
        f"{name} ships a non-empty {BLOCK}.{KEY}; the list must be empty in "
        "every committed configuration"
    )


@pytest.mark.unit
@pytest.mark.parametrize("profile", sorted(PROFILE_PAIRS))
def test_every_deploy_profile_merges_to_an_empty_bypass(profile):
    """The merged (base ⊕ overlay) result each profile boots with is empty."""
    base, overlay = PROFILE_PAIRS[profile]
    merged = compute_effective_config(base, overlay)["raw_user_config"]

    assert merged[BLOCK][KEY] == []


@pytest.mark.unit
@pytest.mark.parametrize("env", sorted(PROTECTED_ENVS))
def test_every_shipped_config_builds_in_pre_and_prod(monkeypatch, env):
    """The shipped files pass the rule they are pinned to — in the environments it guards."""
    for name in _SHIPPED:
        loaded = yaml.safe_load((COMMUNITY_CONFIGS / name).read_text(encoding="utf-8")) or {}
        out = _build(monkeypatch, loaded.get("user_config") or {}, env=env)
        assert out.user_ids == frozenset(), name


def test_the_test_injector_binds_the_config_empty(test_injector):
    out = test_injector.get(cfg.OpenApiUserIdBypassConfig)

    assert isinstance(out, OpenApiUserIdBypassConfig)
    assert out.user_ids == frozenset()
    assert cfg.OpenApiUserIdBypassConfig is OpenApiUserIdBypassConfig
