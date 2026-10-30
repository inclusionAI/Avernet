"""Transient-gateway behavior for ``IdentityService``.

🔴 high · 2026102900120185548 · aicoding
These are the narrow cases the DeviceFileSystem contract cannot promise:
gateway errors on the *other* side of a bash call are reported by its plain
5xx. They are infrastructure signal, not a claim about the file. This is the
narrowest repair for that gap: one same-method attempt, no other request is
made, no payload is reused across attempts, and 404/client 5xx are left
exactly as they are.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentclaw.community.core.services.identity import IdentityService


def _gateway_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "identity/SOUL.md")
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=httpx.Response(status)
    )


def _service(*side_effects: object) -> tuple[IdentityService, AsyncMock]:
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(side_effect=list(side_effects))
    device_fs.write_file = AsyncMock(side_effect=list(side_effects))
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    resolver = MagicMock()

    def resolve_for_bot(*args: object, **kwargs: object) -> MagicMock:
        return MagicMock()

    resolver.resolve_for_bot.side_effect = resolve_for_bot
    return (
        IdentityService(
            path_factory=MagicMock(),
            publish_repo=MagicMock(),
            bot_repo=bot_repo,
            resolver=resolver,
            device_fs_dispatcher=dispatcher,
            binding_repo=MagicMock(),
        ),
        device_fs.read_file,
    )


@pytest.mark.asyncio
async def test_read_retries_502_then_success() -> None:
    """稳定：502 可重试一次，成功后返回内容。"""
    svc, read_file = _service(_gateway_error(502), b"# from gateway-retry")
    result = await svc.read_identity_file(
        "staff", "100018", "bot-1", "SOUL.md", "100018"
    )
    assert result == "# from gateway-retry"
    assert read_file.await_count == 2


@pytest.mark.asyncio
async def test_read_503_is_left_to_the_existing_final_error_contract() -> None:
    """稳定：503 会保留原始错误，避免在锁窗口内把重复读当成功。"""
    svc, read_file = _service(_gateway_error(503), b"# not-this-value")
    with pytest.raises(httpx.HTTPStatusError):
        await svc.read_identity_file(
            "staff", "100018", "bot-1", "SOUL.md", "100018"
        )
    assert read_file.await_count == 1


@pytest.mark.asyncio
async def test_read_retries_504_then_success() -> None:
    """稳定：504 可重试一次，成功后返回内容。"""
    svc, read_file = _service(_gateway_error(504), b"# from gateway-retry")
    result = await svc.read_identity_file(
        "staff", "100018", "bot-1", "SOUL.md", "100018"
    )
    assert result == "# from gateway-retry"
    assert read_file.await_count == 2

    svc, read_file = _service(_gateway_error(502), _gateway_error(504))
    with pytest.raises(httpx.HTTPStatusError) as e:
        await svc.read_identity_file(
            "staff", "100018", "bot-1", "SOUL.md", "100018"
        )
    assert e.value.response.status_code == 504
    assert read_file.await_count == 2


@pytest.mark.asyncio
async def test_read_404_keeps_the_missing_file_contract_and_never_retries() -> None:
    """稳定：404 是“文件不存在”的终态答案，不触发任何重试。"""
    svc, read_file = _service(_gateway_error(404))
    assert await svc.read_identity_file(
        "staff", "100018", "bot-1", "SOUL.md", "100018"
    ) == ""
    assert read_file.await_count == 1


@pytest.mark.asyncio
async def test_read_client_error_residual_is_surfaced_immediately() -> None:
    """稳定：401 不被视作网关重试，只返回 first error。"""
    svc, read_file = _service(_gateway_error(401), b"# no-retry-success")
    with pytest.raises(httpx.HTTPStatusError):
        await svc.read_identity_file(
            "staff", "100018", "bot-1", "SOUL.md", "100018"
        )
    assert read_file.await_count == 1


@pytest.mark.asyncio
async def test_read_non_gateway_5xx_is_surfaced_after_one_attempt() -> None:
    """稳定：500 保留报错语义，不借用 404 逻辑，也不重试。"""
    svc, read_file = _service(_gateway_error(500))
    with pytest.raises(httpx.HTTPStatusError):
        await svc.read_identity_file(
            "staff", "100018", "bot-1", "SOUL.md", "100018"
        )
    assert read_file.await_count == 1


@pytest.mark.asyncio
async def test_write_retries_502_then_success() -> None:
    """稳定：502 时重发相同的完整写。"""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock(
        side_effect=[_gateway_error(502), None]
    )
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = lambda *args, **kwargs: MagicMock()
    svc = IdentityService(
        path_factory=MagicMock(),
        publish_repo=MagicMock(),
        bot_repo=bot_repo,
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
        binding_repo=MagicMock(),
    )
    await svc.write_identity_file(
        "staff", "100018", "bot-1", "SOUL.md", "# final-old", "100018"
    )
    assert device_fs.write_file.await_count == 2
    assert [c.args for c in device_fs.write_file.await_args_list] == [
        ("identity/SOUL.md", b"# final-old"),
        ("identity/SOUL.md", b"# final-old"),
    ]


@pytest.mark.asyncio
async def test_write_503_is_left_to_the_existing_final_error_contract() -> None:
    """稳定：503 不与短 IO 锁窗口绑定，失败立即抛出。"""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock(side_effect=[_gateway_error(503)])
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = lambda *args, **kwargs: MagicMock()
    svc = IdentityService(
        path_factory=MagicMock(),
        publish_repo=MagicMock(),
        bot_repo=bot_repo,
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
        binding_repo=MagicMock(),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await svc.write_identity_file(
            "staff", "100018", "bot-1", "SOUL.md", "# final", "100018"
        )
    assert device_fs.write_file.await_count == 1


@pytest.mark.asyncio
async def test_write_retries_504_then_success_with_one_write() -> None:
    """稳定：504 时重发相同的完整写，避免重复状态不一致。"""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock(
        side_effect=[_gateway_error(504), None]
    )
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = lambda *args, **kwargs: MagicMock()
    svc = IdentityService(
        path_factory=MagicMock(),
        publish_repo=MagicMock(),
        bot_repo=bot_repo,
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
        binding_repo=MagicMock(),
    )
    await svc.write_identity_file(
        "staff", "100018", "bot-1", "SOUL.md", "# run-2", "100018"
    )
    assert [c.args for c in device_fs.write_file.await_args_list] == [
        ("identity/SOUL.md", b"# run-2"),
        ("identity/SOUL.md", b"# run-2"),
    ]


@pytest.mark.asyncio
async def test_write_client_error_residual_is_surfaced_immediately() -> None:
    """稳定：401 不被视作网关重试，一次失败后立即上抛。"""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock(
        side_effect=[_gateway_error(401), None]
    )
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = lambda *args, **kwargs: MagicMock()
    svc = IdentityService(
        path_factory=MagicMock(),
        publish_repo=MagicMock(),
        bot_repo=bot_repo,
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
        binding_repo=MagicMock(),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await svc.write_identity_file(
            "staff", "100018", "bot-1", "SOUL.md", "# c", "100018"
        )
    assert device_fs.write_file.await_count == 1


@pytest.mark.asyncio
async def test_write_non_gateway_5xx_is_surfaced_after_one_attempt() -> None:
    """稳定：551/500 不借用 404 逻辑，也不重试。"""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock(side_effect=[_gateway_error(500)])
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = lambda *args, **kwargs: MagicMock()
    svc = IdentityService(
        path_factory=MagicMock(),
        publish_repo=MagicMock(),
        bot_repo=bot_repo,
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
        binding_repo=MagicMock(),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await svc.write_identity_file(
            "staff", "100018", "bot-1", "SOUL.md", "other", "100018"
        )
    assert [c.args for c in device_fs.write_file.await_args_list] == [
        ("identity/SOUL.md", b"other")
    ]
