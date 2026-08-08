from __future__ import annotations

import json

from agentclaw.community.core.bot_management.engines import (
    BotProvisioningContext,
    resolve_provisioning,
)
from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.registry import (
    get_engine_provisioning_registry,
)
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_baas_engine_bucket,
)


def test_resolve_baas_engine_bucket_routes_general_cc_by_template_type():
    assert (
        resolve_baas_engine_bucket(
            engine_type="claude_code",
            template_type="generalCC",
        )
        == "aicoding"
    )


def test_resolve_baas_engine_bucket_routes_architect_by_template_type():
    assert (
        resolve_baas_engine_bucket(
            engine_type="claude_code",
            template_type="architect",
        )
        == "aicoding"
    )


def test_resolve_baas_engine_bucket_routes_architect_without_template_config():
    assert (
        resolve_baas_engine_bucket(
            engine_type="claude_code",
            template_type="architect",
        )
        == "aicoding"
    )


def test_resolve_baas_engine_bucket_keeps_normal_cc_on_claude_code():
    assert (
        resolve_baas_engine_bucket(
            engine_type="claude-code",
            template_type="normalCC",
        )
        == "claude_code"
    )


def test_aicoding_strategy_personal_coding_model_runtime_and_token():
    strategy = AicodingProvisioningStrategy("aicoding")
    ctx = BotProvisioningContext(
        active_engine="aicoding",
        template_type="personalCoding",
        bot_id="b1",
        owner_id="u1",
        bot_type="personal",
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
        bot_id="b1",
        owner_id="u1",
        bot_type="service",
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
        bot_id="b1",
        owner_id="u1",
        bot_type="application",
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
        bot_id="b1",
        owner_id="u1",
        bot_type="service",
        template_config={"token": "tok", "model": "m1", "runtime": "r1"},
    )
    strategy = get_engine_provisioning_registry().resolve_for_context(ctx)

    assert strategy.build_extra_envs(ctx) is None
    assert strategy.should_encrypt_template_token(ctx) is False
    assert strategy.extract_runtime_token(ctx) is None


def test_register_duplicate_engine_raises():
    """Duplicate registration must fail loudly rather than silently overwrite."""
    from agentclaw.community.core.bot_management.engines.registry import (
        EngineProvisioningRegistry,
    )
    from agentclaw.community.core.bot_management.engines.default import (
        DefaultProvisioningStrategy,
    )

    registry = EngineProvisioningRegistry()
    registry.register(DefaultProvisioningStrategy("custom"))
    try:
        registry.register(DefaultProvisioningStrategy("custom"))
    except ValueError:
        return
    raise AssertionError("expected ValueError on duplicate registration")


def test_registry_is_singleton_across_calls():
    """The module accessor must hand back the same registry instance each call."""
    a = get_engine_provisioning_registry()
    b = get_engine_provisioning_registry()
    assert a is b


def test_unknown_engine_falls_back_to_default():
    """Engines without an explicit strategy (here: moltis) get the default no-op.

    moltis is intentionally not registered in the provisioning registry; it
    must still resolve to the default no-op strategy so legacy call sites keep
    working.
    """
    ctx = BotProvisioningContext(
        bot_id="b1",
        owner_id="u1",
        bot_type="t",
        active_engine="moltis",
        template_type=None,
    )
    strategy = get_engine_provisioning_registry().resolve_for_context(ctx)

    assert strategy.build_extra_envs(ctx) is None
    assert strategy.should_encrypt_template_token(ctx) is False
    assert strategy.extract_runtime_token(ctx) is None


def test_resolve_accepts_str_only_and_recognizes_known_engines():
    """resolve() takes a real engine string; unknown -> default, known -> that engine."""
    from agentclaw.community.core.bot_management.engines.registry import (
        EngineProvisioningRegistry,
    )

    registry = EngineProvisioningRegistry()
    # resolve is strict str API (no Optional[None]); unknown -> default.
    assert registry.resolve("does-not-exist").engine_type == "default"


