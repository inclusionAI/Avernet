"""Tests for ThreadSafeLazyRouter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.core.service.paas import DeviceCreationError
from secbaas.core.service.paas.desktop.instance_router._config import (
    InstanceRouterConfig,
)
from secbaas.core.service.paas.desktop.instance_router._lazy_router import (
    ThreadSafeLazyRouter,
)

_PORT = 8889


def make_lazy_router():
    """Create a ThreadSafeLazyRouter with mock dependencies."""
    return ThreadSafeLazyRouter(
        InstanceRouterConfig(internal_port=_PORT),
        local_user_machine_repo=MagicMock(),
    )


class TestThreadSafeLazyRouterInit:
    def test_init_creates_uninitialized_router(self) -> None:
        lazy = make_lazy_router()
        assert lazy._router is None
        assert lazy._init_error is None
        assert lazy._lock is not None

    def test_init_twice_creates_independent_instances(self) -> None:
        lazy1 = make_lazy_router()
        lazy2 = make_lazy_router()
        lazy1._router = "fake"
        assert lazy2._router is None


class TestThreadSafeLazyRouterGetInstanceFor:
    def test_get_instance_for_delegates_to_router(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()
        mock_router.get_instance_for.return_value = "instance-123"
        lazy._router = mock_router

        result = lazy.get_instance_for("machine-1", "prod")

        assert result == "instance-123"
        mock_router.get_instance_for.assert_called_once_with("machine-1", "prod")

    def test_get_instance_for_returns_none(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()
        mock_router.get_instance_for.return_value = None
        lazy._router = mock_router

        result = lazy.get_instance_for("machine-missing", "dev")

        assert result is None


class TestThreadSafeLazyRouterRouteToInstance:
    @pytest.mark.asyncio
    async def test_route_to_instance_delegates_to_router(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()
        mock_router.route_to_instance = AsyncMock(return_value={"status": "ok"})
        lazy._router = mock_router

        result = await lazy.route_to_instance(
            target_instance="instance-a",
            action="execute_command",
            machine_id="machine-1",
            params={"cmd": "ls"},
            request_id="req-001",
        )

        assert result == {"status": "ok"}
        mock_router.route_to_instance.assert_awaited_once_with(
            target_instance="instance-a",
            action="execute_command",
            machine_id="machine-1",
            params={"cmd": "ls"},
            request_id="req-001",
        )

    @pytest.mark.asyncio
    async def test_route_to_instance_forwards_exception(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()
        mock_router.route_to_instance = AsyncMock(
            side_effect=RuntimeError("router error")
        )
        lazy._router = mock_router

        with pytest.raises(RuntimeError, match="router error"):
            await lazy.route_to_instance(
                target_instance="instance-a",
                action="execute_command",
                machine_id="machine-1",
                params={},
                request_id="req-002",
            )


class TestThreadSafeLazyRouterGetRouterFastPath:
    def test_get_router_returns_cached_when_initialized(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()
        lazy._router = mock_router

        result = lazy._get_router()

        assert result is mock_router

    def test_get_router_skips_lock_when_already_initialized(self) -> None:
        lazy = make_lazy_router()
        lazy._router = MagicMock()

        track = {"acquire_called": False}

        class TrackLock:
            def acquire(self, *args: object, **kwargs: object) -> bool:
                track["acquire_called"] = True
                return True

            def release(self) -> None:
                pass

            def __enter__(self) -> bool:
                return True

            def __exit__(self, *args: object) -> None:
                pass

        lazy._lock = TrackLock()  # type: ignore[assignment]
        lazy._get_router()
        assert not track["acquire_called"]


class TestThreadSafeLazyRouterGetRouterFailFast:
    def test_get_router_raises_cached_error_fast_path(self) -> None:
        lazy = make_lazy_router()
        error = DeviceCreationError(
            error_code="LAZY_ROUTER_INIT_FAILED",
            message="test cached error",
        )
        lazy._init_error = error

        with pytest.raises(DeviceCreationError) as exc_info:
            lazy._get_router()

        assert exc_info.value is error
        assert exc_info.value.error_code == "LAZY_ROUTER_INIT_FAILED"
        assert "test cached error" in exc_info.value.message

    def test_get_router_fast_path_skips_lock_on_error(self) -> None:
        error = DeviceCreationError(
            error_code="LAZY_ROUTER_INIT_FAILED",
            message="fail fast",
        )

        lazy = make_lazy_router()
        lazy._init_error = error

        track = {"acquire_called": False}

        class TrackLock:
            def acquire(self, *args: object, **kwargs: object) -> bool:
                track["acquire_called"] = True
                return True

            def release(self) -> None:
                pass

            def __enter__(self) -> bool:
                return True

            def __exit__(self, *args: object) -> None:
                pass

        lazy._lock = TrackLock()  # type: ignore[assignment]

        with pytest.raises(DeviceCreationError):
            lazy._get_router()

        assert not track["acquire_called"]

    def test_get_router_raises_cached_error_after_lock_acquired(self) -> None:
        error = DeviceCreationError(
            error_code="LAZY_ROUTER_INIT_FAILED",
            message="test error set between checks",
        )

        lazy = make_lazy_router()

        class SetErrorLock:
            def __enter__(self):
                lazy._init_error = error
                return None

            def __exit__(self, *args: object) -> None:
                pass

        lazy._lock = SetErrorLock()  # type: ignore[assignment]

        with pytest.raises(DeviceCreationError) as exc_info:
            lazy._get_router()

        assert exc_info.value is error
        assert "test error set between checks" in exc_info.value.message


class TestThreadSafeLazyRouterGetRouterSuccessfulInit:
    def test_get_router_initializes_on_first_call(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()

        with patch(
            "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
            return_value=mock_router,
        ) as mock_init:
            result = lazy._get_router()

        assert result is mock_router
        assert lazy._router is mock_router
        mock_init.assert_called_once()

    def test_get_router_caches_router_after_init(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()

        with patch(
            "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
            return_value=mock_router,
        ):
            lazy._get_router()

        with patch(
            "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
        ) as mock_init:
            result = lazy._get_router()
            mock_init.assert_not_called()

        assert result is mock_router


class TestThreadSafeLazyRouterGetRouterFailedInit:
    def test_get_router_caches_init_error(self) -> None:
        lazy = make_lazy_router()
        init_error = RuntimeError("DB connection failed")

        with (
            patch(
                "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
                side_effect=init_error,
            ),
        ):
            with pytest.raises(DeviceCreationError) as exc_info:
                lazy._get_router()

        assert exc_info.value.error_code == "LAZY_ROUTER_INIT_FAILED"
        assert "DB connection failed" in exc_info.value.message
        assert "DB connection failed" in str(exc_info.value.context["underlying_error"])
        assert lazy._init_error is not None
        assert lazy._init_error.error_code == "LAZY_ROUTER_INIT_FAILED"

    def test_get_router_fail_fast_after_prior_failure(self) -> None:
        lazy = make_lazy_router()

        with (
            patch(
                "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
                side_effect=ValueError("Config missing"),
            ),
        ):
            with pytest.raises(DeviceCreationError):
                lazy._get_router()

        with patch(
            "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
        ) as mock_init:
            with pytest.raises(DeviceCreationError) as exc_info:
                lazy._get_router()
            mock_init.assert_not_called()

        assert exc_info.value.error_code == "LAZY_ROUTER_INIT_FAILED"
        assert "Config missing" in exc_info.value.message

    def test_get_router_double_check_already_initialized(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()

        class SetRouterLock:
            def __enter__(self):
                lazy._router = mock_router
                return None

            def __exit__(self, *args: object) -> None:
                pass

        lazy._lock = SetRouterLock()  # type: ignore[assignment]

        result = lazy._get_router()
        assert result is mock_router

    def test_get_router_init_error_captures_exception_context(self) -> None:
        lazy = make_lazy_router()

        with (
            patch(
                "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
                side_effect=ZeroDivisionError,
            ),
            patch(
                "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
                side_effect=OSError("File not found"),
            ),
        ):
            with pytest.raises(DeviceCreationError) as exc_info:
                lazy._get_router()

        assert exc_info.value.context is not None
        assert "File not found" in exc_info.value.context["underlying_error"]

    def test_get_router_module_level_singleton_state(self) -> None:
        lazy = make_lazy_router()
        assert lazy._router is None
        assert lazy._init_error is None
        assert lazy._lock is not None


class TestThreadSafeLazyRouterConcurrentBehavior:
    def test_get_router_lock_used_during_init(self) -> None:
        lazy = make_lazy_router()
        mock_router = MagicMock()

        with (
            patch(
                "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
                return_value=mock_router,
            ),
        ):
            result = lazy._get_router()

        assert result is mock_router
        assert lazy._router is mock_router

    def test_get_router_nested_exception_produces_proper_error(self) -> None:
        lazy = make_lazy_router()

        inner = KeyError("missing key")
        outer = RuntimeError("wrapper")
        outer.__cause__ = inner

        with (
            patch(
                "secbaas.core.service.paas.desktop.instance_router.initialize_instance_router",
                side_effect=outer,
            ),
        ):
            with pytest.raises(DeviceCreationError) as exc_info:
                lazy._get_router()

        assert "wrapper" in exc_info.value.message
        assert "wrapper" in exc_info.value.context["underlying_error"]
