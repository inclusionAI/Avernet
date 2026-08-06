"""Composition root for engine provisioning strategies."""

from __future__ import annotations

from typing import Any

from agentclaw.community.log import get_logger

from .aicoding.strategy import AicodingProvisioningStrategy, CODING_TEMPLATE_TYPES
from .default import DefaultProvisioningStrategy
from .provisioning import (
    EngineExtraProperties,
    ExtraPropertiesContributor,
    BotProvisioningContext,
    EngineProvisioningStrategy,
)

logger = get_logger()


class EngineProvisioningRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, EngineProvisioningStrategy] = {}
        self._extra_properties_contributors: list[ExtraPropertiesContributor] = []
        self._default = DefaultProvisioningStrategy()

    def register(self, strategy: EngineProvisioningStrategy) -> None:
        engine_type = strategy.engine_type
        if engine_type in self._strategies:
            # Guard against accidental double registration which would silently
            # overwrite a previously installed strategy.
            raise ValueError(
                f"engine provisioning strategy already registered: {engine_type}"
            )
        self._strategies[engine_type] = strategy

    def register_extra_properties_contributor(
        self, contributor: ExtraPropertiesContributor
    ) -> None:
        self._extra_properties_contributors.append(contributor)

    def build_extra_properties(
        self, ctx: BotProvisioningContext
    ) -> EngineExtraProperties | None:
        """Merge engine and independent extension contributions.

        The registry is the composition seam: generic callers pass one context,
        while each extension independently decides whether it applies.
        """
        merged: dict[str, Any] = {}
        strategy_properties = self.resolve_for_context(ctx).build_extra_properties(ctx)
        if strategy_properties is not None:
            merged.update(strategy_properties.to_dict())
        for contributor in self._extra_properties_contributors:
            properties = contributor.contribute(ctx)
            if properties is not None:
                merged.update(properties.to_dict())
        return EngineExtraProperties(merged) if merged else None

    def resolve(self, engine_type: str) -> EngineProvisioningStrategy:
        """Resolve the strategy for a known engine type.

        Unknown engine types fall back to the default no-op strategy.  Call
        sites that may not have ``active_engine`` at all (legacy / partial
        metadata) should use ``resolve_for_context`` instead of passing a
        sentinel here.
        """
        return self._strategies.get(engine_type, self._default)

    def resolve_for_context(
        self, ctx: BotProvisioningContext
    ) -> EngineProvisioningStrategy:
        """Resolve a strategy for legacy call sites with partial metadata.

        Prefer ``active_engine``.  If older call sites only pass
        ``template_type``, route known legacy coding templates to the coding
        strategy so historical TemplateService signatures keep working while
        keeping the rule in one place.  Template-factory templates should be
        detected from active_engine + template_config at the strategy layer; do
        not infer them here from template keys or engine_config lists.  An
        explicit (non-empty) engine always wins over a coding ``template_type``,
        so dirty data such as ``openclaw`` + ``personalCoding`` does not
        accidentally get AICoding provisioning.
        """
        if ctx.active_engine:
            return self.resolve(ctx.active_engine)
        if ctx.template_type in CODING_TEMPLATE_TYPES:
            return self.resolve("aicoding")
        return self._default


def _build_default_registry() -> EngineProvisioningRegistry:
    """Assemble the process-wide strategy registry with all known engines.

    Pure and side-effect free: it only instantiates lightweight strategy
    objects.  Built eagerly at import so the single-instance contract is
    obvious and no lazy double-checked-locking is needed.
    """
    registry = EngineProvisioningRegistry()
    registry.register(AicodingProvisioningStrategy("aicoding"))
    registry.register(AicodingProvisioningStrategy("claude_code"))
    # Cross-engine platform extensions (auditing/billing/...) may register an
    # ExtraPropertiesContributor here. AICoding single-domain fields are NOT
    # registered this way — they live in AicodingProvisioningStrategy so one
    # engine class owns every AICoding template field.
    for engine_type in ("openclaw", "teclaw", "hermes"):
        registry.register(DefaultProvisioningStrategy(engine_type))
    return registry


# Eager module-level singleton: built exactly once at import.  The registry is
# bootstrap state (a fixed set of strategies), so there is no need for lazy DCL.
_REGISTRY: EngineProvisioningRegistry = _build_default_registry()