def test_resolve_provisioning_builds_context_and_strategy():
    """Single entry point returns (ctx, strategy) with resolution applied."""
    ctx, strategy = resolve_provisioning(
        bot_id="b1",
        owner_id="u1",
        bot_type="personal",
        active_engine="aicoding",
        template_type="personalCoding",
        template_config={"model": "m1", "runtime": "r1", "token": "tok"},
    )
    assert ctx.bot_id == "b1"
    assert ctx.active_engine == "aicoding"
    assert strategy.engine_type == "aicoding"
    # strategy is the resolved one — exercising its hooks proves wiring
    assert strategy.build_extra_envs(ctx) == {
        "BOT_TYPE": "personal",
        "RELAY_DEFAULT_MODEL": "m1",
        "RELAY_DEFAULT_RUNTIME": "r1",
    }
    assert strategy.extract_runtime_token(ctx) == "tok"


def test_resolve_provisioning_routes_legacy_template_only_context():
    """active_engine=None + coding template_type routes to the coding strategy."""
    ctx, strategy = resolve_provisioning(
        bot_id="b1",
        owner_id="u1",
        bot_type="personal",
        active_engine=None,
        template_type="personalCoding",
        template_config={"token": "tok"},
    )
    assert strategy.engine_type == "aicoding"
    assert strategy.should_encrypt_template_token(ctx) is True


def test_template_factory_normal_cc_consumes_model_runtime_repos_and_token():
    strategy = AicodingProvisioningStrategy("claude_code")
    ctx = BotProvisioningContext(
        active_engine="claude_code",
        template_type="normalCC",
        bot_id="b1",
        owner_id="u1",
        bot_type="service",
        template_config={
            "template_key": "normalCC",
            "template_uid": "aicoding",
            "bot_template_config": {
                "engine_config": {"type": ["claude-code", "codefuse-antcc"]},
            },
            "model": "  m-normal  ",
            "runtime": "  codefuse-antcc  ",
            "token": "tok-normal",
            "repos": ["https://code/repo1"],
            "init_repos": [{"url": "https://code/repo2"}],
        },
    )

    envs = strategy.build_extra_envs(ctx)
    assert envs is not None
    assert envs["RELAY_DEFAULT_MODEL"] == "m-normal"
    assert envs["RELAY_DEFAULT_RUNTIME"] == "codefuse-antcc"
    assert json.loads(envs["GIT_ADDRESSES"]) == [
        "https://code/repo1",
        "https://code/repo2",
    ]
    assert envs["BOT_TYPE"] == "normalCC"
    assert strategy.should_encrypt_template_token(ctx) is True
    assert strategy.extract_runtime_token(ctx) == "tok-normal"


def test_template_factory_architect_routes_by_template_config_not_template_type_enum():
    ctx, strategy = resolve_provisioning(
        bot_id="b1",
        owner_id="u1",
        bot_type="service",
        active_engine="claude_code",
        template_type="architect",
        template_config={
            "template_key": "architect",
            "template_uid": "aicoding",
            "bot_template_config": {
                "engine_config": {"type": ["claude-code"]},
            },
            "token": "tok-architect",
        },
    )

    assert strategy.engine_type == "claude_code"
    assert strategy.should_encrypt_template_token(ctx) is True
    assert strategy.extract_runtime_token(ctx) == "tok-architect"


def test_user_created_template_factory_config_consumed_without_backend_template_type_enum():
    """AC 用户自建模板不应要求后端为每个 template_type 发版加枚举。"""
    strategy = AicodingProvisioningStrategy("claude_code")
    ctx = BotProvisioningContext(
        active_engine="claude_code",
        template_type="userCustomTemplate",
        bot_id="b1",
        owner_id="u1",
        bot_type="personal",
        template_config={
            "template_key": "userCustomTemplate",
            "template_uid": "aicoding",
            "bot_template_config": {
                "engine_config": {"type": ["claude-code"]},
            },
            "model": "  custom-model  ",
            "runtime": "  codefuse-antcc  ",
            "token": "tok-custom",
            "repos": ["https://code/custom-repo"],
        },
    )

    envs = strategy.build_extra_envs(ctx)
    assert envs is not None
    assert envs["RELAY_DEFAULT_MODEL"] == "custom-model"
    assert envs["RELAY_DEFAULT_RUNTIME"] == "codefuse-antcc"
    assert json.loads(envs["GIT_ADDRESSES"]) == ["https://code/custom-repo"]
    assert envs["BOT_TYPE"] == "userCustomTemplate"
    assert strategy.should_encrypt_template_token(ctx) is True
    assert strategy.extract_runtime_token(ctx) == "tok-custom"


