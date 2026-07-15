"""Default no-op provisioning strategy."""
from __future__ import annotations

from .provisioning import BotProvisioningContext


class DefaultProvisioningStrategy:
    """No-op strategy for engines without special provisioning rules."""

    def __init__(self, engine_type: str = "default") -> None:
        self._engine_type = engine_type

    @property
    def engine_type(self) -> str:
        return self._engine_type

    def build_extra_envs(self, ctx: BotProvisioningContext):
        return None

    def should_encrypt_template_token(self, ctx: BotProvisioningContext) -> bool:
        return False

    def extract_runtime_token(self, ctx: BotProvisioningContext):
        return None

    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        return None

    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        return None
