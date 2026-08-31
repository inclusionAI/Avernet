"""配置清单 Repository — one ORM implementation, both runtimes.

A package rather than a module because the key derivation is worth reading on
its own: ``_key.py`` carries the argument for why the surrogate key is
length-prefixed, which is a correctness property and not an implementation
detail of the queries in ``repository.py``.
"""
from __future__ import annotations

from agentclaw.community.core.repository.implementations.bot.config_manifest.repository import (
    BotConfigManifestRepository,
)

__all__ = ["BotConfigManifestRepository"]
