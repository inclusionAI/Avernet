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
        """
        if not engine_properties:
            return PreparedBotCreate()
        if "template" in engine_properties:
            raise BotCombinationUnsupportedError(
                f"application coding does not support engine: {self.engine_type}"
            )
        raise BotTemplateInvalidError(
            "engine {} does not support engine_properties: {}".format(
                self.engine_type, sorted(engine_properties)
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
        passport_plugin: object,
        skill_set_factory: object,
        template_service: object,
    ) -> None:
        return None

    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        return None

    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        return None
