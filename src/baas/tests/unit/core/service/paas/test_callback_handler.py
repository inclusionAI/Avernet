"""Unit tests for core.service.paas callback_handler.DeviceCallbackHandler.

DeviceCallbackHandler is a DI-managed class whose executor is injected via a lazy
factory (protocol-compliant callable). It delegates device callbacks, performs no
container access, and does not import the concrete publish_manage module.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.paas import DeviceCallbackHandler


@pytest.mark.asyncio
async def test_handle_delegates_to_injected_executor():
    """WHEN handle() is called, THEN it delegates to the injected executor."""
    callback = MagicMock()
    executor = MagicMock()
    executor.handle_device_callback = AsyncMock(return_value={"status": "ok"})

    handler = DeviceCallbackHandler(lambda: executor)

    result = await handler.handle(callback)

    assert result == {"status": "ok"}
    executor.handle_device_callback.assert_awaited_once_with(callback)


@pytest.mark.asyncio
async def test_executor_is_resolved_lazily():
    """WHEN the handler is constructed, THEN the executor is not yet resolved."""
    executor = MagicMock()
    executor.handle_device_callback = AsyncMock(return_value={"status": "ok"})
    resolved = []

    def factory():
        resolved.append(True)
        return executor

    handler = DeviceCallbackHandler(factory)
    assert resolved == []

    await handler.handle(MagicMock())
    assert resolved == [True]


@pytest.mark.asyncio
async def test_handle_propagates_executor_exception():
    """WHEN the executor raises, THEN the handler propagates the exception."""
    executor = MagicMock()
    executor.handle_device_callback = AsyncMock(side_effect=RuntimeError("boom"))

    handler = DeviceCallbackHandler(lambda: executor)

    with pytest.raises(RuntimeError, match="boom"):
        await handler.handle(MagicMock())


def test_module_does_not_import_publish_manage():
    """WHEN the callback handler module is imported, THEN it does not import publish_manage.

    The handler depends only on a locally-declared protocol, keeping the
    device_service ↔ publish_service import graph acyclic.
    """
    import ast
    from pathlib import Path

    src = Path("src/secbaas/community/core/service/paas/_callback_handler.py").resolve()
    tree = ast.parse(src.read_text())
    imports = [
        n.names[0].name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module
    ]
    assert not any("publish_manage" in i for i in imports)


def test_publish_service_factory_is_required():
    """WHEN the publish_service_factory argument is omitted, THEN construction fails fast.

    Covers the fail-at-construction (vs lazy call-time) requirement: a missing
    callback dependency surfaces immediately rather than being silently tolerated.
    """
    with pytest.raises(TypeError):
        DeviceCallbackHandler()  # noqa: E1120