def get_engine_provisioning_registry() -> EngineProvisioningRegistry:
    """Return the process-wide strategy registry."""
    return _REGISTRY


def resolve_provisioning(
    *,
    bot_id: str,
    owner_id: str,
    bot_type: str,
    active_engine: str | None = None,
    template_type: str | None = None,
    template_config: dict[str, Any] | None = None,
) -> tuple[BotProvisioningContext, EngineProvisioningStrategy]:
    """Build the provisioning context and resolve the engine strategy.

    Single entry point so ``BotProvisioningContext`` construction + strategy
    resolution live in one place.  Every service call site (BotService,
    BaasDeviceService, TemplateService, utils) goes through here instead of
    repeating ``BotProvisioningContext(...)`` + ``resolve_for_context(...)``.

    Returns the built context together with the resolved strategy so the caller
    can invoke the specific hook it needs (``build_extra_envs`` /
    ``extract_runtime_token`` / ``should_encrypt_template_token``).
    """
    ctx = BotProvisioningContext(
        bot_id=bot_id,
        owner_id=owner_id,
        bot_type=bot_type,
        active_engine=active_engine,
        template_type=template_type,
        template_config=template_config,
    )
    strategy = get_engine_provisioning_registry().resolve_for_context(ctx)
    return ctx, strategy


def build_extra_properties_fail_open(
    *,
    bot_id: str,
    owner_id: str,
    bot_type: str,
    active_engine: str | None = None,
    template_type: str | None = None,
    template_config: dict[str, Any] | None = None,
    log_context: str = "engine_provisioning",
) -> dict[str, Any] | None:
    """Build opaque provisioning properties (as a plain dict) without blocking.

    Strategy resolution and execution are extension hooks. Returns the engine
    property envelope as a plain ``dict`` so generic lifecycle services forward
    it verbatim with no engine knowledge. Any failure falls back to ``None`` so
    the legacy path is preserved.
    """
    try:
        ctx, _ = resolve_provisioning(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_type=bot_type,
            active_engine=active_engine,
            template_type=template_type,
            template_config=template_config,
        )
        props = get_engine_provisioning_registry().build_extra_properties(ctx)
        return props.to_dict() if props is not None else None
    except Exception as exc:
        logger.warning(
            "[%s] Provisioning extra properties fallback=none bot_id=%s error_type=%s",
            log_context,
            bot_id,
            type(exc).__name__,
        )
        return None


# Compatibility alias for callers outside this repository. Generic lifecycle
# code uses the engine-agnostic name above.
build_engine_extra_properties_fail_open = build_extra_properties_fail_open


def build_extra_envs_fail_open(
    *,
    bot_id: str,
    owner_id: str,
    bot_type: str,
    active_engine: str | None = None,
    template_type: str | None = None,
    template_config: dict[str, Any] | None = None,
    log_context: str = "engine_provisioning",
) -> dict[str, str] | None:
    """Build engine extra envs without blocking generic provisioning.

    Single fail-open entry point for the ``build_extra_envs`` hook so call sites
    stop hand-rolling ``resolve_provisioning`` + try/except. Any failure falls
    back to ``None``.
    """
    try:
        ctx, strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_type=bot_type,
            active_engine=active_engine,
            template_type=template_type,
            template_config=template_config,
        )
        extra_envs = strategy.build_extra_envs(ctx)
        if extra_envs:
            logger.info(
                "[%s] Setting engine extra_envs for bot %s: %s",
                log_context,
                bot_id,
                extra_envs,
            )
        return extra_envs
    except Exception as exc:
        logger.warning(
            "[%s] Provisioning extra_envs fallback=none bot_id=%s error_type=%s",
            log_context,
            bot_id,
            type(exc).__name__,
        )
        return None


def extract_runtime_token_fail_open(
    *,
    bot_id: str,
    owner_id: str,
    bot_type: str,
    active_engine: str | None = None,
    template_type: str | None = None,
    template_config: dict[str, Any] | None = None,
    log_context: str = "engine_provisioning",
) -> str | None:
    """Resolve the engine runtime token without blocking generic provisioning.

    Single fail-open entry point for the ``extract_runtime_token`` hook so the
    token-refresh path stops hand-rolling ``resolve_provisioning`` + try/except.
    """
    try:
        ctx, strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_type=bot_type,
            active_engine=active_engine,
            template_type=template_type,
            template_config=template_config,
        )
        token = strategy.extract_runtime_token(ctx)
        if token:
            logger.info(
                "[%s] Resolved engine runtime token for bot %s",
                log_context,
                bot_id,
            )
        return token
    except Exception as exc:
        logger.warning(
            "[%s] Provisioning runtime token fallback=none bot_id=%s error_type=%s",
            log_context,
            bot_id,
            type(exc).__name__,
        )
        return None


