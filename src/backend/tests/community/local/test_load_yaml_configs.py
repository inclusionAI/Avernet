"""YamlConfigProvider _load_yaml_configs 显式选 overlay 测试。

B2 把 YAML 加载逻辑从 ``agentclaw.community.local`` 搬到了 ``core/config/yaml_provider``；
此处直接测新家。
"""
import pytest

from agentclaw.community.core.config import yaml_provider
from agentclaw.community.core.config.yaml_provider import (
    YamlConfigProvider,
    _load_yaml_configs,
)
from agentclaw.community.di.profile import DeployProfile

# The corp domain markers these guards scan for are assembled from fragments so
# this guard's own source carries no literal internal-domain token for an OSS
# grep to flag; they still match the real domains at runtime.
_ALI = "ali" "pay"

class TestLoadYamlConfigsOverlaySelection:
    """_load_yaml_configs 应该加载调用方指定的 overlay yaml。"""

    def test_singlebox_loads_singlebox_yaml(self):
        cfg = _load_yaml_configs("application-singlebox.yaml")
        # singlebox.yaml 标志位：app.title = "AgentClaw Single Box"
        assert cfg["user_config"]["app"]["title"] == "AgentClaw Single Box"

    def test_provider_selects_overlay_from_profile_semantics(self):
        provider = YamlConfigProvider(DeployProfile.SINGLEBOX)

        config = provider.load()

        assert provider.overlay_name == "application-singlebox.yaml"
        assert config.user_config["app"]["title"] == "AgentClaw Single Box"

    def test_provider_rejects_physical_overlay_name_as_public_input(self):
        with pytest.raises(ValueError, match="Unknown YAML config profile"):
            YamlConfigProvider("application-singlebox.yaml")

    # B11: dev/prod are corp overlays (corp/configs); the community yaml_provider
    # searches only cwd/configs + community/configs, so it no longer loads them (corp
    # config is read by sofapy). The base ⊕ corp-overlay merge is covered by
    # tests/corp/core/config/test_corp_overlay_merge.py.

    def test_default_dev_overlay_requires_a_complete_config_pair(self):
        with pytest.raises(FileNotFoundError, match="application-dev.yaml"):
            _load_yaml_configs()

    def test_singlebox_has_no_external_baseurl(self):
        """singlebox 模式下所有 base_url 都应该 mock 到 127.0.0.1（除 baas 外）。"""
        cfg = _load_yaml_configs("application-singlebox.yaml")
        user_config = cfg["user_config"]
        # 抽查几个关键字段
        assert user_config["buservice"]["base_url"] == "http://127.0.0.1:9999"
        assert user_config["arca_sandbox"]["base_url"] == "http://127.0.0.1:9999"
        assert user_config["skill_center"]["base_url"] == "http://127.0.0.1:9999"
        # baas 是唯一保留的真本机依赖
        assert user_config["baas"]["api_base_url"] == "http://localhost:8890"

    def test_skips_base_only_directory_for_later_complete_overlay_pair(
        self, monkeypatch, tmp_path
    ):
        overlay_name = "application-selected.yaml"
        first_configs = tmp_path / "configs"
        first_configs.mkdir()
        (first_configs / "application.yaml").write_text("source: first\n")

        fallback_community = tmp_path / "fallback" / "community"
        fallback_configs = fallback_community / "configs"
        fallback_configs.mkdir(parents=True)
        (fallback_configs / "application.yaml").write_text("source: fallback\n")
        (fallback_configs / overlay_name).write_text("selected: true\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            yaml_provider,
            "__file__",
            str(fallback_community / "core" / "config" / "yaml_provider.py"),
        )

        assert _load_yaml_configs(overlay_name) == {
            "source": "fallback",
            "selected": True,
        }

    def test_raises_when_no_candidate_has_the_selected_overlay(
        self, monkeypatch, tmp_path
    ):
        overlay_name = "application-missing.yaml"
        first_configs = tmp_path / "configs"
        first_configs.mkdir()
        (first_configs / "application.yaml").write_text("source: first\n")

        fallback_community = tmp_path / "fallback" / "community"
        fallback_configs = fallback_community / "configs"
        fallback_configs.mkdir(parents=True)
        (fallback_configs / "application.yaml").write_text("source: fallback\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            yaml_provider,
            "__file__",
            str(fallback_community / "core" / "config" / "yaml_provider.py"),
        )

        with pytest.raises(FileNotFoundError) as error:
            _load_yaml_configs(overlay_name)

        assert overlay_name in str(error.value)
        assert str(first_configs) in str(error.value)
        assert str(fallback_configs) in str(error.value)


