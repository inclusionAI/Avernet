"""Task feature switch contract tests."""

from __future__ import annotations

from agentclaw.community.core.task.task_dispatch.task_settings import (
    HARNESS_POLLER,
    HARNESS_POLLER_KEY,
    TaskSettingsService,
)


class _FakeStore:
    """最小系统配置 KV fake:签名对齐 SystemConfigService.set_config(operator=)。"""

    def __init__(self, value=None, exc=None) -> None:
        self._value = value
        self._exc = exc
        self.set_calls: list[dict] = []

    def get_config(self, *, category, config_key, env):
        if self._exc is not None:
            raise self._exc
        return self._value

    def set_config(
        self,
        *,
        category,
        config_key,
        config_value,
        env,
        description=None,
        operator=None,
    ) -> int:
        self.set_calls.append(
            {
                "category": category,
                "config_key": config_key,
                "config_value": config_value,
                "env": env,
                "description": description,
                "operator": operator,
            }
        )
        self._value = config_value
        return 1


def test_harness_poller_defaults_on_and_writable():
    """harness_poller 开关默认开启,且经 TaskSettingsService.set_enabled 写穿后 is_enabled 立即反映。

    锁定:tasks/settings 接口可直接开启/关闭 harness 旁路巡检,默认 True(常驻兜底)。
    """
    store = _FakeStore()
    svc = TaskSettingsService(config=store)

    # 默认开启(store 无值 → 返回 default=True)
    assert svc.get_enabled(setting_type=HARNESS_POLLER, env="pre") is True
    assert svc.is_enabled(HARNESS_POLLER) is True

    # 写穿:harness_poller_enabled KV 落库,is_enabled 立即 True(无需等 KV 读)
    assert svc.set_enabled(setting_type=HARNESS_POLLER, enabled=True, env="pre", operator="146836") is True
    assert len(store.set_calls) == 1
    call = store.set_calls[0]
    assert call["config_key"] == HARNESS_POLLER_KEY
    assert call["config_value"] is True
    assert call["operator"] == "146836"
    assert svc.is_enabled(HARNESS_POLLER) is True

    # 关闭回写:set_enabled 返回新状态(False),is_enabled 立即反映
    assert svc.set_enabled(setting_type=HARNESS_POLLER, enabled=False, env="pre", operator="146836") is False
    assert svc.is_enabled(HARNESS_POLLER) is False


def test_harness_poller_unsupported_type_rejected():
    """未知 setting_type 抛 ValueError(防止误传开关名静默 fail-open)。"""
    svc = TaskSettingsService(config=None)
    raised = False
    try:
        svc.get_enabled(setting_type="not_a_real_setting", env="pre")
    except ValueError:
        raised = True
    assert raised is True


def test_claim_join_filter_setting_is_retired():
    """The retired claim-join filter must not surface through task settings again."""
    from agentclaw.community.adapters.http.task.schemas import TaskSettingType

    assert "claim_join_filter" not in TaskSettingType.__args__
    svc = TaskSettingsService(config=None)
    try:
        svc.get_enabled(setting_type="claim_join_filter", env="pre")
    except ValueError:
        pass
    else:
        raise AssertionError("claim_join_filter must not be a supported task setting")
