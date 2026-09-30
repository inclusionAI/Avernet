"""LocalInstanceRouter implementation.

Real implementation using httpx for cross-instance HTTP forwarding.
Per Microkernel Architecture Rule 20: Same implementation works for local/prod.
"""

from typing import TYPE_CHECKING

import httpx
from httpx import AsyncClient, Limits, Timeout

from secbaas.community.logger import get_logger

from ._config import InstanceRouterConfig
from ._exceptions import (
    ForwardHTTPError,
    ForwardTimeoutError,
)
from ._utils import log_forward_operation

if TYPE_CHECKING:
    from secbaas.community.core.repository.local_user_machine import (
        LocalUserMachineRepository,
    )

logger = get_logger("core-service")


class LocalInstanceRouter:
    """Cross-instance HTTP request router using httpx.

    Discovers target instances via repository lookup and forwards requests
    via HTTP POST to the target instance's internal endpoint.

    Implements connection pooling via httpx.AsyncClient for efficiency.
    All timeouts and limits are configurable via InstanceRouterConfig.

    Per D-IR01: Instance discovery via database lookup on
    baas_local_user_machine.connected_server_instance.
    Per D-IR02: HTTP client is httpx.AsyncClient with shared connection pool.
    Per D-IR03: Timeout matches LocalPaasService command timeout (30 seconds).
    Per D-IR04: Error handling is fast-fail with original exception.
    Per D-LOG01: INFO logging for forward start/complete with timing.
    Per D-TRACE01: X-Request-ID header propagated in forwarded requests.
    """

    def __init__(
        self,
        repository: "LocalUserMachineRepository",
        config: InstanceRouterConfig | None = None,
        http_client: AsyncClient | None = None,
    ) -> None:
        """Initialize LocalInstanceRouter.

        Args:
            repository: Repository for instance discovery.
            config: Configuration for HTTP client. Uses defaults if None.
            http_client: Optional pre-configured httpx client. If provided,
                        this client is used instead of creating one.
        """
        self._repository = repository
        self._config = config or InstanceRouterConfig()
        self._client = http_client or self._create_client()

    def _create_client(self) -> AsyncClient:
        """Create httpx AsyncClient with configured limits and timeouts.

        Per D-CONCUR01: No explicit concurrency limit - rely on httpx pool.

        Returns:
            Configured AsyncClient instance.
        """
        limits = Limits(
            max_connections=self._config.max_connections,
            max_keepalive_connections=self._config.max_keepalive,
        )
        timeout = Timeout(
            connect=self._config.connect_timeout,
            read=self._config.read_timeout,
            write=self._config.read_timeout,
            pool=self._config.pool_timeout,
        )
        return AsyncClient(limits=limits, timeout=timeout, trust_env=False)

    def get_instance_for(self, machine_id: str, env: str) -> str | None:
        """Get the connected secbaas instance for a machine.

        Per D-IR01: Instance discovery via database lookup.
        Per D-STALE02: Stale instance info is logged only, no auto-cleanup.

        Args:
            machine_id: The machine identifier to look up.
            env: Environment (dev, pre, prod).

        Returns:
            The instance identifier (e.g., "secbaas-instance-2") or None if
            the machine is not connected to any instance.
        """
        record = self._repository.get_by_machine_id(machine_id, env)
        if record is None:
            logger.debug(f"No database record found for machine {machine_id}")
            return None

        instance = record.connected_server_instance
        if not instance:
            logger.debug(
                f"Machine {machine_id} has no connected_server_instance in database"
            )
            return None

        return instance

    @log_forward_operation
    async def route_to_instance(
        self,
        target_instance: str,
        action: str,
        machine_id: str,
        params: dict,
        request_id: str,
    ) -> dict:
        """Route a request to the target instance via HTTP POST.

        Per D-PATH01: Target endpoint is POST /internal/v1/forward.
        Per PHS01: Request/response uses JSON-RPC style.
        Per D-HEALTH01: No proactive health checks - fail on use.

        Args:
            target_instance: Target instance (hostname/IP or instance ID).
            action: The action to execute (e.g., "execute_command").
            machine_id: Target machine ID for internal routing.
            params: Parameters for the action (API-specific, forwarded to mng).
            request_id: Unique request ID for tracing/correlation.

        Returns:
            Response dict from the target instance.

        Raises:
            ForwardTimeoutError: If the request times out.
            ForwardHTTPError: If the target returns non-2xx status.
        """
        # Build target URL (target_instance may be hostname or instance ID)
        # In local mode, this is localhost; in prod, internal service discovery
        url = (
            f"http://{target_instance}:{self._config.internal_port}/internal/v1/forward"
        )

        # Build request body (JSON-RPC style)
        body = {
            "action": action,
            "machine_id": machine_id,
            "params": params,
            "request_id": request_id,
        }

        # Propagate X-Request-ID header for distributed tracing
        headers = {"X-Request-ID": request_id}

        try:
            response = await self._client.post(url, json=body, headers=headers)
            response.raise_for_status()
            result = response.json()
            # DEBUG: Log raw forwarded response to diagnose nested data issue
            logger.info(
                f"[FORWARD_RAW_RESPONSE] Received from target instance: "
                f"target={target_instance}, action={action}, request_id={request_id}, "
                f"raw_result={result}"
            )
            return result

        except httpx.TimeoutException as e:
            logger.error(
                f"Forward timeout to {target_instance}: "
                f"action={action}, request_id={request_id}, error={e}"
            )
            raise ForwardTimeoutError(
                target_instance=target_instance,
                action=action,
                timeout=self._config.read_timeout,
            ) from e

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Forward HTTP error to {target_instance}: "
                f"action={action}, request_id={request_id}, "
                f"status={e.response.status_code}"
            )
            raise ForwardHTTPError(
                target_instance=target_instance,
                status_code=e.response.status_code,
                response_body=e.response.text,
            ) from e

        except httpx.HTTPError as e:
            # Other HTTP errors (connection refused, DNS, etc.)
            logger.error(
                f"Forward connection error to {target_instance}: "
                f"action={action}, request_id={request_id}, error={e}"
            )
            raise ForwardHTTPError(
                target_instance=target_instance,
                status_code=0,
                response_body=str(e),
            ) from e

    async def close(self) -> None:
        """Close the HTTP client and release resources.

        Should be called during application shutdown to properly close
        connection pools.
        """
        await self._client.aclose()
        logger.info("InstanceRouter HTTP client closed")
