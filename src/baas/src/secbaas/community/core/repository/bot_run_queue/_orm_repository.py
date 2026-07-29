import json
from datetime import datetime, timedelta

from sqlalchemy import func

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._orm_model import BotRunQueueModel
from ._protocol import BotRunQueueRepository
from ._record import BotRunQueueRecord

log = get_logger("orm-repository")


class OrmBotRunQueueRepository(OrmConnectionMixin, BotRunQueueRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_queue(
        self,
        *,
        run_id: str,
        bot_id: str,
        session_id: str | None = None,
        meta: dict | None = None,
    ) -> str:
        meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
        env = get_current_env()
        row = BotRunQueueModel(
            run_id=run_id,
            bot_id=bot_id,
            session_id=session_id,
            status="PENDING",
            meta=meta_json,
            env=env,
        )
        self._session.add(row)
        self._session.flush()
        log.info(
            "[bot-run-queue:insert] run_id=%s bot_id=%s session_id=%s env=%s",
            run_id,
            bot_id,
            session_id,
            env,
        )
        return run_id

    @with_orm_session
    def get_by_run_id(self, run_id: str) -> BotRunQueueRecord | None:
        row = (
            self._session.query(BotRunQueueModel)
            .filter(BotRunQueueModel.run_id == run_id)
            .first()
        )
        return row.to_record() if row else None

    @with_orm_session
    def discover_active_bots(self, limit: int = 50) -> list[str]:
        """发现有 PENDING 工作项的 bot_id 列表（确定性顺序，避免活锁）。"""
        env = get_current_env()
        rows = (
            self._session.query(BotRunQueueModel.bot_id)
            .filter(
                BotRunQueueModel.status == "PENDING",
                BotRunQueueModel.env == env,
            )
            .distinct()
            .order_by(BotRunQueueModel.bot_id.asc())
            .limit(limit)
            .all()
        )
        return [r[0] for r in rows]

    @with_orm_session
    def claim_pending_by_bot(
        self,
        bot_id: str,
        worker_id: str,
        *,
        candidates: int = 5,
    ) -> BotRunQueueRecord | None:
        """无锁乐观认领指定 bot 的一个 PENDING 工作项。

        不使用 ``SKIP LOCKED``（OceanBase 版本未验证、SQLite 测试后端不支持），
        改用"条件 UPDATE + 检查 affected rows"实现行级出队互斥：
        ``UPDATE ... WHERE run_id=? AND status='PENDING'`` 是原子的，
        并发 Worker 中只有一个能把某行从 PENDING 翻成 RUNNING（affected=1），
        其余 affected=0 自动跳到下一候选。OB(InnoDB) 与 SQLite 行为一致。

        同 session 串行不在此处保证 —— 由上层 DistributedLockService 的
        session 维度锁负责（见 core/service 层）。
        """
        rows = (
            self._session.query(BotRunQueueModel.run_id)
            .filter(
                BotRunQueueModel.bot_id == bot_id,
                BotRunQueueModel.status == "PENDING",
                BotRunQueueModel.env == get_current_env(),
            )
            .order_by(BotRunQueueModel.gmt_create.asc())
            .limit(candidates)
            .all()
        )
        now = datetime.now()
        for (run_id,) in rows:
            updated = (
                self._session.query(BotRunQueueModel)
                .filter(
                    BotRunQueueModel.run_id == run_id,
                    BotRunQueueModel.status == "PENDING",
                )
                .update(
                    {
                        "status": "RUNNING",
                        "assigned_worker": worker_id,
                        "last_heartbeat": now,
                        "gmt_modified": func.now(),
                    },
                    synchronize_session=False,
                )
            )
            if updated == 1:
                row = (
                    self._session.query(BotRunQueueModel)
                    .filter(BotRunQueueModel.run_id == run_id)
                    .first()
                )
                log.info(
                    "[bot-run-queue:claim] worker=%s claimed run_id=%s bot_id=%s",
                    worker_id,
                    run_id,
                    bot_id,
                )
                return row.to_record() if row else None
        return None

    @with_orm_session
    def touch_heartbeat(self, run_id: str, worker_id: str) -> None:
        """刷新当前 Worker 持有的 RUNNING 工作项心跳（供宕机恢复判活）。"""
        self._session.query(BotRunQueueModel).filter(
            BotRunQueueModel.run_id == run_id,
            BotRunQueueModel.status == "RUNNING",
            BotRunQueueModel.assigned_worker == worker_id,
        ).update({"last_heartbeat": datetime.now()}, synchronize_session=False)

    @with_orm_session
    def release_to_pending(self, run_id: str, worker_id: str) -> int:
        """把已置 RUNNING 但未能开跑（抢锁/限流失败）的工作项放回 PENDING。

        仅当当前 Worker 仍持有该 RUNNING 行时生效，返回受影响行数。
        """
        updated = (
            self._session.query(BotRunQueueModel)
            .filter(
                BotRunQueueModel.run_id == run_id,
                BotRunQueueModel.status == "RUNNING",
                BotRunQueueModel.assigned_worker == worker_id,
            )
            .update(
                {
                    "status": "PENDING",
                    "assigned_worker": None,
                    "last_heartbeat": None,
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        affected = int(updated)
        if affected == 0:
            row = (
                self._session.query(BotRunQueueModel)
                .filter(BotRunQueueModel.run_id == run_id)
                .first()
            )
            cur_status = row.status if row else "NOT_FOUND"
            log.warning(
                "[bot-run-queue:release] run_id=%s worker=%s updated=0 cur_status=%s "
                "(expected RUNNING owned by worker, may have been reset/reclaimed/DONE)",
                run_id,
                worker_id,
                cur_status,
            )
        else:
            log.info(
                "[bot-run-queue:release] run_id=%s worker=%s RUNNING->PENDING",
                run_id,
                worker_id,
            )
        return affected

    @with_orm_session
    def mark_done(self, run_id: str, worker_id: str) -> int:
        """执行写入终态后标记工作项 DONE（仅当当前 Worker 仍持有 RUNNING 时生效）。

        DONE 行不再被发现/认领/恢复触碰，等待 TTL 清理。结果正文已落
        ``baas_bot_run``，本表只留工作轨迹。
        """
        updated = (
            self._session.query(BotRunQueueModel)
            .filter(
                BotRunQueueModel.run_id == run_id,
                BotRunQueueModel.status == "RUNNING",
                BotRunQueueModel.assigned_worker == worker_id,
            )
            .update(
                {"status": "DONE", "gmt_modified": func.now()},
                synchronize_session=False,
            )
        )
        affected = int(updated)
        if affected == 0:
            row = (
                self._session.query(BotRunQueueModel)
                .filter(BotRunQueueModel.run_id == run_id)
                .first()
            )
            cur_status = row.status if row else "NOT_FOUND"
            log.warning(
                "[bot-run-queue:mark_done] run_id=%s worker=%s updated=0 cur_status=%s "
                "(expected RUNNING owned by worker, executor may have released/reclaimed)",
                run_id,
                worker_id,
                cur_status,
            )
        else:
            log.info(
                "[bot-run-queue:mark_done] run_id=%s worker=%s RUNNING->DONE",
                run_id,
                worker_id,
            )
        return affected

    @with_orm_session
    def force_done(self, run_id: str) -> int:
        """无论 PENDING/RUNNING 直接标记 DONE（超时终结用）。"""
        updated = (
            self._session.query(BotRunQueueModel)
            .filter(
                BotRunQueueModel.run_id == run_id,
                BotRunQueueModel.status.in_(("PENDING", "RUNNING")),
            )
            .update(
                {"status": "DONE", "gmt_modified": func.now()},
                synchronize_session=False,
            )
        )
        return int(updated)

    @with_orm_session
    def reset_stale_running(self, stale_seconds: int) -> int:
        """心跳过期的 RUNNING 工作项重置为 PENDING，供其他 Worker 重新认领。

        判活基于 ``last_heartbeat``（Worker 执行期间周期刷新），不与"最长
        响应时间"耦合。返回重置的行数。
        """
        threshold = datetime.now() - timedelta(seconds=stale_seconds)
        updated = (
            self._session.query(BotRunQueueModel)
            .filter(
                BotRunQueueModel.status == "RUNNING",
                BotRunQueueModel.env == get_current_env(),
                BotRunQueueModel.last_heartbeat.isnot(None),
                BotRunQueueModel.last_heartbeat < threshold,
            )
            .update(
                {
                    "status": "PENDING",
                    "assigned_worker": None,
                    "last_heartbeat": None,
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        count = int(updated)
        if count:
            log.warning(
                "[bot-run-queue:reset_stale_running] reset %s stale RUNNING -> "
                "PENDING (stale_seconds=%s)",
                count,
                stale_seconds,
            )
        return count

    @with_orm_session
    def count_pending_by_bot(self, bot_id: str) -> int:
        """统计某 bot 的 PENDING 队列深度（供入口背压判断）。"""
        return int(
            self._session.query(BotRunQueueModel)
            .filter(
                BotRunQueueModel.bot_id == bot_id,
                BotRunQueueModel.status == "PENDING",
                BotRunQueueModel.env == get_current_env(),
            )
            .count()
        )

    @with_orm_session
    def update_meta(self, run_id: str, updates: dict) -> bool:
        """合并更新队列工作项的 meta JSON 字段。

        读取当前 meta，用 ``updates`` 合并后写回。若行不存在返回 False。
        """
        row = (
            self._session.query(BotRunQueueModel)
            .filter(BotRunQueueModel.run_id == run_id)
            .first()
        )
        if row is None:
            return False
        current = {}
        if row.meta:
            try:
                current = json.loads(row.meta)
            except (json.JSONDecodeError, TypeError):
                current = {}
        current.update(updates)
        row.meta = json.dumps(current, ensure_ascii=False)
        row.gmt_modified = func.now()
        self._session.flush()
        return True

    @with_orm_session
    def scan_timeout(self, limit: int = 200) -> list[BotRunQueueRecord]:
        """扫描 PENDING/RUNNING 中已超时的工作项。

        查询非终态行，在 Python 层解析 meta.timeout 判断是否超时
        （meta 是 JSON Text，跨数据库 JSON 函数兼容性差）。
        """
        env = get_current_env()
        rows = (
            self._session.query(BotRunQueueModel)
            .filter(
                BotRunQueueModel.status.in_(("PENDING", "RUNNING")),
                BotRunQueueModel.env == env,
            )
            .order_by(BotRunQueueModel.gmt_create.asc())
            .limit(limit)
            .all()
        )
        now = datetime.now()
        result: list[BotRunQueueRecord] = []
        for row in rows:
            meta = json.loads(row.meta) if row.meta else {}
            timeout = meta.get("timeout")
            if timeout is None or row.gmt_create is None:
                continue
            deadline = row.gmt_create + timedelta(seconds=float(timeout))
            if now > deadline:
                result.append(row.to_record())
        return result
