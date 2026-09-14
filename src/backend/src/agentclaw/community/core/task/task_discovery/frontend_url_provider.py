"""FrontendUrlProvider — re-export 兼容层.

协议与空实现已迁移至 ``plugin_api.frontend_url``:

- ``agentclaw.community.plugin_api.frontend_url.FrontendUrlProvider`` (Plugin Protocol)
- ``agentclaw.community.plugin_api.frontend_url.NullFrontendUrlProvider`` (空实现)

本文件仅为向后兼容保留 re-export, 供尚未迁移的 core consumer
(``discovery_service`` / ``session_creator`` / ``openapi_bot_session_initiator``)
继续按旧路径 import。新代码请直接 import ``plugin_api.frontend_url``。
"""
from __future__ import annotations

from agentclaw.community.plugin_api.frontend_url import (  # noqa: F401
    FrontendUrlProvider,
    NullFrontendUrlProvider,
)

__all__ = ["FrontendUrlProvider", "NullFrontendUrlProvider"]
