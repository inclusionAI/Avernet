"""InstanceRouter utilities.

Provides logging decorators and helper functions for InstanceRouter operations.
"""

import functools
import time
from collections.abc import Callable
from typing import Any

from secbaas.logger import get_logger

from .._utils import get_instance_id

logger = get_logger("core-service")


def log_forward_operation(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to log forward operations with timing and tracing.

    Logs at INFO level for operation start and completion.
    Logs at ERROR level for operation failures.
    Includes source instance, target instance, machine_id, action, request_id,
    and duration.

    Args:
        func: The async function to wrap (route_to_instance).

    Returns:
        Wrapped function with logging.
    """

    @functools.wraps(func)
    async def wrapper(
        self,
        target_instance: str,
        action: str,
        machine_id: str,
        params: dict,
        request_id: str,
    ) -> Any:
        # machine_id is now a direct parameter, not extracted from params
        if not machine_id:
            machine_id = "unknown"
        source_instance = get_instance_id()

        logger.info(
            f"[IR-FORWARD-START] source={source_instance} target={target_instance} "
            f"machine={machine_id} action={action} request_id={request_id}"
        )

        start_time = time.monotonic()

        try:
            result = await func(
                self, target_instance, action, machine_id, params, request_id
            )

            duration = time.monotonic() - start_time
            logger.info(
                f"[IR-FORWARD-COMPLETE] source={source_instance} target={target_instance} "
                f"machine={machine_id} action={action} request_id={request_id} "
                f"duration_ms={duration * 1000:.2f}"
            )

            return result

        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error(
                f"[IR-FORWARD-FAILED] source={source_instance} target={target_instance} "
                f"machine={machine_id} action={action} request_id={request_id} "
                f"duration_ms={duration * 1000:.2f} error={type(e).__name__}: {e}"
            )
            raise

    return wrapper
