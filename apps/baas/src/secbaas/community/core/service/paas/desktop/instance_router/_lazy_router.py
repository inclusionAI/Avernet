"""Thread-safe lazy initialization proxy for InstanceRouter.

Per WR-03 Fix: Provides a robust solution to the multiprocess initialization issue
where child processes (SpawnProcess) inherit NoopInstanceRouter from parent.
Instead of explicit initialization, this proxy lazily creates the real
LocalInstanceRouter on first method call using double-checked locking pattern.
"""

from threading import Lock
from typing import TYPE_CHECKING, Any

from secbaas.community.core.repository.local_user_machine import (
    LocalUserMachineRepository,
)
from secbaas.community.logger import get_logger

from ._config import InstanceRouterConfig

if TYPE_CHECKING:
    from ._local_instance_router import (
        LocalInstanceRouter,
    )

logger = get_logger("core-service")


class ThreadSafeLazyRouter:
    """Thread-safe lazy initialization proxy for InstanceRouter.

    This class acts as a transparent proxy to LocalInstanceRouter, deferring
    actual initialization until the first method call. Uses double-checked
    locking pattern for thread safety in concurrent environments.

    Benefits:
    - No need to worry about multiprocess inheritance issues
    - Initialization happens on demand (first call)
    - Thread-safe via double-checked locking
    - Transparent to callers (same interface as real router)
    - No modifications needed to LocalPaasService

    Example:
        >>> # Module level in factory.py
        >>> _instance_router = ThreadSafeLazyRouter()
        >>>
        >>> # Used in LocalPaasService
        >>> service = LocalPaasService(..., instance_router=_instance_router)
        >>> # First call triggers initialization
        >>> result = await service.get_machine_info(machine_id)
    """

    def __init__(
        self,
        config: InstanceRouterConfig,
        local_user_machine_repo: LocalUserMachineRepository,
    ) -> None:
        """Initialize the lazy router proxy (not the actual router).

        Args:
            config: InstanceRouterConfig containing internal_port and timeout
                settings. Injected by the DI container — never None at runtime.
            local_user_machine_repo: Repository for looking up machine assignments.
        """
        self._config = config
        self._local_user_machine_repo = local_user_machine_repo
        self._lock = Lock()
        self._router: LocalInstanceRouter | None = None
        self._init_error: Exception | None = None
        logger.info(
            "[ThreadSafeLazyRouter] Initialized with config: internal_port=%s",
            config.internal_port,
        )

    def _get_router(self) -> "LocalInstanceRouter":
        """Get or create the actual LocalInstanceRouter instance.

        Uses double-checked locking pattern for thread-safe lazy initialization:
        1. Fast path: check without lock (already initialized)
        2. Slow path: acquire lock and check again
        3. Initialize if still needed

        Per D-01 (Error Handling Strategy): If initialization fails, cache
        the exception and raise it on subsequent calls without re-trying.
        Service restart is required to recover from initialization failure.

        Returns:
            Initialized LocalInstanceRouter instance

        Raises:
            DeviceCreationError: If initialization fails
        """
        # Check if previous initialization failed (fail-fast per D-01)
        if self._init_error is not None:
            raise self._init_error

        # Fast path: already initialized
        if self._router is not None:
            return self._router

        # Slow path: need initialization - acquire lock
        with self._lock:
            # Double-check after acquiring lock
            if self._init_error is not None:
                raise self._init_error

            if self._router is not None:
                return self._router

            # Initialize the real router
            from secbaas.community.core.service.paas import DeviceCreationError

            from . import (
                initialize_instance_router,
            )

            try:
                # Per D-02: Log first-time initialization
                logger.info(
                    "[ThreadSafeLazyRouter] Initializing InstanceRouter for first use..."
                )

                self._router = initialize_instance_router(
                    self._local_user_machine_repo, config=self._config
                )

                logger.info(
                    "[ThreadSafeLazyRouter] InstanceRouter initialized successfully"
                )
                return self._router
            except Exception as exc:
                # Cache the error for fail-fast behavior (D-01)
                error = DeviceCreationError(
                    error_code="LAZY_ROUTER_INIT_FAILED",
                    message=f"ThreadSafeLazyRouter initialization failed: {exc}",
                    context={"underlying_error": str(exc)},
                )
                self._init_error = error

                logger.error(
                    f"[ThreadSafeLazyRouter] Initialization failed: {exc}. "
                    f"All subsequent calls will fail until service restart."
                )
                raise error from exc

    def get_instance_for(self, machine_id: str, env: str) -> str | None:
        """Get the instance for a machine (delegates to real router).

        Triggers lazy initialization on first call.
        """
        return self._get_router().get_instance_for(machine_id, env)

    def ensure_initialized(self) -> None:
        """Eagerly trigger initialization (fail-fast on startup).

        Called from app.py lifespan to surface initialization errors at
        startup rather than on first request. Idempotent — subsequent
        calls are no-ops once initialized.
        """
        self._get_router()

    # -- Lifecycle Protocol --------------------------------------------------

    async def start(self) -> None:
        """Lifecycle.start: eagerly initialize the inner router."""
        self.ensure_initialized()

    async def stop(self) -> None:
        """Lifecycle.stop: close the inner router's HTTP client if initialized."""
        if self._router is not None:
            await self._router.close()

    async def route_to_instance(
        self,
        target_instance: str,
        action: str,
        machine_id: str,
        params: dict,
        request_id: str,
    ) -> Any:
        """Route request to target instance (delegates to real router).

        Triggers lazy initialization on first call.
        """
        return await self._get_router().route_to_instance(
            target_instance=target_instance,
            action=action,
            machine_id=machine_id,
            params=params,
            request_id=request_id,
        )
