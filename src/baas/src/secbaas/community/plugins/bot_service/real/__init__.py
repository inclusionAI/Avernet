"""Aiohttp BotService plugin — HTTP implementation."""

from ._plugin import AiohttpBotServicePlugin
from ._principal_signer import CallerPrincipalConfig, CallerPrincipalSigner

__all__ = [
    "AiohttpBotServicePlugin",
    "CallerPrincipalConfig",
    "CallerPrincipalSigner",
]