def should_encrypt_template_token_fail_open(
    *,
    bot_id: str,
    owner_id: str,
    bot_type: str,
    active_engine: str | None = None,
    template_type: str | None = None,
    template_config: dict[str, Any] | None = None,
    log_context: str = "engine_provisioning",
) -> bool:
    """Ask the engine strategy whether to encrypt the template token.

    Single entry point for the ``should_encrypt_template_token`` hook. Unlike
    the extra-properties/envs fail-open seams (which return an empty envelope
    / None on failure — a safe no-op), the token-encryption decision controls
    whether a plaintext credential is persisted to ``ac_templates.ext``. A
    fail-open ``False`` here would persist the plaintext token on any
    provisioning/strategy failure, which is an irreversible security breach.

    Therefore this seam is **fail-closed on credential persistence**: on any
    extension failure it returns ``True`` (conservative: encrypt). The caller
    :func:`TemplateService._encrypt_token_field` already skips encryption when
    there is no token or the token is already ciphertext, so a conservative
    ``True`` only encrypts an actual plaintext token and never breaks templates
    that carry no token.
    """
    try:
        ctx, strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_type=bot_type,
            active_engine=active_engine,
            template_type=template_type,
            template_config=template_config,
        )
        return strategy.should_encrypt_template_token(ctx)
    except Exception as exc:
        logger.warning(
            "[%s] Provisioning encrypt check fallback=closed(encrypt) bot_id=%s error_type=%s",
            log_context,
            bot_id,
            type(exc).__name__,
        )
        # Fail-closed on credential persistence: never let a plaintext token
        # reach storage when we cannot determine the engine policy.
        return True


def _coerce_template_config(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def build_extra_envs_from_bot(
    *, bot: dict[str, Any], log_context: str = "engine_provisioning"
) -> dict[str, str] | None:
    """Build engine extra envs from a bot dict via the strategy seam.

    Adapts a bot record to the provisioning context and delegates to
    ``build_extra_envs_fail_open``. Generic services call this instead of
    hand-rolling bot-dict field extraction + strategy resolution.
    """
    return build_extra_envs_fail_open(
        bot_id=str(bot.get("bot_id") or ""),
        owner_id=str(bot.get("owner_id") or ""),
        bot_type=str(bot.get("bot_type") or ""),
        active_engine=bot.get("active_engine"),
        template_type=bot.get("template_type"),
        template_config=_coerce_template_config(bot.get("template_config")),
        log_context=log_context,
    )


def build_extra_properties_from_bot(
    *, bot: dict[str, Any], log_context: str = "engine_provisioning"
) -> dict[str, Any] | None:
    """Build opaque provisioning properties from a bot dict via the strategy seam.

    Adapts a bot record to the provisioning context and delegates to
    ``build_extra_properties_fail_open``. Generic services call this instead of
    hand-rolling bot-dict field extraction + fail-open + ``to_dict``.
    """
    return build_extra_properties_fail_open(
        bot_id=str(bot.get("bot_id") or ""),
        owner_id=str(bot.get("owner_id") or ""),
        bot_type=str(bot.get("bot_type") or ""),
        active_engine=bot.get("active_engine"),
        template_type=bot.get("template_type"),
        template_config=_coerce_template_config(bot.get("template_config")),
        log_context=log_context,
    )



__all__ = [
    "EngineExtraProperties",
    "BotProvisioningContext",
    "EngineProvisioningRegistry",
    "build_extra_properties_fail_open",
    "build_engine_extra_properties_fail_open",
    "get_engine_provisioning_registry",
    "resolve_provisioning",
    "build_extra_envs_fail_open",
    "extract_runtime_token_fail_open",
    "should_encrypt_template_token_fail_open",
    "build_extra_envs_from_bot",
    "build_extra_properties_from_bot",
]
