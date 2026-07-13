"""BCN uplink PostRunCallback 实现。

BCN 来源 run 执行完成后补发 uplink 事件，由 Worker 或 TaskMessageDispatcher
在执行完毕后通过 ``callback_function`` 名查找并调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from secbaas.community.api.bcn import ChatEvent, EventMessage, EventSpeaker, EventUsage
from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.logger import get_logger

from ._protocol import UplinkClient

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot_run import BotRunRecord

logger = get_logger("core-bot-run")


def _build_chat_event(
    record: BotRunRecord,
    task_exception: BaseException | None,
) -> ChatEvent:
    """根据 BotRunRecord 构造 ChatEvent"""
    if record.status == "FAILED":
        state = "error"
    else:
        state = "final"

    message: EventMessage | None = None
    content = record.result_content_long if state == "final" else record.error
    if content:
        speaker: EventSpeaker | None = None
        if record.metadata and isinstance(record.metadata, dict):
            from_ref = record.metadata.get("from_ref")
            if isinstance(from_ref, dict):
                speaker = EventSpeaker(
                    kind=from_ref.get("kind", "bot"),
                    id=from_ref.get("id"),
                    name=from_ref.get("name"),
                )
        message = EventMessage(text=content, speaker=speaker)

    usage: EventUsage | None = None
    if record.result_extra and isinstance(record.result_extra, dict):
        usage_data = record.result_extra.get("usage")
        if isinstance(usage_data, dict):
            usage = EventUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                model=usage_data.get("model"),
                latency_ms=usage_data.get("latency_ms"),
            )

    return ChatEvent(
        run_id=record.run_id,
        seq=0,
        state=state,
        message=message,
        usage=usage,
    )


class BcnUplinkCallback:
    """BCN 来源 run 执行完成后补发 uplink 事件的 PostRunCallback 实现。

    队列化前回调挂在 ``asyncio.Task.add_done_callback`` 上；队列化后请求由 Worker
    跨进程执行，原 Task 不存在，改由本回调在内层执行器返回后处理。内容读
    ``baas_bot_run``（结果/状态/metadata），去重标记写在队列工作项的 ``meta`` JSON 中。

    触发条件（与旧行为对齐）：``baas_bot_run.metadata.from_bcn`` 为真且
    ``request_type != "inject"`` 且 run 处终态（COMPLETED/FAILED）。

    去重：``send_event`` 以 ``event_id=run_id`` 在 BCN 侧幂等；队列 ``meta.bcn_callback_sent``
    标记避免恢复重跑重复上报。标记 **在上报成功后** 才置位，上报失败保持未置位，
    留给恢复重投（至少一次 + BCN 侧去重）。
    """

    def __init__(
        self,
        uplink_client: UplinkClient,
        run_repository: BotRunRepository,
    ) -> None:
        self._uplink_client = uplink_client
        self._run = run_repository

    async def __call__(self, run_id: str) -> None:
        run = self._run.get_by_run_id(run_id)
        if run is None:
            logger.info(
                "[BcnUplinkCallback] skip: run_id=%s run_found=%s",
                run_id,
                run is not None,
            )
            return

        if run.status not in ("COMPLETED", "FAILED"):
            return  # 非终态：交由恢复流程在终态后上报

        event = _build_chat_event(run, None)
        try:
            result = await self._uplink_client.send_event(
                event, bot_id=run.bot_id, event_id=run_id
            )
            logger.info(
                "[BcnUplinkCallback] uplink sent run_id=%s ok=%s dedup=%s",
                run_id,
                getattr(result, "ok", None),
                getattr(result, "deduplicated", None),
            )
        except Exception as e:
            logger.error(
                "[BcnUplinkCallback] uplink failed run_id=%s err=%s", run_id, e
            )
