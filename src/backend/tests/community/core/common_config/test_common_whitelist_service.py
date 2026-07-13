from __future__ import annotations

import pytest

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


def test_get_owner_ids_normalizes_deduplicates_and_drops_blanks():
    config = FakeCommonConfigService(
        {
            ("bot_dormant", "protected_owner_ids", "prod"): [
                100001,
                " 100002 ",
                "100001",
                "",
                "   ",
                None,
            ]
        }
    )
    service = CommonWhiteListService(config)

    assert service.get_owner_ids(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="prod",
    ) == frozenset({"100001", "100002"})
    assert len(config.calls) == 1
    assert config.calls[0]["business_code"] == "bot_dormant"
    assert config.calls[0]["param_code"] == "protected_owner_ids"
    assert config.calls[0]["env"] == "prod"
    assert config.calls[0]["only_enabled"] is True
    assert config.calls[0]["default"] is not None


def test_get_owner_ids_returns_empty_set_when_config_is_missing():
    service = CommonWhiteListService(FakeCommonConfigService())

    assert service.get_owner_ids(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="pre",
    ) == frozenset()


def test_get_owner_ids_returns_empty_set_when_config_is_disabled():
    config = FakeCommonConfigService()
    config.get_value = lambda **kwargs: kwargs["default"]
    service = CommonWhiteListService(config)

    assert service.get_owner_ids(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="pre",
    ) == frozenset()


@pytest.mark.parametrize("value", [None], ids=["enabled_top_level_null"])
def test_get_owner_ids_rejects_enabled_top_level_null(value, caplog):
    caplog.set_level("ERROR")
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {("bot_dormant", "protected_owner_ids", "prod"): value}
        )
    )

    with pytest.raises(ValueError, match="protected owner IDs must be a list"):
        service.get_owner_ids(
            business_code="bot_dormant",
            param_code="protected_owner_ids",
            env="prod",
        )

    assert "value_type=NoneType" in caplog.text
    assert "owner1" not in caplog.text


@pytest.mark.parametrize("invalid_item", [True, {}, []])
def test_get_owner_ids_rejects_invalid_list_elements(invalid_item, caplog):
    caplog.set_level("ERROR")
    value = [100001, invalid_item]
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {("bot_dormant", "protected_owner_ids", "prod"): value}
        )
    )

    with pytest.raises(ValueError, match="strings or integers"):
        service.get_owner_ids(
            business_code="bot_dormant",
            param_code="protected_owner_ids",
            env="prod",
        )

    assert "item_type=" in caplog.text
    assert repr(invalid_item) not in caplog.text
    assert repr(value) not in caplog.text


@pytest.mark.parametrize("value", [{"100001": True}, "100001", 100001, True])
def test_get_owner_ids_rejects_non_list_config(value, caplog):
    caplog.set_level("ERROR")
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {("bot_dormant", "protected_owner_ids", "prod"): value}
        )
    )

    with pytest.raises(ValueError, match="protected owner IDs must be a list"):
        service.get_owner_ids(
            business_code="bot_dormant",
            param_code="protected_owner_ids",
            env="prod",
        )
    assert "business_code=bot_dormant" in caplog.text
    assert "param_code=protected_owner_ids" in caplog.text
    assert "env=prod" in caplog.text
    assert repr(value) not in caplog.text


def test_get_owner_ids_propagates_config_read_failure(caplog):
    caplog.set_level("ERROR")
    config = FakeCommonConfigService()
    config.get_value = lambda **_: (_ for _ in ()).throw(RuntimeError("db unavailable"))
    service = CommonWhiteListService(config)

    with pytest.raises(RuntimeError, match="db unavailable"):
        service.get_owner_ids(
            business_code="bot_dormant",
            param_code="protected_owner_ids",
            env="prod",
        )
    assert "business_code=bot_dormant" in caplog.text
    assert "param_code=protected_owner_ids" in caplog.text
    assert "env=prod" in caplog.text
