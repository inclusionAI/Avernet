"""Tests for ConfigPath enum and get_config_by_path()."""

import pytest

from secbaas.community.config import Config, ConfigPath, get_config_by_path


@pytest.fixture
def sample_config() -> Config:
    """A Config instance with known values for the actual ConfigPath members."""
    return Config(
        user_config={
            "agentclawproxy": {
                "host": {
                    "dev": "ac-proxy-dev.service.test",
                    "pre": "ac-proxy-pre.service.test",
                    "prod": "ac-proxy-prod.service.test",
                },
            },
            "secbaas": {
                "callback": {
                    "host": {
                        "dev": "https://cb.dev.service.test",
                        "pre": "https://cb.pre.service.test",
                        "prod": "https://cb.prod.service.test",
                    },
                },
            },
        },
    )


class TestConfigPathValues:
    """Every ConfigPath member has the expected dotted value."""

    @pytest.mark.parametrize(
        "member, expected",
        [
            (
                ConfigPath.AGENTCLAW_PROXY_HOST_DEV,
                "user_config.agentclawproxy.host.dev",
            ),
            (
                ConfigPath.AGENTCLAW_PROXY_HOST_PRE,
                "user_config.agentclawproxy.host.pre",
            ),
            (
                ConfigPath.AGENTCLAW_PROXY_HOST_PROD,
                "user_config.agentclawproxy.host.prod",
            ),
            (
                ConfigPath.SECBAAS_CALLBACK_HOST_DEV,
                "user_config.secbaas.callback.host.dev",
            ),
            (
                ConfigPath.SECBAAS_CALLBACK_HOST_PRE,
                "user_config.secbaas.callback.host.pre",
            ),
            (
                ConfigPath.SECBAAS_CALLBACK_HOST_PROD,
                "user_config.secbaas.callback.host.prod",
            ),
        ],
    )
    def test_config_path_value(self, member, expected):
        assert member.value == expected


class TestGetConfigByPath:
    """Path resolution for each ConfigPath member."""

    def test_agentclawproxy_host_dev(self, sample_config):
        assert (
            get_config_by_path(sample_config, ConfigPath.AGENTCLAW_PROXY_HOST_DEV)
            == "ac-proxy-dev.service.test"
        )

    def test_agentclawproxy_host_pre(self, sample_config):
        assert (
            get_config_by_path(sample_config, ConfigPath.AGENTCLAW_PROXY_HOST_PRE)
            == "ac-proxy-pre.service.test"
        )

    def test_agentclawproxy_host_prod(self, sample_config):
        assert (
            get_config_by_path(sample_config, ConfigPath.AGENTCLAW_PROXY_HOST_PROD)
            == "ac-proxy-prod.service.test"
        )

    def test_secbaas_callback_host_dev(self, sample_config):
        assert (
            get_config_by_path(sample_config, ConfigPath.SECBAAS_CALLBACK_HOST_DEV)
            == "https://cb.dev.service.test"
        )

    def test_secbaas_callback_host_pre(self, sample_config):
        assert (
            get_config_by_path(sample_config, ConfigPath.SECBAAS_CALLBACK_HOST_PRE)
            == "https://cb.pre.service.test"
        )

    def test_secbaas_callback_host_prod(self, sample_config):
        assert (
            get_config_by_path(sample_config, ConfigPath.SECBAAS_CALLBACK_HOST_PROD)
            == "https://cb.prod.service.test"
        )


class TestGetConfigByStringPath:
    """Arbitrary string paths work in addition to ConfigPath members."""

    def test_arbitrary_path(self, sample_config):
        assert (
            get_config_by_path(sample_config, "user_config.agentclawproxy.host.dev")
            == "ac-proxy-dev.service.test"
        )

    def test_empty_returns_default(self, sample_config):
        assert get_config_by_path(sample_config, "") is None
        assert get_config_by_path(sample_config, "", default=42) == 42


class TestGetConfigDefaults:
    """Behaviour when config keys are missing."""

    def test_missing_top_level_returns_default(self, sample_config):
        assert (
            get_config_by_path(sample_config, "nonexistent", default="fallback")
            == "fallback"
        )

    def test_missing_nested_returns_default(self, sample_config):
        assert (
            get_config_by_path(
                sample_config, "user_config.nonexistent.key", default=None
            )
            is None
        )

    def test_partial_path_returns_default(self, sample_config):
        assert (
            get_config_by_path(
                sample_config,
                "user_config.agentclawproxy.nonexistent.subkey",
                default="nope",
            )
            == "nope"
        )

    def test_default_none_for_unknown_path(self, sample_config):
        assert get_config_by_path(sample_config, "user_config.not_there") is None


class TestConfigPathProperties:
    """ConfigPath behaves like a proper string enum."""

    def test_value_is_string(self):
        assert (
            ConfigPath.AGENTCLAW_PROXY_HOST_DEV == "user_config.agentclawproxy.host.dev"
        )
        assert isinstance(ConfigPath.AGENTCLAW_PROXY_HOST_DEV, str)

    def test_members_are_unique(self):
        values = [m.value for m in ConfigPath]
        assert len(values) == len(set(values))

    def test_str_converts_to_value(self):
        assert (
            str(ConfigPath.SECBAAS_CALLBACK_HOST_PROD)
            == "user_config.secbaas.callback.host.prod"
        )

    def test_comparison_with_string(self):
        assert (
            ConfigPath.AGENTCLAW_PROXY_HOST_DEV == "user_config.agentclawproxy.host.dev"
        )
        assert ConfigPath.AGENTCLAW_PROXY_HOST_DEV != "wrong"
