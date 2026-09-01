"""Engine provisioning extension point.

This module keeps bot/device/template services engine-agnostic.  Public
services ask a strategy what should be provisioned; concrete engines own their
special template/env/token rules in their own strategy modules.

It also carries the create-time prevalidation contract shared by the create
flow and the strategies: :class:`PreparedBotCreate` and the neutral template
sanitation helpers.  Strategies must not import ``create_flow`` (that would
cycle), so everything both sides need lives here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Dict, Optional

from agentclaw.community.core.bot_management.errors import BotTemplateInvalidError
from agentclaw.community.core.workspace.runtime_identity import ENGINE_FORM_KEY
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

# Public template input must not set platform-owned identity or lifecycle data.
# ``engine_form`` is the server-managed form marker written only by creation
# normalization (legacy ``aicoding`` engine folded into ``claude_code``).
TEMPLATE_SERVER_RESERVED_FIELDS = frozenset(
    {
        "workspace_id",
        "template_uid",
        "bot_id",
        "workspace_status",
        "workspace_state",
        "start_status",
        ENGINE_FORM_KEY,
    }
)


@dataclass(frozen=True)
class PreparedBotCreate:
    """Sanitized creation attributes plus required platform capabilities.

    ``template_type`` / ``template_config`` are the Core-internal compatibility
    contract consumed by ``BotService.create_bot``; strategies translate their
    engine-owned properties into it.
    """

    template_type: Optional[str] = None
    template_config: Optional[Dict[str, Any]] = None
    requires_workspace_hosting: bool = False


class BotCreateTemplateValidationMode(StrEnum):
    """Template validation contract selected by the caller's API surface.

    ``PUBLIC`` keeps the strict ownership rules of the OpenAPI contract;
    ``LEGACY`` (the default for internal callers) accepts established internal
    template snapshots that may contain platform-managed fields such as
    ``template_uid``.
    """

    LEGACY = "legacy"
    PUBLIC = "public"


def to_internal_template_config(
    value: Dict[str, Any] | None,
    *,
    reject_server_managed_fields: bool = True,
) -> Dict[str, Any] | None:
    """Validate public ownership rules and detach caller-owned input.

    The default keeps this helper's public-input behavior. Legacy internal
    callers use the explicit opt-out because their template config is an
    established internal snapshot that may contain platform-managed fields.
    """
    if value is None:
        return None
    if reject_server_managed_fields:
        reserved = sorted(TEMPLATE_SERVER_RESERVED_FIELDS.intersection(value))
        if reserved:
            raise BotTemplateInvalidError(
                f"template_config contains server-managed fields: {reserved}"
            )
    return deepcopy(value)


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
    def prepare_create(
        self,
        *,
        engine_type: str,
        engine_properties: Dict[str, Any],
        bot_type: str,
        deployment_mode: str,
        space_kind: str,
        template_validation_mode: BotCreateTemplateValidationMode = (
            BotCreateTemplateValidationMode.LEGACY
        ),
    ) -> PreparedBotCreate:
        """Validate engine-owned create input before side effects.

        ``engine_type`` is the engine the request asked for. It equals this
        instance's registration key, except for the shared default fallback
        instance, which serves *every* unregistered engine — so error messages
        must name this parameter's value, never ``self.engine_type``.

        ``engine_properties`` is the opaque bag routed by engine type; only this
        strategy may interpret its keys. Called before Passport apply and any
        persistence, so every violation must raise, never silently drop input.

        ``{"template_config": None}`` is a Core-only legacy compatibility shape (the
        public schema requires a non-empty object): the key's *presence* carries
        the application-coding intent when a legacy caller omitted the config.

        ``template_validation_mode`` carries the caller's surface: public
        inputs get full ownership validation, legacy internal snapshots stay
        accepted verbatim.
        """

    @abstractmethod
    def resolve_bot_engine(self, bot: Dict[str, Any]) -> str | None:
        """Resolve the effective runtime engine for a bot record."""

    @abstractmethod
    def build_extra_envs(self, ctx: BotProvisioningContext) -> Dict[str, str] | None:
        """Return extra env vars to inject into the runtime container."""

    @abstractmethod
    def build_extra_properties(
        self,
        ctx: BotProvisioningContext,
        *,
        secret_resolver: SecretResolver | None = None,
        theta_master_key_secret: str = "",
    ) -> dict[str, Any] | None:
        """Resolve engine-owned template fields into a generic runtime envelope."""

    @abstractmethod
    def should_encrypt_template_token(self, ctx: BotProvisioningContext) -> bool:
        """Whether ``template_config['token']`` should be encrypted before persist."""

    @abstractmethod
    def extract_runtime_token(self, ctx: BotProvisioningContext) -> str | None:
        """Return the token value that should be forwarded to container init."""

    @abstractmethod
    def uses_adapter_chat_session_lifecycle(self, ctx: BotProvisioningContext) -> bool:
        """Whether ExpertChat should manage chat sessions through Adapter APIs.

        ``True`` preserves the legacy OpenClaw adapter lifecycle: create, pre-check,
        and delete call ``/api/sessions`` on the device adapter. Engines that create
        sessions lazily through their relay/runtime should return ``False`` so
        ExpertChat only keeps its local session key.
        """

    @abstractmethod
    def build_local_chat_session_key(
        self, ctx: BotProvisioningContext, *, user_id: str
    ) -> str:
        """Build the local session key for relay-managed chat sessions.

        Only strategies with ``uses_adapter_chat_session_lifecycle(ctx) == False``
        should be asked to construct this key. The concrete engine strategy owns
        the wire format instead of ExpertChat branching on engine literals.
        Versioned service-bot formats are documented in
        ``src/backend/specs/2026-08-10-expert-chat-service-bot-session-keys/spec.md``.
        """

    @abstractmethod
    def apply_restart_extra_configs(
        self,
        ctx: BotProvisioningContext,
        extra_configs: Dict[str, Any] | None,
        *,
        template_service: Any,
    ) -> None:
        """Apply engine-owned values from the restart extension envelope.

        Concrete engines own their keys, validation, and side effects.
        Unsupported engines should no-op so the core restart path remains
        engine-agnostic and backward compatible.
        """

    @abstractmethod
    def refresh_restart_authorization(
        self,
        ctx: BotProvisioningContext,
        bot: Dict[str, Any],
        extra_configs: Dict[str, Any] | None,
        *,
        mcp_sync: Any = None,
        skill_set_factory: Any = None,
        template_service: Any = None,
    ) -> bool:
        """Optionally refresh engine-owned restart authorization/runtime state.

        Engines own their opt-in keys and side effects. Implementations must be
        best-effort: failures should be logged/swallowed so restart success is
        not blocked. ``template_service`` is optional and lets strategies consume
        persisted, engine-owned restart intents after async lifecycle events.
        Return value only records whether the engine opted in.
        """

    @abstractmethod
    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        """Post-create hook. Default strategies should no-op."""

    @abstractmethod
    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        """Post-template-update hook. Default strategies should no-op."""
