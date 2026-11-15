"""Boot-time read-repair: 归位协作者 scope 漂移的副屏 CDN 记录。

背景:legacy ``/api/bot-render-screens`` POST 曾把记录 ``owner_id`` 写成
「创建者登录人工号」,而 list/authorize 均经 ``resource_locator`` 把查询侧的
owner 归一化成 Bot 的 ``entity_id``——漂移行对归属者/协作者稳定不可见、不可
管理(协作者在他人 Bot 配置副屏后,刷新页面报「组件库不存在」)。create 侧已在
service 层统一落 ``bot.owner_id``;本模块在应用启动(Lifecycle startup)时把
存量漂移行一次性归位,并持续收敛旧实例未下线期间产生的增量。

判定谓词(与 ODC 探查脚本语义一致):

- 漂移 = 行 ``owner_id`` 与该 ``(bot_id, env)`` 下 live bot 的
  ``owner_id``/``entity_id`` 均不匹配;
- 仅当该 ``(bot_id, env)`` 下 live bot 恰好唯一时才归位——共享 bot_id(如
  ``default``,每用户一行)无法单值判定归属,保持不动;
- 目标值为该唯一 bot 的 ``entity_id``(记录的 canonical scope)。

幂等:目标值确定,重复执行无害,多副本并发归位也安全(同值 UPDATE)。
Best-effort:单条失败仅记日志,外层异常同样吞掉——lifespan setup 是 fail-fast,
数据修复永远不允许阻塞应用启动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
# Protocol 必须运行时导入:python-injector 通过 get_type_hints 解析 @inject
# 构造参数,TYPE_CHECKING 内的名字在运行时不存在会造成 DI 装配失败。
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    RenderScreenRepository,
)

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.render_screen.models import (
        RenderScreenRecord,
    )

logger = get_logger()


@dataclass(frozen=True)
class DriftRepair:
    """一条待归位的漂移修复(纯数据,便于测试断言)。"""

    record_id: int
    bot_id: str
    from_owner_id: str
    to_owner_id: str


def compute_drift_repairs(
    rows: list[RenderScreenRecord],
    live_bots_by_bot_id: dict[str, list[dict[str, Any]]],
) -> list[DriftRepair]:
    """纯函数:由全量 render 行 + 各 bot_id 的 live bot 行,计算漂移修复清单。"""
    repairs: list[DriftRepair] = []
    for row in rows:
        bots = live_bots_by_bot_id.get(row.bot_id) or []
        if len(bots) != 1:
            # bot 行缺失或共享 bot_id(多 owner):归属无法单值判定,不动。
            continue
        bot = bots[0]
        entity_id = str(bot.get("entity_id") or "")
        owner_id = str(bot.get("owner_id") or "")
        if not entity_id:
            continue
        if row.owner_id in (entity_id, owner_id):
            # 已是合法 scope(canonical entity 或归属者裸值)。
            continue
        repairs.append(
            DriftRepair(
                record_id=row.id,
                bot_id=row.bot_id,
                from_owner_id=row.owner_id,
                to_owner_id=entity_id,
            )
        )
    return repairs


def backfill_render_screen_owner_scope(
    render_repo: RenderScreenRepository,
    bot_repo: BotRepository,
) -> int:
    """执行一次全量扫描归位,返回修复行数(小表,<百行级)。"""
    rows = render_repo.list_all_for_backfill()
    if not rows:
        return 0

    live_bots_by_bot_id = {
        bot_id: bot_repo.list_live_by_bot_id(bot_id)
        for bot_id in {row.bot_id for row in rows}
    }

    repaired = 0
    for repair in compute_drift_repairs(rows, live_bots_by_bot_id):
        try:
            render_repo.update_owner_by_id(
                record_id=repair.record_id,
                owner_id=repair.to_owner_id,
            )
            repaired += 1
            logger.info(
                "[RenderScreenBackfill] 归位漂移行 id=%s bot_id=%s owner_id %s -> %s",
                repair.record_id,
                repair.bot_id,
                repair.from_owner_id,
                repair.to_owner_id,
            )
        except Exception as exc:  # 单条失败不终止整批
            logger.warning(
                "[RenderScreenBackfill] 修复失败 id=%s bot_id=%s err=%s",
                repair.record_id,
                repair.bot_id,
                exc,
            )

    if repaired:
        logger.info("[RenderScreenBackfill] 启动订正完成,共归位 %d 行", repaired)
    else:
        logger.debug("[RenderScreenBackfill] 无漂移行,跳过")
    return repaired


class RenderScreenOwnerScopeBackfill(LifecycleBase):
    """Lifecycle 参与方:startup 阶段惰性订正一次(幂等、best-effort)。"""

    @inject
    def __init__(
        self,
        render_repo: RenderScreenRepository,
        bot_repo: BotRepository,
    ) -> None:
        self._render_repo = render_repo
        self._bot_repo = bot_repo

    async def startup(self) -> None:
        try:
            backfill_render_screen_owner_scope(self._render_repo, self._bot_repo)
        except Exception as exc:
            # 数据订正永远不允许阻塞应用启动;失败仅记日志,等下次滚动发布重试。
            logger.warning("[RenderScreenBackfill] 启动订正未完成,跳过: %s", exc)
