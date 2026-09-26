"""Unit tests for K8sClientManager.

Covers:
- K8sClientManager.get_or_create_client: create, cache reuse, invalid/empty kubeconfig
- K8sClientManager._run_sync: bridges sync to asyncio.to_thread
- K8sClientManager retry: 409/429/503 retryable, 400/404 non-retryable, exhaust re-raise
- K8sClientManager.close: thread-safe close, cleanup all cached clients
"""

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from secbaas.community.api.device_manage import K8sCredentials
from secbaas.community.plugins.sandbox.k8s.real import K8sClientManager
from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
    _DEFAULT_RETRY_MAX_ATTEMPTS,
)

# ==================== TestGetOrCreateClient ====================


class TestGetOrCreateClient:
    """Tests for K8sClientManager.get_or_create_client."""

    SAMPLE_KUBECONFIG = """
apiVersion: v1
kind: Config
clusters:
- cluster:
    server: https://test-cluster:6443
  name: test-cluster
contexts:
- context:
    cluster: test-cluster
  name: default-context
current-context: default-context
"""

    def test_creates_client_on_first_call(self):
        """get_or_create_client creates an ApiClient via new_client_from_config_dict with persist_config=False."""
        creds = K8sCredentials(
            kubeconfig=self.SAMPLE_KUBECONFIG,
            template_id=1,
            template_uuid="tmpl-k8s-001",
        )
        manager = K8sClientManager()

        with patch("kubernetes.config.new_client_from_config_dict") as mock_new:
            mock_client = MagicMock(spec=[])
            mock_new.return_value = mock_client

            result = manager.get_or_create_client(creds)

            assert result is mock_client
            mock_new.assert_called_once()
            call_kwargs = mock_new.call_args.kwargs
            assert call_kwargs["persist_config"] is False

    def test_reuses_client_for_same_kubeconfig(self):
        """get_or_create_client returns cached client for same kubeconfig content."""
        creds = K8sCredentials(
            kubeconfig=self.SAMPLE_KUBECONFIG,
            template_id=1,
            template_uuid="tmpl-k8s-001",
        )
        manager = K8sClientManager()

        with patch("kubernetes.config.new_client_from_config_dict") as mock_new:
            mock_new.return_value = MagicMock()
            client1 = manager.get_or_create_client(creds)
            client2 = manager.get_or_create_client(creds)

            assert client1 is client2
            mock_new.assert_called_once()

    def test_creates_separate_client_for_different_kubeconfig(self):
        """get_or_create_client creates separate clients for different kubeconfig strings."""
        kubeconfig_a = self.SAMPLE_KUBECONFIG
        kubeconfig_b = """
apiVersion: v1
kind: Config
clusters:
- cluster:
    server: https://other-cluster:6443
  name: other-cluster
contexts:
- context:
    cluster: other-cluster
  name: other-context
current-context: other-context
"""
        creds_a = K8sCredentials(
            kubeconfig=kubeconfig_a, template_id=1, template_uuid="tmpl-a"
        )
        creds_b = K8sCredentials(
            kubeconfig=kubeconfig_b, template_id=1, template_uuid="tmpl-b"
        )
        manager = K8sClientManager()

        with patch("kubernetes.config.new_client_from_config_dict") as mock_new:
            mock_client_a = MagicMock()
            mock_client_b = MagicMock()
            mock_new.side_effect = [mock_client_a, mock_client_b]

            result_a = manager.get_or_create_client(creds_a)
            result_b = manager.get_or_create_client(creds_b)

            assert result_a is mock_client_a
            assert result_b is mock_client_b
            assert result_a is not result_b
            assert mock_new.call_count == 2

    def test_raises_on_empty_kubeconfig(self):
        """get_or_create_client raises ValueError for empty kubeconfig string."""
        creds = K8sCredentials(
            kubeconfig="", template_id=1, template_uuid="tmpl-k8s-001"
        )
        manager = K8sClientManager()

        with pytest.raises(ValueError, match="empty"):
            manager.get_or_create_client(creds)

    def test_raises_on_none_kubeconfig(self):
        """get_or_create_client raises ValueError when kubeconfig is None."""
        creds = K8sCredentials(template_id=1, template_uuid="tmpl-k8s-001")
        manager = K8sClientManager()

        with pytest.raises(ValueError):
            manager.get_or_create_client(creds)

    def test_thread_safety_double_check(self):
        """Double-checked locking protects critical section with threading.Lock.

        Verifies that:
        - _lock is a threading.Lock (required for thread-safe double-check)
        - new_client_from_config_dict is called exactly once (deduplication works)
        - The lock is acquired for the critical section
        """
        creds = K8sCredentials(
            kubeconfig=self.SAMPLE_KUBECONFIG,
            template_id=1,
            template_uuid="tmpl-k8s-001",
        )
        manager = K8sClientManager()

        assert hasattr(manager._lock, "acquire")

        with patch("kubernetes.config.new_client_from_config_dict") as mock_new:
            mock_client = MagicMock()
            mock_new.return_value = mock_client

            result = manager.get_or_create_client(creds)

            assert result is mock_client
            mock_new.assert_called_once()

    def test_reuse_after_cache_populated(self):
        """After a client is cached, subsequent requests return the cached client via fast path."""
        creds = K8sCredentials(
            kubeconfig=self.SAMPLE_KUBECONFIG,
            template_id=1,
            template_uuid="tmpl-k8s-001",
        )
        manager = K8sClientManager()

        with patch("kubernetes.config.new_client_from_config_dict") as mock_new:
            mock_client = MagicMock()
            mock_new.return_value = mock_client

            # First call — creates client
            result1 = manager.get_or_create_client(creds)
            assert result1 is mock_client
            assert mock_new.call_count == 1

            # Second call — cache hit, fast path (no lock acquisition needed)
            result2 = manager.get_or_create_client(creds)
            assert result2 is mock_client
            assert mock_new.call_count == 1


