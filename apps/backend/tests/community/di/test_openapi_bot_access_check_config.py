"""``openapi.bot_access_check`` — read strictly, shipped empty, resolved at boot."""
from __future__ import annotations

import pytest

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.openapi_bot_access_check_config_module import (
    APP_NAMES,
    BLOCK,
    KEY,
    OpenApiBotAccessCheckConfigModule,
)
from agentclaw.community.di.modules.openapi_user_id_bypass_config_module import (
    OpenApiUserIdBypassConfigModule,
)
from agentclaw.community.di.openapi_bot_access_check_config import (
    OpenApiBotAccessCheckConfig,
)
from tests.community.config.effective_config import (
    PROFILE_PAIRS,
    compute_effective_config,
)


def _build(monkeypatch, user_config: dict) -> OpenApiBotAccessCheckConfig:
    monkeypatch.setattr(config_module, "read_user_config", lambda: dict(user_config))
    return OpenApiBotAccessCheckConfigModule().openapi_bot_access_check()


@pytest.mark.unit
@pytest.mark.parametrize(
    "user_config",
    [{}, {BLOCK: None}, {BLOCK: {}}, {BLOCK: {KEY: None}}, {BLOCK: {KEY: {}}},
     {BLOCK: {KEY: {APP_NAMES: None}}}, {BLOCK: {KEY: {APP_NAMES: []}}}],
)
def test_absent_or_empty_means_nobody(monkeypatch, user_config):
    assert _build(monkeypatch, user_config).app_names == frozenset()


@pytest.mark.unit
def test_the_list_is_read_into_the_config(monkeypatch):
    out = _build(monkeypatch, {BLOCK: {KEY: {APP_NAMES: ["app-a", "app-b", "app-a"]}}})

    assert out.app_names == frozenset({"app-a", "app-b"})


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["app-a", {"name": "x"}, 42, True])
def test_a_non_list_is_refused(monkeypatch, raw):
    with pytest.raises(ValueError, match="must be a list"):
        _build(monkeypatch, {BLOCK: {KEY: {APP_NAMES: raw}}})


@pytest.mark.unit
@pytest.mark.parametrize("entry", ["", "   ", 42, True, None, ["x"]])
def test_a_blank_or_non_string_entry_is_refused(monkeypatch, entry):
    with pytest.raises(ValueError, match="non-blank string"):
        _build(monkeypatch, {BLOCK: {KEY: {APP_NAMES: ["ok", entry]}}})


@pytest.mark.unit
def test_an_unknown_key_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="unknown openapi.bot_access_check key"):
        _build(monkeypatch, {BLOCK: {KEY: {APP_NAMES: [], "allowed_app_ids": [1]}}})


@pytest.mark.unit
@pytest.mark.parametrize("block", ["x", ["x"]])
def test_a_non_mapping_block_is_refused(monkeypatch, block):
    with pytest.raises(ValueError, match="must be a mapping"):
        _build(monkeypatch, {BLOCK: block})


@pytest.mark.unit
def test_a_non_mapping_settings_value_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="must be a mapping"):
        _build(monkeypatch, {BLOCK: {KEY: ["app-a"]}})


@pytest.mark.unit
def test_the_user_id_bypass_module_accepts_the_shared_block(monkeypatch):
    user_config = {BLOCK: {"user_id_bypass": [], KEY: {APP_NAMES: ["app-a"]}}}
    monkeypatch.setattr(config_module, "read_user_config", lambda: dict(user_config))
    monkeypatch.setenv("SERVER_ENV", "dev")

    assert OpenApiUserIdBypassConfigModule().openapi_user_id_bypass().user_ids == frozenset()


@pytest.mark.unit
@pytest.mark.parametrize("profile", sorted(PROFILE_PAIRS))
def test_every_deploy_profile_merges_to_the_shipped_allowlist(profile):
    base, overlay = PROFILE_PAIRS[profile]
    merged = compute_effective_config(base, overlay)["raw_user_config"]

    assert merged[BLOCK][KEY][APP_NAMES] == ["acmng"]


@pytest.mark.unit
def test_build_injector_resolves_it(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    injector = build_injector(profile=DeployProfile.TEST)

    assert injector.get(OpenApiBotAccessCheckConfig).app_names == frozenset({"acmng"})


@pytest.mark.unit
def test_a_malformed_allowlist_fails_the_boot(monkeypatch):
    real = config_module.read_user_config
    block = {"user_id_bypass": [], KEY: {APP_NAMES: "app-a"}}
    monkeypatch.setattr(config_module, "read_user_config", lambda: {**real(), BLOCK: block})
    monkeypatch.setenv("SERVER_ENV", "dev")

    with pytest.raises(Exception, match="must be a list"):
        build_injector(profile=DeployProfile.TEST)
