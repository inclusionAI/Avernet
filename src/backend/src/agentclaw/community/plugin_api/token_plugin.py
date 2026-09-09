"""Contracts for independent outbound token exchange plugins."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TokenExchangeContext:
    """Trusted context assembled by the application boundary."""

    bot_id: str
    bot_type: str
    owner_user_id: str
    user_list_entity_id: str
    entity_id: str | None
    stage: str
    env: str
    publish_id: int | None
    binding_id: int | None
    request_id: str | None


@dataclass(frozen=True, slots=True)
class TokenInjectionTarget:
    """Resolved runtime target used by an outbound token plugin."""

    arca_sandbox_id: str
    paas_device_id: str


@runtime_checkable
class TokenScopeMatcher(Protocol):
    """Check a configured user-list scope without exposing list entries."""

    def is_allowed(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        env: str,
    ) -> bool: ...


@runtime_checkable
class TokenRuntimeTargetResolver(Protocol):
    """Resolve one existing runtime target for token injection."""

    def resolve(self, context: TokenExchangeContext) -> TokenInjectionTarget: ...


class TokenResponseParser(Protocol):
    """Parse one upstream response into an opaque exchanged token."""

    def parse(self, *, status_code: int, body: object) -> ExchangedToken: ...


@dataclass(frozen=True, slots=True)
class PreparedExchangeTokenRequest:
    """All data needed by one plugin's exchange and append phases."""

    plugin_code: str
    method: str
    path: str
    headers: Mapping[str, str]
    body: Mapping[str, object]
    timeout_seconds: float
    response_parser: TokenResponseParser
    paas_device_id: str
    outbound_header_name: str
    outbound_action: str = "set"
    outbound_domains: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExchangedToken:
    """An opaque token kept in memory only."""

    value: str
    expires_at_ms: int | None
    fingerprint: str
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class TokenPluginResult:
    """A non-sensitive result for one plugin execution."""

    plugin_code: str
    status: str
    failed_stage: str | None = None
    error_code: str | None = None

    @classmethod
    def skipped(cls, plugin_code: str) -> TokenPluginResult:
        return cls(plugin_code=plugin_code, status="skipped")

    @classmethod
    def succeeded(cls, plugin_code: str) -> TokenPluginResult:
        return cls(plugin_code=plugin_code, status="succeeded")

    @classmethod
    def failed(
        cls,
        plugin_code: str,
        *,
        failed_stage: str,
        error_code: str,
    ) -> TokenPluginResult:
        return cls(
            plugin_code=plugin_code,
            status="failed",
            failed_stage=failed_stage,
            error_code=error_code,
        )


@runtime_checkable
class TokenOutboundAppender(Protocol):
    """Append one token header through the BaaS outbound-rule boundary."""

    def append_token(
        self,
        *,
        paas_device_id: str,
        header_name: str,
        action: str,
        token: str,
        plugin_code: str,
        domains: tuple[str, ...] = (),
    ) -> bool: ...


class OutboundTokenPlugin(Protocol):
    """One independently executable four-phase token plugin."""

    code: str

    def match(self, context: TokenExchangeContext) -> bool: ...

    def prepare_exchange_token_request(
        self,
        context: TokenExchangeContext,
    ) -> PreparedExchangeTokenRequest: ...

    async def exchange_token(
        self,
        *,
        context: TokenExchangeContext,
        prepared: PreparedExchangeTokenRequest,
    ) -> ExchangedToken: ...

    def append_to_outbound(
        self,
        *,
        context: TokenExchangeContext,
        prepared: PreparedExchangeTokenRequest,
        token: ExchangedToken,
        appender: TokenOutboundAppender,
    ) -> None: ...


__all__ = [
    "ExchangedToken",
    "PreparedExchangeTokenRequest",
    "TokenExchangeContext",
    "TokenInjectionTarget",
    "TokenRuntimeTargetResolver",
    "TokenScopeMatcher",
    "OutboundTokenPlugin",
    "TokenOutboundAppender",
    "TokenPluginResult",
    "TokenResponseParser",
]
