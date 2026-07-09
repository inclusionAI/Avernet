"""Unit tests for hook_executor module.

Tests the shared bounded ThreadPoolExecutor used for async hook execution.
Covers get_hook_executor lifecycle (create/return singleton) and
shutdown_hook_executor behaviour.
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# get_hook_executor
# ---------------------------------------------------------------------------


def test_get_hook_executor_creates_singleton():
    import secbaas.core.service.paas._hook_executor as mod

    mod._EXECUTOR = None

    with patch.object(mod, "logger", MagicMock()) as mock_logger:
        ex1 = mod.get_hook_executor()
        ex2 = mod.get_hook_executor()

    assert ex1 is ex2
    assert isinstance(ex1, ThreadPoolExecutor)
    assert ex1 is mod._EXECUTOR
    assert mock_logger.info.call_count == 1
    assert "max_workers=20" in mock_logger.info.call_args[0][0]


def test_get_hook_executor_returns_existing():
    import secbaas.core.service.paas._hook_executor as mod

    dummy = ThreadPoolExecutor(max_workers=1)
    mod._EXECUTOR = dummy

    with patch.object(mod, "logger", MagicMock()) as mock_logger:
        result = mod.get_hook_executor()

    assert result is dummy
    mock_logger.info.assert_not_called()

    dummy.shutdown(wait=False)
    mod._EXECUTOR = None


# ---------------------------------------------------------------------------
# shutdown_hook_executor
# ---------------------------------------------------------------------------


def test_shutdown_hook_executor_when_none():
    import secbaas.core.service.paas._hook_executor as mod

    mod._EXECUTOR = None

    with patch.object(mod, "logger", MagicMock()) as mock_logger:
        mod.shutdown_hook_executor(wait=True)

    mock_logger.info.assert_not_called()
    assert mod._EXECUTOR is None


def test_shutdown_hook_executor_with_wait_true():
    import secbaas.core.service.paas._hook_executor as mod

    mock_executor = MagicMock(spec=ThreadPoolExecutor)
    mod._EXECUTOR = mock_executor

    with patch.object(mod, "logger", MagicMock()) as mock_logger:
        mod.shutdown_hook_executor(wait=True)

    mock_executor.shutdown.assert_called_once_with(wait=True)
    assert mod._EXECUTOR is None
    assert mock_logger.info.call_count == 2


def test_shutdown_hook_executor_with_wait_false():
    import secbaas.core.service.paas._hook_executor as mod

    mock_executor = MagicMock(spec=ThreadPoolExecutor)
    mod._EXECUTOR = mock_executor

    mod.shutdown_hook_executor(wait=False)

    mock_executor.shutdown.assert_called_once_with(wait=False)
    assert mod._EXECUTOR is None


# ---------------------------------------------------------------------------
# Integration / edge cases
# ---------------------------------------------------------------------------


def test_get_hook_executor_max_workers():
    import secbaas.core.service.paas._hook_executor as mod

    mod._EXECUTOR = None

    ex = mod.get_hook_executor()
    assert ex._max_workers == 20  # type: ignore[attr-defined]

    ex.shutdown(wait=False)
    mod._EXECUTOR = None


def test_get_then_shutdown_cycle():
    import secbaas.core.service.paas._hook_executor as mod

    mod._EXECUTOR = None

    ex = mod.get_hook_executor()
    assert isinstance(ex, ThreadPoolExecutor)
    assert mod._EXECUTOR is ex

    fut = ex.submit(lambda: 42)
    assert fut.result() == 42

    mod.shutdown_hook_executor(wait=True)
    assert mod._EXECUTOR is None


def test_shutdown_then_get_new_executor():
    import secbaas.core.service.paas._hook_executor as mod

    mod._EXECUTOR = None

    ex1 = mod.get_hook_executor()
    mod.shutdown_hook_executor(wait=True)

    ex2 = mod.get_hook_executor()
    assert ex2 is not ex1
    assert isinstance(ex2, ThreadPoolExecutor)

    ex2.shutdown(wait=False)
    mod._EXECUTOR = None
