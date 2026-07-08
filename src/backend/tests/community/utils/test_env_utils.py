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


class TestIsSinglebox:
    """is_singlebox() 只在 SERVER_ENV=singlebox 时返 True。"""

    def test_singlebox_env_returns_true(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "singlebox")
        assert utils_env.is_singlebox() is True

    def test_dev_env_returns_false(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "dev")
        assert utils_env.is_singlebox() is False

    def test_empty_env_returns_false(self, clean_env):
        assert utils_env.is_singlebox() is False

    def test_case_insensitive(self, clean_env, monkeypatch):
        """SERVER_ENV=SINGLEBOX 也应被识别为 singlebox。"""
        monkeypatch.setenv("SERVER_ENV", "SINGLEBOX")
        assert utils_env.is_singlebox() is True


class TestIsDevWithSinglebox:
    """is_dev() 在 singlebox 模式下也应返 True（向后兼容）。"""

    def test_singlebox_is_dev(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "singlebox")
        assert utils_env.is_dev() is True

    def test_dev_is_dev(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "dev")
        assert utils_env.is_dev() is True

    def test_prod_is_not_dev(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "prod")
        assert utils_env.is_dev() is False


class TestGetCurrentEnvSinglebox:
    """get_current_env() 需要单独识别 singlebox（不再 fallback 到 dev）。"""

    def test_singlebox_returns_singlebox(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "singlebox")
        assert utils_env.get_current_env() == "singlebox"

    def test_dev_still_returns_dev(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "dev")
        assert utils_env.get_current_env() == "dev"

    def test_prod_still_returns_prod(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "prod")
        assert utils_env.get_current_env() == "prod"

    def test_empty_still_returns_dev(self, clean_env):
        """空 env 仍按既有规则 fallback 到 dev。"""
        assert utils_env.get_current_env() == "dev"


class TestIsSingleboxReadsAllEnvVars:
    """is_singlebox() 应该和 is_dev/get_current_env 一样，
    查 SERVER_ENV → REAL_SERVER_ENV → ALIPAY_APP_ENV 三个变量。

    Bug fix: 之前 is_singlebox 只查 SERVER_ENV，行为跟其他函数不一致。
    """

    def test_real_server_env_singlebox(self, clean_env, monkeypatch):
        monkeypatch.setenv("REAL_SERVER_ENV", "singlebox")
        assert utils_env.is_singlebox() is True

    def test_alipay_app_env_singlebox(self, clean_env, monkeypatch):
        monkeypatch.setenv("ALIPAY_APP_ENV", "singlebox")
        assert utils_env.is_singlebox() is True

    def test_server_env_overrides_others(self, clean_env, monkeypatch):
        """优先级：SERVER_ENV > REAL_SERVER_ENV > ALIPAY_APP_ENV。"""
        monkeypatch.setenv("SERVER_ENV", "prod")
        monkeypatch.setenv("REAL_SERVER_ENV", "singlebox")
        assert utils_env.is_singlebox() is False


class TestGetCurrentEnvWithGraySinglebox:
    """get_current_env_with_gray() 也应识别 singlebox。"""

    def test_singlebox_returns_singlebox(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "singlebox")
        assert utils_env.get_current_env_with_gray() == "singlebox"

    def test_gray_still_returns_gray(self, clean_env, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "gray")
        assert utils_env.get_current_env_with_gray() == "gray"
