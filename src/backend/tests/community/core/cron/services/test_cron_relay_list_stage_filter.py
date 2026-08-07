"""``list_all_crons`` 的 runtime_stage 过滤。

openapi_v1 的 routines 列表只操作草稿态：服务 Bot 的 verify/online 运行态
既不能出现在结果里，也不能被查询（发布态设备的失败项同样不该泄漏到公开面）。
内部控制台不传 stage，保持全运行态聚合——默认行为必须不变。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget,
    RUNTIME_STAGE_DRAFT,
    RUNTIME_STAGE_ONLINE,
)


def _make_service(*, bot_provider=None):
    return CronRelayService(
        bot_provider=bot_provider or MagicMock(),
        device_provider=MagicMock(),
        transport=MagicMock(),
        resolver=MagicMock(),
        template_repo=MagicMock(),
        publish_repo=MagicMock(),
    )


def _target(stage: str, binding_id: int) -> CronRuntimeTarget:
    return CronRuntimeTarget(
        bot_id="bot-1",
        bot_name="bot-1",
        owner_id="user-1",
        bot_type="service",
        runtime_stage=stage,
        binding_id=binding_id,
    )


def _service_with_stage_targets():
    """一个已发布服务 Bot：draft + online 两个运行态目标，外加一个发布态失败项。"""
    bot_provider = MagicMock()
    bot_provider.get_bot.return_value = {
        "bot_id": "bot-1",
        "bot_type": "service",
        "owner_id": "user-1",
        "binding_id": 1,
    }
    svc = _make_service(bot_provider=bot_provider)
    svc._build_runtime_targets = MagicMock(
        return_value=(
            [_target(RUNTIME_STAGE_DRAFT, 1), _target(RUNTIME_STAGE_ONLINE, 42)],
            [
                {
                    "bot_id": "bot-1",
                    "runtime_stage": RUNTIME_STAGE_ONLINE,
                    "reason": "publish_query_failed",
                }
            ],
        )
    )
    svc._fetch_runtime_target_crons = AsyncMock(
        return_value={"success": True, "data": [{"task_id": "t1"}]}
    )
    return svc


@pytest.mark.asyncio
async def test_draft_stage_filter_neither_returns_nor_queries_published_targets():
    svc = _service_with_stage_targets()

    result = await svc.list_all_crons(
        user_id="user-1", nick_name="user-1", bot_id="bot-1",
        runtime_stage=RUNTIME_STAGE_DRAFT,
    )

    # 只取草稿态目标——发布态设备一次都不被触达。
    fetched = [
        call.args[0] for call in svc._fetch_runtime_target_crons.await_args_list
    ]
    assert [t.runtime_stage for t in fetched] == [RUNTIME_STAGE_DRAFT]
    assert all(c.get("runtime_stage") == RUNTIME_STAGE_DRAFT for c in result["data"])
    # 发布态的失败项同样不进入公开面的响应。
    assert result["failed_targets"] == []


@pytest.mark.asyncio
async def test_no_stage_keeps_the_all_stage_aggregation():
    """默认（内部控制台）行为不变：全部运行态都被查询。"""
    svc = _service_with_stage_targets()

    result = await svc.list_all_crons(
        user_id="user-1", nick_name="user-1", bot_id="bot-1",
    )

    fetched = [
        call.args[0] for call in svc._fetch_runtime_target_crons.await_args_list
    ]
    assert {t.runtime_stage for t in fetched} == {
        RUNTIME_STAGE_DRAFT,
        RUNTIME_STAGE_ONLINE,
    }
    assert len(result["failed_targets"]) == 1


@pytest.mark.asyncio
async def test_an_unknown_stage_is_refused_before_any_bot_is_read():
    bot_provider = MagicMock()
    svc = _make_service(bot_provider=bot_provider)

    with pytest.raises(CronRelayError):
        await svc.list_all_crons(
            user_id="user-1", nick_name="user-1", bot_id="bot-1",
            runtime_stage="something-new",
        )

    bot_provider.get_bot.assert_not_called()
