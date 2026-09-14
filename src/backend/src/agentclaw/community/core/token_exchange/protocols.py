"""Core-local aliases for the token plugin contracts."""
from agentclaw.community.plugin_api.token_plugin import (
    ExchangedToken,
    OutboundTokenPlugin,
    PreparedExchangeTokenRequest,
    TokenExchangeContext,
    TokenInjectionTarget,
    TokenOutboundAppender,
    TokenPluginResult,
    TokenRuntimeTargetResolver,
    TokenScopeMatcher,
)


__all__ = [
    "TokenExchangeContext",
    "TokenInjectionTarget",
    "TokenRuntimeTargetResolver",
    "TokenScopeMatcher",
    "OutboundTokenPlugin",
    "PreparedExchangeTokenRequest",
    "ExchangedToken",
    "TokenOutboundAppender",
    "TokenPluginResult",
]