# ==================== TestRunSync ====================


class TestRunSync:
    """Tests for K8sClientManager._run_sync."""

    @pytest.mark.asyncio
    async def test_run_sync_uses_asyncio_to_thread(self):
        """_run_sync delegates to asyncio.to_thread with the sync function."""
        manager = K8sClientManager()

        def marker_func():
            return "marker-value"

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = "marker-value"

            result = await manager._run_sync(marker_func)

            assert result == "marker-value"
            mock_to_thread.assert_called_once_with(marker_func)

    @pytest.mark.asyncio
    async def test_run_sync_passes_args_and_kwargs(self):
        """_run_sync forwards positional and keyword args to asyncio.to_thread."""
        manager = K8sClientManager()

        def capture_func(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

        with patch("asyncio.to_thread") as mock_to_thread:
            expected = {"args": (1, 2), "kwargs": {"key": "val"}}
            mock_to_thread.return_value = expected

            result = await manager._run_sync(capture_func, 1, 2, key="val")

            assert result == expected
            mock_to_thread.assert_called_once_with(capture_func, 1, 2, key="val")


# ==================== TestRetryK8sCall ====================


class TestRetryK8sCall:
    """Tests for K8sClientManager._retry_k8s_call."""

    @staticmethod
    def _fast_retry_decorator(func):
        """Fast retry decorator that retries instantly (no sleep).

        Provides the same retry logic (stop_after_attempt, retry_if_exception)
        but uses wait=wait_none() for zero-delay retries during testing.
        """
        from tenacity import retry, retry_if_exception, stop_after_attempt, wait_none

        from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
            _is_k8s_retryable,
        )

        return retry(
            stop=stop_after_attempt(_DEFAULT_RETRY_MAX_ATTEMPTS),
            wait=wait_none(),
            retry=retry_if_exception(_is_k8s_retryable),
            reraise=True,
        )(func)

    @staticmethod
    def _fast_retry_decorator_5_attempts(func):
        """Fast retry decorator with 5 max attempts."""
        from tenacity import retry, retry_if_exception, stop_after_attempt, wait_none

        from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
            _is_k8s_retryable,
        )

        return retry(
            stop=stop_after_attempt(5),
            wait=wait_none(),
            retry=retry_if_exception(_is_k8s_retryable),
            reraise=True,
        )(func)

    def test_retries_409_then_succeeds(self):
        """_retry_k8s_call retries on ApiException(status=409) and returns success."""
        manager = K8sClientManager()

        call_count = [0]

        def flaky_func():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ApiException(status=409, reason="Conflict")
            return "success"

        with patch.object(
            manager, "_make_retry_decorator", return_value=self._fast_retry_decorator
        ):
            result = manager._retry_k8s_call(flaky_func)
        assert result == "success"
        assert call_count[0] == 3  # 2 fails + 1 success

    def test_retries_429_then_succeeds(self):
        """_retry_k8s_call retries on ApiException(status=429) and returns success."""
        manager = K8sClientManager()

        call_count = [0]

        def flaky_func():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ApiException(status=429, reason="Too Many Requests")
            return "success"

        with patch.object(
            manager, "_make_retry_decorator", return_value=self._fast_retry_decorator
        ):
            result = manager._retry_k8s_call(flaky_func)
        assert result == "success"
        assert call_count[0] == 3  # 2 fails + 1 success

    def test_retries_503_then_succeeds(self):
        """_retry_k8s_call retries on ApiException(status=503) and returns success."""
        manager = K8sClientManager()

        call_count = [0]

        def flaky_func():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ApiException(status=503, reason="Service Unavailable")
            return "success"

        with patch.object(
            manager, "_make_retry_decorator", return_value=self._fast_retry_decorator
        ):
            result = manager._retry_k8s_call(flaky_func)
        assert result == "success"
        assert call_count[0] == 3  # 2 fails + 1 success

    def test_does_not_retry_400(self):
        """_retry_k8s_call does NOT retry on ApiException(status=400)."""
        manager = K8sClientManager()

        call_count = [0]

        def bad_func():
            call_count[0] += 1
            raise ApiException(status=400, reason="Bad Request")

        with patch.object(
            manager, "_make_retry_decorator", return_value=self._fast_retry_decorator
        ):
            with pytest.raises(ApiException) as exc_info:
                manager._retry_k8s_call(bad_func)
        assert exc_info.value.status == 400
        assert call_count[0] == 1

    def test_does_not_retry_404(self):
        """_retry_k8s_call does NOT retry on ApiException(status=404)."""
        manager = K8sClientManager()

        call_count = [0]

        def not_found_func():
            call_count[0] += 1
            raise ApiException(status=404, reason="Not Found")

        with patch.object(
            manager, "_make_retry_decorator", return_value=self._fast_retry_decorator
        ):
            with pytest.raises(ApiException) as exc_info:
                manager._retry_k8s_call(not_found_func)
        assert exc_info.value.status == 404
        assert call_count[0] == 1

    def test_reraises_after_max_attempts_409(self):
        """_retry_k8s_call re-raises after exhausting max retry attempts on 409."""
        manager = K8sClientManager()

        call_count = [0]

        def always_conflict():
            call_count[0] += 1
            raise ApiException(status=409, reason="Conflict")

        with patch.object(
            manager, "_make_retry_decorator", return_value=self._fast_retry_decorator
        ):
            with pytest.raises(ApiException) as exc_info:
                manager._retry_k8s_call(always_conflict)
        assert exc_info.value.status == 409
        assert call_count[0] == 3  # default max attempts = 3

    def test_retry_not_applied_to_non_api_exception(self):
        """_retry_k8s_call does NOT retry non-ApiException (e.g., RuntimeError)."""
        manager = K8sClientManager()

        call_count = [0]

        def runtime_err():
            call_count[0] += 1
            raise RuntimeError("unexpected")

        with patch.object(
            manager, "_make_retry_decorator", return_value=self._fast_retry_decorator
        ):
            with pytest.raises(RuntimeError, match="unexpected"):
                manager._retry_k8s_call(runtime_err)
        assert call_count[0] == 1

    def test_extra_k8s_opts_override_retry_params(self):
        """_retry_k8s_call uses extra_k8s_opts to override retry_max_attempts."""
        creds = K8sCredentials(
            kubeconfig=TestGetOrCreateClient.SAMPLE_KUBECONFIG,
            template_id=1,
            template_uuid="tmpl-k8s-001",
            extra_k8s_opts={"retry_max_attempts": 5},
        )
        manager = K8sClientManager()

        call_count = [0]

        def always_conflict():
            call_count[0] += 1
            raise ApiException(status=409, reason="Conflict")

        with patch.object(
            manager,
            "_make_retry_decorator",
            return_value=self._fast_retry_decorator_5_attempts,
        ):
            with pytest.raises(ApiException) as exc_info:
                manager._retry_k8s_call(always_conflict, credentials=creds)
        assert exc_info.value.status == 409
        assert call_count[0] == 5  # overridden to 5 attempts


