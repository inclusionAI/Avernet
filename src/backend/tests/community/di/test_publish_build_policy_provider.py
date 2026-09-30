"""``publish_build_policy`` provider — the publish build's resource seam.

业务码不探宿主机：NAS staging / 远端 MCP catalog 是否必需由部署经
``user_config.publish_build`` 声明，composition root 解析成
``PublishBuildPolicyConfig`` 注入 ``BotBuildService``。这里钉住三件事：

* 两键缺省即生产语义（required=True，缺失响亮失败）——没有任何
  publish_build 块的部署不得静默滑向宽容行为；
* yaml 声明被逐键尊重；
* 部分声明时另一键保持默认。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.service_bot.services.bot_build_policy import (
    PublishBuildPolicyConfig,
)
from agentclaw.community.di.modules import publish_build_config_module
from agentclaw.community.di.modules.publish_build_config_module import (
    PublishBuildConfigModule,
)


def test_absent_block_resolves_to_required_defaults(monkeypatch):
    # 一个不声明 publish_build 的部署按生产语义走：全 required。
    _set_raw_block(monkeypatch, {})
    module = PublishBuildConfigModule()
    assert module.publish_build_policy() == PublishBuildPolicyConfig(
        nas_migration_required=True,
        mcp_catalog_required=True,
    )


def test_yaml_overrides_are_respected_per_key(monkeypatch):
    _set_raw_block(
        monkeypatch,
        {
            "nas_migration_required": False,
            "mcp_catalog_required": False,
        },
    )
    module = PublishBuildConfigModule()
    policy = module.publish_build_policy()
    assert policy.nas_migration_required is False
    assert policy.mcp_catalog_required is False


def test_partial_override_keeps_other_key_default(monkeypatch):
    _set_raw_block(monkeypatch, {"nas_migration_required": False})
    module = PublishBuildConfigModule()
    policy = module.publish_build_policy()
    assert policy.nas_migration_required is False
    assert policy.mcp_catalog_required is True

def test_empty_scalar_is_rejected_at_config_load(monkeypatch):
    # ``nas_migration_required:``（空值 None）必须响亮失败：静默取默认会让
    # required 部署无声跳过迁移，把坏挂载藏起来。
    _set_raw_block(monkeypatch, {"nas_migration_required": None})
    with pytest.raises(ValueError, match="nas_migration_required"):
        PublishBuildConfigModule().publish_build_policy()


def test_string_boolean_is_rejected(monkeypatch):
    # YAML 引号误裹成字符串时，bool("false") 真值语义会把宽容部署反转成
    # 响亮失败 —— 配置加载期拒绝而不是运行期惊吓。
    _set_raw_block(monkeypatch, {"mcp_catalog_required": "false"})
    with pytest.raises(ValueError, match="mcp_catalog_required"):
        PublishBuildConfigModule().publish_build_policy()


def test_unknown_keys_are_rejected(monkeypatch):
    _set_raw_block(
        monkeypatch,
        {
            "nas_migration_required": False,
            "nas_migraton_required": False,  # typo 键必须报错而不是静默忽略
        },
    )
    with pytest.raises(ValueError, match="nas_migraton_required"):
        PublishBuildConfigModule().publish_build_policy()


def _set_raw_block(monkeypatch, block: dict) -> None:
    monkeypatch.setattr(publish_build_config_module, "_block", lambda: dict(block))
