"""Default no-op provisioning strategy."""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_management.errors import (
    BotCombinationUnsupportedError,
    BotTemplateInvalidError,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

from .provisioning import (
    BotCreateTemplateValidationMode,
    BotProvisioningContext,
    EngineProvisioningStrategy,
    PreparedBotCreate,
)


class DefaultProvisioningStrategy(EngineProvisioningStrategy):
    """No-op strategy for engines without special provisioning rules."""

    def __init__(self, engine_type: str = "default") -> None:
        self._engine_type = engine_type

    @property
    def engine_type(self) -> str:
        return self._engine_type

    def prepare_create(
        self,
        *,
        engine_type: str,
        engine_properties: dict[str, Any],
        bot_type: str,
        deployment_mode: str,
        space_kind: str,
        template_validation_mode: BotCreateTemplateValidationMode = (
            BotCreateTemplateValidationMode.LEGACY
        ),
    ) -> PreparedBotCreate:
        """Reject create-time engine properties this engine does not own.

        A ``template`` key means application-coding intent (new public contract
        or legacy normalization); the combination error keeps the historical
        409 mapping for the legacy shape instead of turning it into a 422.
        Gates replicate the deleted ``prepare_bot_create`` ladder exactly:
        error messages name the *requested* engine (the shared fallback
        instance's own ``engine_type`` would say "default"), and the cloud-only
        check still answers before the engine check.
        """
        if not engine_properties:
            return PreparedBotCreate()
        if "template" in engine_properties:
            # Historical gate order: a local deployment reports "cloud-only"
            # before the engine gate gets to answer.
            if deployment_mode != "cloud":
                raise BotCombinationUnsupportedError(
                    "application coding is cloud-only"
                )
            # claude_code resolves to the Aicoding strategy, so any engine
            # reaching this default-managed branch fails the engine gate.
            raise BotCombinationUnsupportedError(
                f"application coding does not support engine: {engine_type}"
            )
        raise BotTemplateInvalidError(
            "engine {} does not support engine_properties: {}".format(
                engine_type, sorted(engine_properties)
            )
        )

    def resolve_bot_engine(self, bot: dict[str, object]) -> str | None:
        engine = bot.get("active_engine")
        return engine if isinstance(engine, str) else None

    def build_extra_envs(self, ctx: BotProvisioningContext) -> dict[str, str] | None:
        return None

    def build_extra_properties(
        self,
        ctx: BotProvisioningContext,
        *,
        secret_resolver: SecretResolver | None = None,
        theta_master_key_secret: str = "",
    ) -> dict[str, object] | None:
        return None

    def should_encrypt_template_token(self, ctx: BotProvisioningContext) -> bool:
        return False

    def extract_runtime_token(self, ctx: BotProvisioningContext) -> str | None:
        return None

    def uses_adapter_chat_session_lifecycle(self, ctx: BotProvisioningContext) -> bool:
        return True

    def build_local_chat_session_key(
        self, ctx: BotProvisioningContext, *, user_id: str
    ) -> str:
        raise NotImplementedError(
            f"engine {ctx.active_engine or self.engine_type} uses adapter chat session lifecycle"
        )

    def apply_restart_extra_configs(
        self,
        ctx: BotProvisioningContext,
        extra_configs: dict[str, object] | None,
        *,
        template_service: object,
    ) -> None:
        return None

    def refresh_restart_authorization(
        self,
        ctx: BotProvisioningContext,
        bot: dict[str, object],
        extra_configs: dict[str, object] | None,
        *,
        mcp_sync: object = None,
        skill_set_factory: object = None,
        template_service: object = None,
    ) -> bool:
        return False

    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        return None

    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        return None