def test_claude_code_other_template_type_consumes_identified_template_config():
    """claude_code + 非 legacy template_type 需有 template_key/template_uid 才消费。"""
    strategy = AicodingProvisioningStrategy("claude_code")
    ctx = BotProvisioningContext(
        active_engine="claude_code",
        template_type="customCC",
        bot_id="b1",
        owner_id="u1",
        bot_type="personal",
        template_config={
            "template_key": "customCC",
            "template_uid": "tpl-custom-cc",
            "model": "  custom-model  ",
            "runtime": "  codefuse-antcc  ",
            "token": "tok-custom",
            "repos": ["https://code/custom-repo"],
        },
    )

    envs = strategy.build_extra_envs(ctx)
    assert envs is not None
    assert envs["BOT_TYPE"] == "customCC"
    assert envs["RELAY_DEFAULT_MODEL"] == "custom-model"
    assert envs["RELAY_DEFAULT_RUNTIME"] == "codefuse-antcc"
    assert json.loads(envs["GIT_ADDRESSES"]) == ["https://code/custom-repo"]
    assert strategy.should_encrypt_template_token(ctx) is True
    assert strategy.extract_runtime_token(ctx) == "tok-custom"


def test_claude_code_other_template_type_ignores_incomplete_template_identity():
    """缺 template_key 或 template_uid 的普通 dict 不能触发非 legacy 模板消费。"""
    strategy = AicodingProvisioningStrategy("claude_code")
    for template_config in (
        {"model": "custom-model", "runtime": "codefuse-antcc", "token": "tok-custom"},
        {"template_key": "customCC", "model": "custom-model", "token": "tok-custom"},
        {"template_uid": "tpl-custom-cc", "model": "custom-model", "token": "tok-custom"},
    ):
        ctx = BotProvisioningContext(
            active_engine="claude_code",
            template_type="customCC",
            bot_id="b1",
            owner_id="u1",
            bot_type="personal",
            template_config=template_config,
        )

        assert strategy.build_extra_envs(ctx) is None
        assert strategy.should_encrypt_template_token(ctx) is False
        assert strategy.extract_runtime_token(ctx) is None


def test_non_coding_engine_does_not_consume_user_template_factory_config():
    """非 coding 引擎即便带模板工厂形态配置，也不能误走 AICoding provisioning。"""
    ctx = BotProvisioningContext(
        active_engine="openclaw",
        template_type="userCustomTemplate",
        bot_id="b1",
        owner_id="u1",
        bot_type="service",
        template_config={
            "template_key": "userCustomTemplate",
            "template_uid": "aicoding",
            "bot_template_config": {
                "engine_config": {"type": ["claude-code"]},
            },
            "model": "custom-model",
            "runtime": "codefuse-antcc",
            "token": "tok-custom",
        },
    )
    strategy = get_engine_provisioning_registry().resolve_for_context(ctx)

    assert strategy.build_extra_envs(ctx) is None
    assert strategy.should_encrypt_template_token(ctx) is False
    assert strategy.extract_runtime_token(ctx) is None


def test_template_only_user_template_factory_config_without_active_engine_is_noop():
    """用户自建模板不能靠后端遍历 engine_config.type 猜策略；创建链路应传 active_engine。"""
    ctx, strategy = resolve_provisioning(
        bot_id="b1",
        owner_id="u1",
        bot_type="personal",
        active_engine=None,
        template_type="userCustomTemplate",
        template_config={
            "template_key": "userCustomTemplate",
            "bot_template_config": {
                "engine_config": {"type": ["claude-code"]},
            },
            "token": "tok-custom",
        },
    )

    assert strategy.engine_type == "default"
    assert strategy.should_encrypt_template_token(ctx) is False
    assert strategy.extract_runtime_token(ctx) is None