# ==================== TestClose ====================


class TestClose:
    """Tests for K8sClientManager.close."""

    def test_close_cleans_all_clients(self):
        """close() calls close() on all cached clients and clears the cache."""
        manager = K8sClientManager()

        mock_client_1 = MagicMock()
        mock_client_2 = MagicMock()
        manager._clients["kubeconfig-a"] = mock_client_1
        manager._clients["kubeconfig-b"] = mock_client_2

        manager.close()

        mock_client_1.close.assert_called_once()
        mock_client_2.close.assert_called_once()
        assert manager._clients == {}

    def test_close_handles_client_close_error(self):
        """close() catches errors from one client and continues closing the rest."""
        manager = K8sClientManager()

        mock_client_1 = MagicMock()
        mock_client_1.close.side_effect = RuntimeError("close failed")
        mock_client_2 = MagicMock()
        manager._clients["kubeconfig-a"] = mock_client_1
        manager._clients["kubeconfig-b"] = mock_client_2

        manager.close()

        mock_client_1.close.assert_called_once()
        mock_client_2.close.assert_called_once()
        assert manager._clients == {}

    def test_close_idempotent(self):
        """close() can be called multiple times safely — second call is a no-op."""
        manager = K8sClientManager()

        mock_client = MagicMock()
        manager._clients["kubeconfig"] = mock_client

        manager.close()
        manager.close()

        # close() called exactly once on each client
        mock_client.close.assert_called_once()
        assert manager._clients == {}


