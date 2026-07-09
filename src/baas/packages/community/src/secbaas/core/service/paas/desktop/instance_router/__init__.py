"""InstanceRouter for cross-instance HTTP forwarding.

Provides InstanceRouter Protocol and implementations for distributed deployment scenarios.
The InstanceRouter routes requests to the correct secbaas instance when an mng daemon
connects to one instance but the request arrives at another.

Per Microkernel Architecture Rule 14: All wiring is configuration-driven.
Per Microkernel Architecture Rule 20: Single protocol for local/prod.
Per Microkernel Architecture Rule 21: Noop and Mock implementations for testing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._config import InstanceRouterConfig

if TYPE_CHECKING:
    from secbaas.core.repository.local_user_machine import (
        LocalUserMachineRepository,
    )
from ._lazy_router import ThreadSafeLazyRouter
from ._local_instance_router import LocalInstanceRouter
from ._mock_instance_router import MockInstanceRouter
from ._noop_instance_router import NoopInstanceRouter
from ._protocol import InstanceRouter


def initialize_instance_router(
    repository: LocalUserMachineRepository,
    config: InstanceRouterConfig | None = None,
) -> LocalInstanceRouter:
    """Create and return an initialized LocalInstanceRouter.

    Called during app startup lifespan. The container wires the router
    as a Singleton — callers that need the initialized router resolve
    it from the container rather than from a module-level global.

    Args:
        repository: LocalUserMachineRepository for instance discovery.
        config: Optional configuration for the router.

    Returns:
        Initialized LocalInstanceRouter instance.
    """
    return LocalInstanceRouter(repository=repository, config=config)


__all__ = [
    "InstanceRouter",
    "InstanceRouterConfig",
    "LocalInstanceRouter",
    "MockInstanceRouter",
    "NoopInstanceRouter",
    "ThreadSafeLazyRouter",
    "initialize_instance_router",
]
