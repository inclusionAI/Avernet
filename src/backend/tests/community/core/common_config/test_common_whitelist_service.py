from __future__ import annotations

from agentclaw.community.core.common_config.whitelist_service import CommonWhiteListService


class FakeCommonConfigService:
    def __init__(self, values: dict[tuple[str, str, str], object] | None = None) -> None:
        self.values = values or {}
        self.calls: list[dict[str, object]] = []

    def get_value(
        self,
        *,
        business_code: str,
        param_code: str,
        env: str,
        default=None,
        only_enabled: bool = True,
    ):
        self.calls.append(
            {
                "business_code": business_code,
                "param_code": param_code,
                "env": env,
                "default": default,
                "only_enabled": only_enabled,
            }
        )
        return self.values.get((business_code, param_code, env), default)


def test_bot_feature_enabled_when_enable_all_true():
    config_service = FakeCommonConfigService(
        {
            ("nas_mount", "engine_dir_mount_whitelist", "pre"): {
                "enable_all": True,
                "whitelist": [],
            }
        }
    )
    service = CommonWhiteListService(config_service)

    assert service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_missing",
        bot_id="bot_missing",
        env="pre",
    ) is True
    assert config_service.calls[0]["only_enabled"] is True


def test_bot_feature_enabled_when_whitelist_matched():
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {
                ("nas_mount", "engine_dir_mount_whitelist", "pre"): {
                    "enable_all": False,
                    "whitelist": [
                        {"owner_id": "owner_1", "bot_id": "bot_1"},
                        {"owner_id": "owner_2", "bot_id": "bot_2"},
                    ],
                }
            }
        )
    )

    assert service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_1",
        bot_id="bot_1",
        env="pre",
    ) is True


def test_bot_feature_disabled_when_enable_all_false_and_not_matched():
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {
                ("nas_mount", "engine_dir_mount_whitelist", "pre"): {
                    "enable_all": False,
                    "whitelist": [
                        {"owner_id": "owner_1", "bot_id": "bot_1"},
                    ],
                }
            }
        )
    )

    assert service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_1",
        bot_id="bot_2",
        env="pre",
    ) is False


def test_bot_feature_disabled_when_config_missing_or_invalid():
    missing_service = CommonWhiteListService(FakeCommonConfigService())
    assert missing_service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_1",
        bot_id="bot_1",
        env="pre",
    ) is False

    invalid_service = CommonWhiteListService(
        FakeCommonConfigService(
            {
                ("nas_mount", "engine_dir_mount_whitelist", "pre"): {
                    "enable_all": False,
                    "whitelist": "owner_1:bot_1",
                }
            }
        )
    )
    assert invalid_service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_1",
        bot_id="bot_1",
        env="pre",
    ) is False


def test_bot_feature_uses_env_dimension():
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {
                ("nas_mount", "engine_dir_mount_whitelist", "pre"): {
                    "enable_all": False,
                    "whitelist": [{"owner_id": "owner_1", "bot_id": "bot_1"}],
                }
            }
        )
    )

    assert service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_1",
        bot_id="bot_1",
        env="prod",
    ) is False


def test_bot_feature_returns_default_when_enable_all_is_missing_or_invalid():
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {
                ("nas_mount", "engine_dir_mount_whitelist", "pre"): {
                    "whitelist": [{"owner_id": "owner_1", "bot_id": "bot_1"}],
                }
            }
        )
    )

    assert service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_1",
        bot_id="bot_1",
        env="pre",
        default=True,
    ) is True

    invalid_service = CommonWhiteListService(
        FakeCommonConfigService(
            {
                ("nas_mount", "engine_dir_mount_whitelist", "pre"): {
                    "enable_all": "false",
                    "whitelist": [{"owner_id": "owner_1", "bot_id": "bot_1"}],
                }
            }
        )
    )

    assert invalid_service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_1",
        bot_id="bot_1",
        env="pre",
        default=True,
    ) is True


def test_bot_feature_skips_malformed_whitelist_items_before_match():
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {
                ("nas_mount", "engine_dir_mount_whitelist", "pre"): {
                    "enable_all": False,
                    "whitelist": [
                        "owner_1:bot_1",
                        None,
                        {"owner_id": "owner_1", "bot_id": "bot_1"},
                    ],
                }
            }
        )
    )

    assert service.is_bot_feature_enabled(
        business_code="nas_mount",
        param_code="engine_dir_mount_whitelist",
        owner_id="owner_1",
        bot_id="bot_1",
        env="pre",
    ) is True
