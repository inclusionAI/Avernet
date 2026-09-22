"""Unit tests for the per-engine 7×24 auto-cron eligibility capability bit.

契约（architecture gate）：cron 侧不出现引擎字符串分支；资格判定唯一入口是
``engines.registry.supports_auto_cron_setup``，per-engine 决策在策略
``supports_auto_cron_setup`` 能力位上，未注册引擎保守拒绝（registry default
no-op 策略，能力位默认 False）。
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.default import (
    DefaultProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.registry import (
    supports_auto_cron_setup,
)


class TestStrategyCapabilityBit:
    def test_abc_default_is_conservative_deny(self):
        """未声明能力位的策略默认 False（新引擎必须显式加入，不允许默认放行新来者）。"""
        for engine_type in ("openclaw", "teclaw", "hermes"):
            assert DefaultProvisioningStrategy(engine_type).supports_auto_cron_setup() is False

    def test_coding_strategies_opt_in(self):
        assert AicodingProvisioningStrategy("aicoding").supports_auto_cron_setup() is True
        assert AicodingProvisioningStrategy("claude_code").supports_auto_cron_setup() is True

    def test_registry_default_strategy_denies(self):
        assert DefaultProvisioningStrategy().supports_auto_cron_setup() is False


class TestRegistryResolution:
    """按 active_engine 经 registry 解析资格；未知/缺失引擎保守拒绝。"""

    def test_coding_engines_resolve_true(self):
        assert supports_auto_cron_setup(engine_type="aicoding") is True
        assert supports_auto_cron_setup(engine_type="claude_code") is True

    def test_alias_spellings_normalized(self):
        """公开拼写的别名（连字符/大小写/空白）经 normalize 后同样放行。"""
        assert supports_auto_cron_setup(engine_type="claude-code") is True
        assert supports_auto_cron_setup(engine_type="Claude_Code") is True
        assert supports_auto_cron_setup(engine_type="  AICODING ") is True

    def test_bare_legacy_alias_converges_to_registry_spelling(self):
        """旧别名集里额外容纳的裸拼 ``claudecode`` 不再单独豁免：与引擎 registry
        的 normalize 语义对齐（连字符→下划线），未注册拼写保守拒绝。"""
        assert supports_auto_cron_setup(engine_type="claudecode") is False

    def test_non_coding_engines_deny(self):
        assert supports_auto_cron_setup(engine_type="openclaw") is False
        assert supports_auto_cron_setup(engine_type="moltis") is False
        assert supports_auto_cron_setup(engine_type="teclaw") is False

    def test_unknown_engine_conservative_deny(self):
        assert supports_auto_cron_setup(engine_type="brand_new_engine") is False

    def test_missing_engine_deny(self):
        assert supports_auto_cron_setup(engine_type=None) is False
        assert supports_auto_cron_setup(engine_type="") is False