# ==================== TestIsRetryable ====================


class TestIsRetryable:
    """Tests for _is_k8s_retryable helper function."""

    def test_api_exception_409_is_retryable(self):
        """_is_k8s_retryable returns True for ApiException(status=409)."""
        from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
            _is_k8s_retryable,
        )

        exc = ApiException(status=409, reason="Conflict")
        assert _is_k8s_retryable(exc) is True

    def test_api_exception_429_is_retryable(self):
        """_is_k8s_retryable returns True for ApiException(status=429)."""
        from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
            _is_k8s_retryable,
        )

        exc = ApiException(status=429, reason="Too Many Requests")
        assert _is_k8s_retryable(exc) is True

    def test_api_exception_503_is_retryable(self):
        """_is_k8s_retryable returns True for ApiException(status=503)."""
        from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
            _is_k8s_retryable,
        )

        exc = ApiException(status=503, reason="Service Unavailable")
        assert _is_k8s_retryable(exc) is True

    def test_api_exception_404_is_not_retryable(self):
        """_is_k8s_retryable returns False for ApiException(status=404)."""
        from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
            _is_k8s_retryable,
        )

        exc = ApiException(status=404, reason="Not Found")
        assert _is_k8s_retryable(exc) is False

    def test_api_exception_400_is_not_retryable(self):
        """_is_k8s_retryable returns False for ApiException(status=400)."""
        from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
            _is_k8s_retryable,
        )

        exc = ApiException(status=400, reason="Bad Request")
        assert _is_k8s_retryable(exc) is False

    def test_non_api_exception_is_not_retryable(self):
        """_is_k8s_retryable returns False for non-ApiException (e.g., RuntimeError)."""
        from secbaas.community.plugins.sandbox.k8s.real._client_manager import (
            _is_k8s_retryable,
        )

        exc = RuntimeError("unexpected error")
        assert _is_k8s_retryable(exc) is False


# ==================== Helpers ====================


def _make_k8s_credentials(**overrides) -> K8sCredentials:
    """Create a K8sCredentials with defaults for testing."""
    defaults = {
        "kubeconfig": "apiVersion: v1\nkind: Config\n",
        "template_id": 1,
        "template_uuid": "tmpl-k8s-test",
    }
    defaults.update(overrides)
    return K8sCredentials(**defaults)
