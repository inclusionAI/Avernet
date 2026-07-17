from __future__ import annotations

from agentclaw.community.core.bot_management.engines import BotProvisioningContext
from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.registry import (
    get_engine_provisioning_registry,
)


def test_aicoding_strategy_personal_coding_model_runtime_and_token():
    strategy = AicodingProvisioningStrategy("aicoding")
    ctx = BotProvisioningContext(
        active_engine="aicoding",
        template_type="personalCoding",
        template_config={
            "model": "  m1  ",
            "runtime": "  codefuse-antcc  ",
            "token": "tok",
        },
    )

    envs = strategy.build_extra_envs(ctx)
    assert envs == {
        "BOT_TYPE": "personal",
        "RELAY_DEFAULT_MODEL": "m1",
        "RELAY_DEFAULT_RUNTIME": "codefuse-antcc",
    }
    assert strategy.should_encrypt_template_token(ctx) is True
    assert strategy.extract_runtime_token(ctx) == "tok"


def test_default_strategy_noops_for_non_coding_template():
    ctx = BotProvisioningContext(
        active_engine="openclaw",
        template_type="normalCC",
        template_config={"model": "m1", "runtime": "r1", "token": "tok"},
    )
    strategy = get_engine_provisioning_registry().resolve_for_context(ctx)

    assert strategy.build_extra_envs(ctx) is None
    assert strategy.should_encrypt_template_token(ctx) is False
    assert strategy.extract_runtime_token(ctx) is None


def test_aicoding_strategy_ignores_non_list_repo_fields():
    strategy = AicodingProvisioningStrategy("aicoding")
    ctx = BotProvisioningContext(
        active_engine="aicoding",
        template_type="applicationCoding",
        template_config={
            "backend_repo": {"repo_url": "git@bad/dict.git"},
            "frontend_repo": "not-a-list",
            "lib_repo": [{"repo_url": "git@good/lib.git"}],
        },
    )

    envs = strategy.build_extra_envs(ctx)
    assert envs is not None
    assert envs["GIT_ADDRESSES"] == '["git@good/lib.git"]'


def test_explicit_non_coding_engine_takes_precedence_over_coding_template():
    """Explicit non-coding engine should stay no-op even with legacy coding template.

    Template-only fallback exists for legacy call sites without active_engine
    (for example TemplateService).  Once an explicit engine is present, the
    engine is authoritative so dirty data such as openclaw + personalCoding does
    not accidentally receive AICoding env/token provisioning.
    """
    ctx = BotProvisioningContext(
        active_engine="openclaw",
        template_type="personalCoding",
        template_config={"token": "tok", "model": "m1", "runtime": "r1"},
    )
    strategy = get_engine_provisioning_registry().resolve_for_context(ctx)

    assert strategy.build_extra_envs(ctx) is None
    assert strategy.should_encrypt_template_token(ctx) is False
    assert strategy.extract_runtime_token(ctx) is None
