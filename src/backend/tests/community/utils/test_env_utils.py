"""Tests for agentclaw.community.utils.env_utils."""
from __future__ import annotations

import pytest

from agentclaw.community.utils import env_utils as utils_env


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for k in (
        "LOCAL_DEV_MODE",
        "DEPLOY_PROFILE",
        "SERVER_ENV",
        "REAL_SERVER_ENV",
        "ALIPAY_APP_ENV",
    ):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("mod", [utils_env])
class TestEnvUtils:
    # ``is_local_mode`` is derived from the ``DEPLOY_PROFILE`` switch (B1):
    # the LOCAL-stub profiles are ``test`` and ``singlebox``.
    @pytest.mark.parametrize("profile", ["test", "singlebox"])
    def test_is_local_mode_true(
        self, mod, profile, clean_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("DEPLOY_PROFILE", profile)
        assert mod.is_local_mode() is True

    @pytest.mark.parametrize("profile", ["corp", "community", None])
    def test_is_local_mode_false(
        self, mod, profile, clean_env, monkeypatch: pytest.MonkeyPatch
    ):
        if profile is not None:
            monkeypatch.setenv("DEPLOY_PROFILE", profile)
        assert mod.is_local_mode() is False

    def test_is_empty_env(self, mod, clean_env):
        assert mod.is_empty_env() is True

    def test_is_empty_env_false(self, mod, clean_env, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SERVER_ENV", "prod")
        assert mod.is_empty_env() is False

    def test_is_dev_empty(self, mod, clean_env):
        assert mod.is_dev() is True

    def test_is_dev_explicit(self, mod, clean_env, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SERVER_ENV", "dev")
        assert mod.is_dev() is True

    def test_is_dev_stable(self, mod, clean_env, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SERVER_ENV", "stable")
        assert mod.is_dev() is True

    def test_is_dev_false_on_prod(self, mod, clean_env, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SERVER_ENV", "prod")
        assert mod.is_dev() is False

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("prod", "prod"),
            ("gray", "prod"),
            ("pre", "pre"),
            ("prepub", "pre"),
            ("dev", "dev"),
            ("", "dev"),
            ("other", "dev"),
        ],
    )
    def test_get_current_env(self, mod, clean_env, monkeypatch, value, expected):
        if value:
            monkeypatch.setenv("SERVER_ENV", value)
        assert mod.get_current_env() == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("prod", "prod"),
            ("gray", "gray"),
            ("pre", "pre"),
            ("prepub", "pre"),
            ("dev", "dev"),
            ("", "dev"),
        ],
    )
    def test_get_current_env_with_gray(
        self, mod, clean_env, monkeypatch, value, expected
    ):
        if value:
            monkeypatch.setenv("SERVER_ENV", value)
        assert mod.get_current_env_with_gray() == expected

    def test_env_var_precedence_real_server_env(
        self, mod, clean_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REAL_SERVER_ENV", "prod")
        assert mod.get_current_env() == "prod"

    def test_env_var_precedence_alipay(
        self, mod, clean_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ALIPAY_APP_ENV", "pre")
        assert mod.get_current_env() == "pre"


def test_singlebox_profile_uses_dev_data_env(clean_env, monkeypatch):
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.setenv("SERVER_ENV", "dev")
    assert utils_env.is_local_mode() is True
    assert utils_env.is_dev() is True
    assert utils_env.get_current_env() == "dev"
    assert utils_env.get_current_env_with_gray() == "dev"
