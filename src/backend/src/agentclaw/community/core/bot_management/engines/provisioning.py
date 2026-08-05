"""Engine provisioning extension point.

This module keeps bot/device/template services engine-agnostic.  Public
services ask a strategy what should be provisioned; concrete engines own their
special template/env/token rules in their own strategy modules.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class AgentCodingBotParams:
    """Opaque AgentCoding Bot parameters forwarded to the sandbox boundary."""

    theta_key: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {"theta_key": self.theta_key}


@dataclass(frozen=True)
class BotProvisioningContext:
    """Common inputs used by engine provisioning strategies.

    ``bot_id`` / ``owner_id`` / ``bot_type`` are required ``str`` (no defaults)
    so call sites must pass them explicitly — safer than silent empty defaults.
    Pass ``""`` at legacy sites that genuinely lack the value (e.g. TemplateService
    only knows ``template_type``).

    ``active_engine`` is ``Optional[str]`` because it is not always resolvable at
    legacy call sites; ``resolve_for_context`` falls back to ``template_type`` /
    default when it is ``None``.

    ``template_type`` / ``template_config`` are legitimately ``Optional``:
    only template-backed coding engines (``aicoding`` / ``claude_code``) need
    them.  For engines without a coding template they stay ``None`` and are
    intentionally not provisioned.
    """

    bot_id: str
    owner_id: str
    bot_type: str
    active_engine: Optional[str] = None
    # ``None`` for non-template engines (openclaw / teclaw / hermes / ...).
    # Coding engines set this to ``applicationCoding`` / ``personalCoding``.
    template_type: Optional[str] = None
    # ``None`` together with ``template_type`` for non-template engines.
    # Coding engines read overrides (model / runtime / token / repos /
    # devflow) from here.
    template_config: Optional[Dict[str, Any]] = None


class EngineProvisioningStrategy(ABC):
    """Per-engine provisioning hooks used by public services.

    Concrete engines subclass this ABC instead of an ad-hoc Protocol so missing
    hooks are caught at import time and the contract is explicit (no duck-typing
    benefit here since we always own the implementations).
    """

    @property
    @abstractmethod
    def engine_type(self) -> str:
        """Stable identifier this strategy is registered under."""

    @abstractmethod
    def build_extra_envs(self, ctx: BotProvisioningContext) -> Dict[str, str] | None:
        """Return extra env vars to inject into the runtime container."""

    def build_agent_coding_bot_params(
        self, ctx: BotProvisioningContext
    ) -> AgentCodingBotParams | None:
        """Return optional AgentCoding-specific sandbox parameters.

        The default is a no-op so non-coding engines and future strategies do
        not need to know about AgentCoding template fields.
        """
        return None

    @abstractmethod
    def should_encrypt_template_token(self, ctx: BotProvisioningContext) -> bool:
        """Whether ``template_config['token']`` should be encrypted before persist."""

    @abstractmethod
    def extract_runtime_token(self, ctx: BotProvisioningContext) -> str | None:
        """Return the token value that should be forwarded to container init."""

    @abstractmethod
    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        """Post-create hook. Default strategies should no-op."""

    @abstractmethod
    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        """Post-template-update hook. Default strategies should no-op."""
