"""Unit tests for the per-engine 7×24 auto-cron eligibility capability bit.

契约（architecture gate）：cron 侧不出现引擎字符串分支；资格判定唯一入口是
``engines.registry.supports_auto_cron_setup``，per-engine 决策在策略
``supports_auto_cron_setup`` 能力位上，未注册引擎保守拒绝（registry default
no-op 策略，能力位默认 False）。aicoding/claude_code 家族的能力位内含模板级
守卫：仅 ``template_type`` 非空且非 legacy ``normalCC`` 放行。
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
            assert (
                DefaultProvisioningStrategy(engine_type).supports_auto_cron_setup(
                    template_type="mcptestpq"
                )
                is False
            )

    def test_registry_default_strategy_denies(self):
        assert DefaultProvisioningStrategy().supports_auto_cron_setup() is False

    def test_coding_strategies_opt_in_with_template_type(self):
        for engine_type in ("aicoding", "claude_code"):
            strategy = AicodingProvisioningStrategy(engine_type)
            assert strategy.supports_auto_cron_setup(template_type="mcptestpq") is True
            assert (
                strategy.supports_auto_cron_setup(template_type="applicationCoding")
                is True
            )

    def test_coding_strategy_template_type_guards(self):
        """模板级守卫：template_type 空 / normalCC 一律不放行。"""
        strategy = AicodingProvisioningStrategy("claude_code")
        assert strategy.supports_auto_cron_setup(template_type="normalCC") is False
        assert strategy.supports_auto_cron_setup(template_type="") is False
        assert strategy.supports_auto_cron_setup(template_type=None) is False


class TestRegistryResolution:
    """按 active_engine 经 registry 解析资格；未知/缺失引擎保守拒绝。"""

    def test_coding_engines_with_template_type_resolve_true(self):
        assert supports_auto_cron_setup(
            engine_type="claude_code", template_type="mcptestpq"
        ) is True
        assert supports_auto_cron_setup(
            engine_type="claude_code", template_type="applicationCoding"
        ) is True
        assert supports_auto_cron_setup(
            engine_type="aicoding", template_type="mcptestpq"
        ) is True

    def test_alias_spellings_normalized(self):
        """公开拼写的别名（连字符/大小写/空白）经 normalize 后同样放行。"""
        assert (
            supports_auto_cron_setup(
                engine_type="claude-code", template_type="mcptestpq"
            )
            is True
        )
        assert (
            supports_auto_cron_setup(
                engine_type="Claude_Code", template_type="mcptestpq"
            )
            is True
        )
        assert (
            supports_auto_cron_setup(
                engine_type="  AICODING ", template_type="mcptestpq"
            )
            is True
        )

    def test_template_type_empty_or_normalcc_denies_even_for_coding_engine(self):
        """claude_code/aicoding 引擎 + 空 or normalCC template_type → 不进入链路。"""
        for engine_type in ("claude_code", "aicoding", "claude-code"):
            assert (
                supports_auto_cron_setup(engine_type=engine_type, template_type="")
                is False
            )
            assert (
                supports_auto_cron_setup(engine_type=engine_type, template_type=None)
                is False
            )
            assert (
                supports_auto_cron_setup(
                    engine_type=engine_type, template_type="normalCC"
                )
                is False
            )

    def test_bare_legacy_alias_converges_to_registry_spelling(self):
        """旧别名集里额外容纳的裸拼 ``claudecode`` 不再单独豁免：与引擎 registry
        的 normalize 语义对齐（连字符→下划线），未注册拼写保守拒绝。"""
        assert (
            supports_auto_cron_setup(
                engine_type="claudecode", template_type="mcptestpq"
            )
            is False
        )

    def test_non_coding_engines_deny(self):
        for engine_type in ("openclaw", "moltis", "teclaw"):
            assert (
                supports_auto_cron_setup(
                    engine_type=engine_type, template_type="mcptestpq"
                )
                is False
            )

    def test_unknown_engine_conservative_deny(self):
        assert (
            supports_auto_cron_setup(
                engine_type="brand_new_engine", template_type="mcptestpq"
            )
            is False
        )

    def test_missing_engine_deny(self):
        assert supports_auto_cron_setup(engine_type=None, template_type="x") is False
        assert supports_auto_cron_setup(engine_type="") is False