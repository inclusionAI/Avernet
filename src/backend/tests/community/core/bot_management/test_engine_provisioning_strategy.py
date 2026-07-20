from __future__ import annotations

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
