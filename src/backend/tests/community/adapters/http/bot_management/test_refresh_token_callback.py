"""refresh-token 回调按 (bot_id, owner_workno) 跨租户解析,不被 tenant guard 挡。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotService,
    BotServiceError,
)
from agentclaw.community.adapters.http.bot_management.router import (
    refresh_bot_passport_token,
)


def _svc_with_bot(bot: dict | None) -> BotService:
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._repository.get_by_id_and_owner.return_value = bot
    svc._passport_plugin = MagicMock()
    return svc


def test_callback_passes_cross_tenant_option():
    # 关键契约:hot_update_passport_token_to_device 的 bot 查询带 skip_avernet_tenant_guard
    # (回调走 /api→默认租户,外部租户 bot 需跨租户直查)。下游链路可能需更多 mock,
    # 这里只断言 lookup 调用契约,允许下游抛错。
    bot = {"bot_id": "20260731_abcd1234", "owner_id": "user001", "bot_type": "personal",
           "binding_id": None, "ext": {}}
    svc = _svc_with_bot(bot)
    try:
        svc.hot_update_passport_token_to_device(
            bot_id="20260731_abcd1234", user_id="user001", token="tok_xyz"
        )
    except Exception:
        pass  # 下游 device 热更新链路可能需更多 mock;本测试只锁定 lookup 契约
    call = svc._repository.get_by_id_and_owner.call_args
    assert call.kwargs["execution_options"]["skip_avernet_tenant_guard"] is True


def test_callback_missing_bot_raises_not_found():
    svc = _svc_with_bot(None)
    with pytest.raises(BotNotFoundError):
        svc.hot_update_passport_token_to_device(
            bot_id="missing", user_id="user001", token="tok"
        )


@pytest.mark.asyncio
async def test_http_callback_success_contract_is_unchanged():
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "bot_id": "service-bot-1",
            "owner_workno": "owner-1",
            "token": "fake-owner-passport-token",
        }
    )
    svc = MagicMock()
    svc.hot_update_passport_token_to_device.return_value = {
        "token_prefix": "fake-owner-passport-",
        "bindings": [{"binding_id": 30, "type": "caller"}],
    }

    response = await refresh_bot_passport_token(request=request, bot_service=svc)

    assert response.success is True
    assert response.error_code == 200
    assert response.message == "Passport Token 刷新成功"


@pytest.mark.asyncio
async def test_http_callback_partial_failure_keeps_retryable_500_contract():
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "bot_id": "service-bot-1",
            "owner_workno": "owner-1",
            "token": "fake-owner-passport-token",
        }
    )
    svc = MagicMock()
    svc.hot_update_passport_token_to_device.side_effect = BotServiceError(
        "部分设备热更新失败: caller(binding_id=30): unavailable"
    )

    response = await refresh_bot_passport_token(request=request, bot_service=svc)

    assert response.success is False
    assert response.error_code == 500
    assert response.data is None