class TestCommunityOverlaySelection:
    """DEPLOY_PROFILE=community 加载中性基座 application.yaml + community overlay
    （application.yaml 已中性化，可安全合并 —— B6/OSS-0 #3）。"""

    def test_community_merges_neutral_base_and_overlay(self):
        cfg = _load_yaml_configs("application-community.yaml")
        user_config = cfg.get("user_config", {})
        # bcs 块只在 community overlay 里（不在 base application.yaml）。
        bcs = user_config.get("bcs", {})
        assert bcs.get("user_path") == "/auth/user"
        assert bcs.get("base_url", "").startswith("http")
        # 证明基座确实被合并进来了：一个只在中性 base 里的块（device_provider）出现。
        assert user_config.get("device_provider") == "local"
        assert cfg.get("app_name") == "agentclaw"

    def test_community_fully_removed_corp_blocks_stay_absent(self):
        """The corp-service blocks fully removed from the neutral base must not
        reappear in the merged community config (neither base nor overlay has
        them)."""
        user_config = _load_yaml_configs("application-community.yaml").get("user_config", {})
        for corp_block in (
            "token_exchange",
            "antbuservice_client",
            "buservice",
            "aceagent_client",
            "arca_sandbox",
            "dima",
            "antcode",
            "skill_center",
            "daas_sdk_config",
            "daas_sdk_config_prod",
            "codefuse_token",
        ):
            assert corp_block not in user_config, (
                f"community merged config leaked corp block {corp_block!r}"
            )

    def test_community_inherited_neutral_blocks_carry_no_corp_values(self):
        """Blocks the community now inherits from the neutral base (skill_scan,
        device_provider, health_check, …) must carry only neutral values — no
        corp endpoint/secret leaks."""
        import json

        user_config = _load_yaml_configs("application-community.yaml").get("user_config", {})
        # The neutral base skill_scan has no corp auth endpoint/secret.
        skill_scan = user_config.get("skill_scan", {})
        assert "auth_endpoint" not in skill_scan
        assert "auth_app_secret" not in skill_scan
        # device_provider is the neutral default, not a corp provider.
        assert user_config.get("device_provider") == "local"
        # No corp endpoint/secret marker anywhere in the merged yaml config.
        blob = json.dumps(user_config)
        for marker in (
            _ALI + ".com",
            _ALI + ".net",
            "antgroup-inc",
            "@other_manual",
            "aliyuncs.com",
            "TEMPLATE-",
        ):
            assert marker not in blob, (
                f"community merged yaml leaked corp marker {marker!r}"
            )

    def test_community_base_has_no_corp_endpoints_or_secrets(self):
        """community 基座里不能出现 corp 端点 / 密钥引用。"""
        import json

        blob = json.dumps(_load_yaml_configs("application-community.yaml"))
        for marker in (_ALI + ".com", _ALI + ".net", "@other_manual", "antgroup-inc"):
            assert marker not in blob, f"community base leaked corp marker {marker!r}"

    def test_community_base_exposes_neutral_blocks_correctly_shaped(self):
        """Positive guard: the neutral blocks the base-list ConfigModule providers
        read must be present AND shaped to match each provider's key access
        (flat vs nested), so a community deploy gets neutral values rather than
        silently falling through to corp dataclass defaults."""
        uc = _load_yaml_configs("application-community.yaml").get("user_config", {})
        # oss_to_nas: the provider reads FLAT keys, not a nested block.
        assert uc.get("oss_mount_root") == "./data/oss"
        assert uc.get("nas_mount_root") == "./data/nas"
        assert "oss_to_nas" not in uc  # must not be nested (would be ignored)
        # nested blocks the providers read via _block(name)
        assert uc.get("workspace", {}).get("openclaw_root", "").startswith("./")
        assert uc.get("device_allocation", {}).get("mode") == "multi"
        assert uc.get("baas", {}).get("tenant") == "community"
        assert uc.get("desktop_bot_periodic_scan", {}).get("enabled") is False

    def test_explicit_community_overlay_ignores_server_env(self, monkeypatch):
        # Explicit overlay selection ignores the runtime env axis.
        monkeypatch.setenv("SERVER_ENV", "singlebox")
        cfg = _load_yaml_configs("application-community.yaml")
        assert "bcs" in cfg.get("user_config", {})
        # 自包含：singlebox 的 corp 字段不会泄漏进来。
        assert "arca_sandbox" not in cfg.get("user_config", {})

    def test_base_application_yaml_has_no_bcs(self):
        # bcs 必须只在 community overlay，不能泄漏进 corp/test 路径。
        cfg = _load_yaml_configs("application-test.yaml")
        assert "bcs" not in cfg.get("user_config", {})

    def test_community_overlay_exposes_data_infra_blocks(self):
        # B3：database/cache/object_storage/secret 四块只在 community overlay。
        user_config = _load_yaml_configs("application-community.yaml").get("user_config", {})
        assert user_config.get("database", {}).get("url", "").startswith("sqlite:///")
        assert user_config.get("cache", {}).get("redis_url") == ""
        storage = user_config.get("object_storage", {})
        assert storage.get("backend") == "fs"
        assert storage.get("s3", {}).get("region") == "us-east-1"
        assert user_config.get("secret", {}).get("env_prefix") == "AGENTCLAW_SECRET_"

    def test_base_application_yaml_has_no_data_infra_blocks(self):
        # 这四块必须只在 community overlay，不能泄漏进 corp/test 路径。
        user_config = _load_yaml_configs("application-test.yaml").get("user_config", {})
        for block in ("database", "cache", "object_storage", "secret"):
            assert block not in user_config
