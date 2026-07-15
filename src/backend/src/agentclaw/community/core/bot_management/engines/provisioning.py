"""Engine provisioning extension point.

This module keeps bot/device/template services engine-agnostic.  Public
services ask a strategy what should be provisioned; concrete engines own their
special template/env/token rules in their own strategy modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class BotProvisioningContext:
    """Common inputs used by engine provisioning strategies.

    All fields are optional except ``bot_id`` because many legacy call sites only
    know a subset of bot metadata.  Strategies must be defensive and return
    no-op results when they cannot prove a rule applies.
    """

    bot_id: str = ""
    owner_id: Optional[str] = None
    active_engine: Optional[str] = None
    bot_type: Optional[str] = None
    template_type: Optional[str] = None
    template_config: Optional[Dict[str, Any]] = None


@runtime_checkable
class EngineProvisioningStrategy(Protocol):
    """Per-engine provisioning hooks used by public services."""

    @property
    def engine_type(self) -> str: ...

    def build_extra_envs(self, ctx: BotProvisioningContext) -> Dict[str, str] | None:
        """Return extra env vars to inject into the runtime container."""
        ...

    def should_encrypt_template_token(self, ctx: BotProvisioningContext) -> bool:
        """Whether ``template_config['token']`` should be encrypted before persist."""
        ...

    def extract_runtime_token(self, ctx: BotProvisioningContext) -> str | None:
        """Return the token value that should be forwarded to container init."""
        ...

    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        """Post-create hook. Default strategies should no-op."""
        ...

    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        """Post-template-update hook. Default strategies should no-op."""
        ...
