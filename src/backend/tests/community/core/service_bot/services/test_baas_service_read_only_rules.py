from unittest.mock import MagicMock

from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.workspace.engines.claude_code import ClaudeCodeSandboxProvider
from agentclaw.community.core.workspace.engines.openclaw import OpenClawSandboxProvider
from agentclaw.community.di import config as cfg
from agentclaw.community.plugins.local.http_client import LocalHttpClient


def _make_registry() -> EngineSandboxRegistry:
    # Tests pin the workspace roots to the prod sandbox defaults so the
    # base_path assertions don't depend on the dev machine's $HOME.
    workspace = cfg.WorkspaceConfig()
    registry = EngineSandboxRegistry()
    registry.register(OpenClawSandboxProvider(workspace=workspace))
    registry.register(ClaudeCodeSandboxProvider(workspace=workspace))
    return registry


def _make_service(bot_repo=None) -> BaasService:
    return BaasService(
        baas_api_base="http://test",
        tenant="test",
        template_uuid="test",
        bot_repo=bot_repo or MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=_make_registry(),
        http_client=LocalHttpClient(),
        general_http_client=LocalHttpClient(base_url=""),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


class TestGetSetReadOnlyRule:
    def test_openclaw_default_rules(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot-1",
            "active_engine": "openclaw",
            "ext": {},
        }
        service = _make_service(bot_repo=bot_repo)

        result = service._get_set_read_only_rule(bot_id="bot-1", owner_id="owner-1")

        assert result.startswith(" --set_read_only ")
        assert "/home/admin/.openclaw/openclaw.json" in result
        assert "/home/admin/.mcporter/mcporter.json" in result

    def test_claude_code_default_rules(self):
        # claude_code 引擎的默认只读规则已经把 claude_code.json 拆成
        # settings.json / models.json / config.json 三类，并且分别覆盖
        # workspace/.claude/ 和 base 根目录两个位置;此外保留
        # workspace/config/mcporter.json。
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot-1",
            "active_engine": "claude_code",
            "ext": {},
        }
        service = _make_service(bot_repo=bot_repo)

        result = service._get_set_read_only_rule(bot_id="bot-1", owner_id="owner-1")

        assert "/home/admin/.claude_code/workspace/config/mcporter.json" in result
        assert "/home/admin/.claude_code/workspace/.claude/settings.json" in result
        assert "/home/admin/.claude_code/workspace/.claude/models.json" in result
        assert "/home/admin/.claude_code/workspace/.claude/config.json" in result
        assert "/home/admin/.claude_code/settings.json" in result
        assert "/home/admin/.claude_code/models.json" in result
        assert "/home/admin/.claude_code/config.json" in result
        # claude_code.json 已被替换,不应再出现
        assert "claude_code.json" not in result

    def test_custom_rules_support_absolute_and_relative_paths(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot-1",
            "active_engine": "claude_code",
            "ext": {
                "read_only_rules": [
                    {"path": "/tmp/custom.txt", "rule_type": "file"},
                    {"path": "workspace/custom/*.md", "rule_type": "glob"},
                ]
            },
        }
        service = _make_service(bot_repo=bot_repo)

        result = service._get_set_read_only_rule(bot_id="bot-1", owner_id="owner-1")

        assert "/tmp/custom.txt" in result
        assert "/home/admin/.claude_code/workspace/custom/*.md" in result
