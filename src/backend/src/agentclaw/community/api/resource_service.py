"""Service API Protocols for resource management and factory.

Re-export only. The Protocol is defined in its owning core module
(``core/resources/resource_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.resources.resource_service_protocol import (
    Resource,
    ResourceServiceFactoryProtocol,
    ResourceServiceProtocol,
    ResourceType,
)

__all__ = [
    "Resource",
    "ResourceServiceFactoryProtocol",
    "ResourceServiceProtocol",
    "ResourceType",
]
