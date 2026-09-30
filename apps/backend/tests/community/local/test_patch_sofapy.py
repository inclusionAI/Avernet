"""Tests for local-mode sofapy_base monkey-patching.

The config fake was removed in B2 — configuration now comes from the
``core/config`` ConfigProvider registry (YAML default for local/test), so the
former ``LocalConfig`` / ``get_config`` assertions are gone. The AppConfig
duck-type and the YAML loader are tested here against their new ``core/config``
homes; ``patch_sofapy_for_local`` is verified to still install the remaining
layotto fake (the mcp fakes were removed in B7).
"""

from agentclaw.community.core.config.provider import AppConfig
from agentclaw.community.core.config.yaml_provider import _deep_merge, _load_yaml_configs
from agentclaw.community.local import patch_sofapy_for_local


class TestAppConfig:
    """AppConfig duck-type compatibility (formerly LocalConfig)."""

    def test_user_config_dict_access(self):
        config = AppConfig(
            user_config={"key": "value"}, raw={}, app_name="test", delegate=None
        )
        assert config.user_config.get("key") == "value"
        assert config.user_config.get("missing", "default") == "default"

    def test_unknown_attr_raises_without_delegate(self):
        # No delegate (YAML path) → arbitrary top-level attr access falls through
        # to AttributeError, so getattr(cfg, x, default) returns the default.
        config = AppConfig(user_config={}, raw={}, app_name="test", delegate=None)
        assert getattr(config, "bcsfuse", None) is None

    def test_model_dump_returns_raw(self):
        raw = {"app_name": "test", "user_config": {"k": "v"}}
        config = AppConfig(
            user_config={"k": "v"}, raw=raw, app_name="test", delegate=None
        )
        assert config.model_dump() == raw


class TestDeepMerge:
    """Tests for _deep_merge utility."""

    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99, "z": 100}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_base_not_mutated(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert base == {"a": {"x": 1}}

    def test_empty_override(self):
        base = {"a": 1}
        result = _deep_merge(base, {})
        assert result == {"a": 1}

    def test_empty_base(self):
        override = {"a": 1}
        result = _deep_merge({}, override)
        assert result == {"a": 1}


class TestLoadYamlConfigs:
    """Tests for _load_yaml_configs."""

    def test_loads_configs_from_project(self):
        """Should be able to load the actual config files from this project."""
        config = _load_yaml_configs("application-test.yaml")
        # Must have user_config from the neutral base application.yaml.
        assert "user_config" in config
        assert "features" in config["user_config"]

    def test_overlay_merged_onto_base(self):
        """The selected overlay is deep-merged onto the base (B11: the test suite
        runs DEPLOY_PROFILE=test, so the overlay is application-test.yaml)."""
        config = _load_yaml_configs("application-test.yaml")
        user_config = config.get("user_config", {})
        # application-test.yaml adds aidesktop_root (absent from the base) — its
        # presence proves the overlay was merged in.
        assert user_config.get("aidesktop_root") == "~/.aidesktop"


class TestPatchSofapyForLocal:
    """patch_sofapy_for_local keeps the runner / application / logger fakes;
    the config fake was removed in B2, the mcp fakes in B7, and the
    layotto_manager fake in B6 (core no longer reads layotto — DRM goes through
    the injected DRMReaderPlugin)."""

    def test_does_not_fake_config(self):
        """patch_sofapy_for_local must NOT install a fake get_config that returns
        our neutral AppConfig — config now comes from the provider registry."""
        patch_sofapy_for_local()
        try:
            import sofapy_base.app.config as config_module
        except ImportError:
            # No sofapy installed (community): there is no config module to fake.
            return
        # sofapy is installed: get_config must be the real one, not our AppConfig fake.
        try:
            result = config_module.get_config()
        except FileNotFoundError:
            # B11: the real sofapy get_config reads an assembled configs/ dir (cwd),
            # which the monorepo test cwd lacks (configs are split across the
            # community/corp subtrees). Raising rather than returning is itself proof
            # that get_config was NOT replaced by our AppConfig-returning fake.
            return
        assert not isinstance(result, AppConfig)
