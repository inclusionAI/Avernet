"""Collaboration repair Service API v2, re-exported for adapters.

The Protocol is defined in its owning Core module
(``core/bot_management/collaboration_repair/service_protocol.py``);
this module re-publishes it so HTTP adapters depend on ``api/`` only.
"""

from __future__ import annotations

from agentclaw.community.core.bot_management.collaboration_repair.service_protocol import (
    CollaborationRepairServiceProtocol,
)

__all__ = ["CollaborationRepairServiceProtocol"]
