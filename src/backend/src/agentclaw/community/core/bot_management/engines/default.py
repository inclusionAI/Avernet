"""Default no-op provisioning strategy."""
from __future__ import annotations

from agentclaw.community.plugin_api.secret_resolver import SecretResolver

from .provisioning import BotProvisioningContext, EngineProvisioningStrategy


class DefaultProvisioningStrategy(EngineProvisioningStrategy):
    """No-op strategy for engines without special provisioning rules."""

    def __init__(self, engine_type: str = "default") -> None:
        self._engine_type = engine_type

    @property
    def engine_type(self) -> str:
        return self._engine_type

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

    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        return None

    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        return None
