"""K8s ApiClient lifecycle manager.

Provides thread-safe lazy-init, per-kubeconfig reuse, async bridge,
and retry for Kubernetes API calls.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any  # noqa: TC003

from secbaas.api.device_manage import K8sCredentials
from secbaas.logger import get_logger

if TYPE_CHECKING:
    from kubernetes.client import ApiClient

logger = get_logger("paas-k8s-client")

RETRYABLE_K8S_STATUSES: frozenset[int] = frozenset({409, 429, 503})
_DEFAULT_RETRY_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_BASE_BACKOFF = 0.5
_DEFAULT_RETRY_MAX_BACKOFF = 10.0


def _is_k8s_retryable(exception: BaseException) -> bool:
    """Return True if the exception warrants a retry (409/429/503).

    Only ApiException instances with status codes in RETRYABLE_K8S_STATUSES
    are considered retryable. All other exceptions are re-raised immediately.

    Args:
        exception: The exception raised during a K8s API call.

    Returns:
        True if the exception is an ApiException with status 409, 429, or 503.
    """
    from kubernetes.client.rest import ApiException

    if isinstance(exception, ApiException):
        return exception.status in RETRYABLE_K8S_STATUSES
    return False


class K8sClientManager:
    """Thread-safe Kubernetes ApiClient lifecycle manager.

    Lazily initializes one ApiClient per unique kubeconfig content,
    reuses it across calls, provides an async bridge via asyncio.to_thread(),
    and supports tenacity-based retry for transient errors (409/429/503).

    Usage example::

        manager = K8sClientManager()
        try:
            client = manager.get_or_create_client(credentials)
            # ... use client for K8s API calls ...
        finally:
            manager.close()
    """

    def __init__(self) -> None:
        """Initialize the K8sClientManager with empty client cache and lock.

        The manager is stateless until get_or_create_client() is called.
        No kubeconfig or credentials are needed at construction time.
        """
        self._lock = threading.Lock()
        self._clients: dict[str, ApiClient] = {}

    def get_or_create_default_client(self) -> ApiClient:
        """Return any existing K8s ApiClient from the cache.

        Returns the first cached client. Used by components that only need
        an already-authenticated client (e.g., health checks).

        Returns:
            An ApiClient from the cache.

        Raises:
            RuntimeError: If no clients have been created yet (a K8s device
                must be created before health checks can run).
        """
        with self._lock:
            if not self._clients:
                raise RuntimeError(
                    "No K8s client available -- a K8s device must be created "
                    "before health checks can run"
                )
            return next(iter(self._clients.values()))

    def get_or_create_client(self, credentials: K8sCredentials) -> ApiClient:
        """Get or lazily create an ApiClient for the given kubeconfig.

        Thread-safe: uses double-checked locking to avoid creating duplicate
        ApiClient instances when multiple threads request the same kubeconfig
        concurrently.

        The kubeconfig content string is used as the cache key, so different
        string values even for the same logical cluster will create separate
        clients.

        Args:
            credentials: K8sCredentials containing inline kubeconfig YAML.

        Returns:
            A kubernetes.client.ApiClient configured for the given kubeconfig.

        Raises:
            ValueError: If credentials.kubeconfig is empty or None.
            yaml.YAMLError: If the kubeconfig string is not valid YAML.
            kubernetes.config.ConfigException: If the kubeconfig cannot be
                parsed into a valid Kubernetes configuration.
        """
        kubeconfig_str = credentials.kubeconfig
        if not kubeconfig_str:
            raise ValueError("K8sCredentials.kubeconfig is empty")

        # Fast path: check cache without lock
        client = self._clients.get(kubeconfig_str)
        if client is not None:
            return client

        # Prepare k8s config outside the lock to minimize lock-held time
        import yaml
        from kubernetes import config as k8s_config

        kubeconfig_dict = yaml.safe_load(kubeconfig_str)

        with self._lock:
            # Double-check under lock
            client = self._clients.get(kubeconfig_str)
            if client is not None:
                return client

            client = k8s_config.new_client_from_config_dict(
                config_dict=kubeconfig_dict,
                context=credentials.context,
                persist_config=False,
            )
            self._clients[kubeconfig_str] = client
            logger.info(
                "Created new K8s ApiClient (context=%s, namespace=%s)",
                credentials.context or "current-context",
                credentials.namespace,
            )
            return client

    async def _run_sync(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Run a sync K8s SDK function in a thread pool without blocking the event loop.

        Uses asyncio.to_thread() to offload synchronous K8s SDK calls to a
        thread pool, preventing them from blocking the async event loop.

        Args:
            func: The synchronous function to call.
            *args: Positional arguments to pass to func.
            **kwargs: Keyword arguments to pass to func.

        Returns:
            The return value of func(*args, **kwargs).
        """
        return await asyncio.to_thread(func, *args, **kwargs)

    def _make_retry_decorator(
        self, extra_opts: dict[str, Any] | None = None
    ) -> Callable:
        """Build a tenacity retry decorator with configured parameters.

        Reads optional overrides from K8sCredentials.extra_k8s_opts (D-07).
        Defaults: max 3 attempts, base backoff 0.5s, max backoff 10s.
        The backoff formula is: min(base_backoff * 2^(attempt-1), max_backoff).
        With defaults: attempt 1 waits 0.5s, attempt 2 waits 1.0s, attempt 3 waits 2.0s.

        Args:
            extra_opts: Optional dict with retry parameter overrides.
                Supported keys: retry_max_attempts, retry_base_backoff,
                retry_max_backoff.

        Returns:
            A tenacity retry decorator configured with the given parameters.
        """
        from tenacity import (
            before_sleep_log,
            retry,
            retry_if_exception,
            stop_after_attempt,
            wait_exponential,
        )

        opts = extra_opts or {}
        max_attempts = int(opts.get("retry_max_attempts", _DEFAULT_RETRY_MAX_ATTEMPTS))
        base_backoff = float(
            opts.get("retry_base_backoff", _DEFAULT_RETRY_BASE_BACKOFF)
        )
        max_backoff = float(opts.get("retry_max_backoff", _DEFAULT_RETRY_MAX_BACKOFF))

        return retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(
                multiplier=base_backoff,
                min=base_backoff,
                max=max_backoff,
            ),
            retry=retry_if_exception(_is_k8s_retryable),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    def _retry_k8s_call(
        self,
        func: Callable,
        *args: Any,
        credentials: K8sCredentials | None = None,
        **kwargs: Any,
    ) -> Any:
        """Apply retry to a synchronous K8s API call.

        Builds a tenacity retry decorator (optionally customized via
        credentials.extra_k8s_opts) and applies it to the given function.
        Non-retryable errors (e.g., 400, 404) re-raise immediately without
        retry.

        Args:
            func: The synchronous K8s API function to call with retry.
            *args: Positional arguments to pass to func.
            credentials: Optional K8sCredentials whose extra_k8s_opts are
                used to customize retry parameters.
            **kwargs: Keyword arguments to pass to func.

        Returns:
            The return value of the decorated func(*args, **kwargs).

        Raises:
            ApiException: If the call fails with a non-retryable status code.
            tenacity.RetryError: If retries are exhausted on retryable errors.
        """
        extra_opts = credentials.extra_k8s_opts if credentials else None
        retry_decorator = self._make_retry_decorator(extra_opts)
        return retry_decorator(func)(*args, **kwargs)

    def close(self) -> None:
        """Thread-safe close of all cached ApiClient connection pools.

        Acquires the internal lock, iterates over all cached ApiClient
        instances, and calls close() on each one. Any exceptions during
        individual client close are caught and logged, allowing remaining
        clients to be closed.

        Safe to call multiple times — subsequent calls are no-ops since
        the clients dict is cleared after the first close.
        """
        with self._lock:
            for key, client in list(self._clients.items()):
                try:
                    client.close()
                except Exception:
                    logger.warning(
                        "Error closing ApiClient for kubeconfig key=%s...",
                        key[:32],
                        exc_info=True,
                    )
            self._clients.clear()
            logger.info("K8sClientManager closed all ApiClient(s)")
