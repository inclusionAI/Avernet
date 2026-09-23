"""``publish_build_policy`` provider — the publish build's resource seam.

业务码不探宿主机：NAS staging / 远端 MCP catalog 是否必需由部署经
``user_config.publish_build`` 声明，composition root 解析成
``PublishBuildPolicyConfig`` 注入 ``BotBuildService``。这里钉住三件事：

* 两键缺省即生产语义（required=True，缺失响亮失败）——没有任何
  publish_build 块的部署不得静默滑向宽容行为；
* yaml 声明被逐键尊重；
* 未知子键被忽略（防劣化部署配置直接炸初始化）。
"""
from __future__ import annotations

from agentclaw.community.di.config import PublishBuildPolicyConfig
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


def _set_user_config(monkeypatch, user_config: dict) -> None:
    monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))


def test_absent_block_resolves_to_required_defaults(monkeypatch):
    # 一个不声明 publish_build 的部署按生产语义走：全 required。
    _set_user_config(monkeypatch, {})
    module = ConfigModule()
    assert module.publish_build_policy() == PublishBuildPolicyConfig(
        nas_migration_required=True,
        mcp_catalog_required=True,
    )


def test_yaml_overrides_are_respected_per_key(monkeypatch):
    _set_user_config(
        monkeypatch,
        {
            "publish_build": {
                "nas_migration_required": False,
                "mcp_catalog_required": False,
            }
        },
    )
    module = ConfigModule()
    policy = module.publish_build_policy()
    assert policy.nas_migration_required is False
    assert policy.mcp_catalog_required is False


def test_partial_override_keeps_other_key_default(monkeypatch):
    _set_user_config(
        monkeypatch,
        {"publish_build": {"nas_migration_required": False}},
    )
    module = ConfigModule()
    policy = module.publish_build_policy()
    assert policy.nas_migration_required is False
    assert policy.mcp_catalog_required is True