"""``TaskArtifactRepositoryProtocol`` implementation for ``task_artifact``
(不可变任务产物 manifest,spec:2026-09-23-task-artifact-manifest)。

* 幂等发布:``create_or_get`` 先 INSERT,撞 ``uk_task_artifact_dedupe``
  (task_id, node_id, attempt, content_hash) 时回读已存行返回 —— 双写 fold
  重放的库层兜底(fire 时点在版本重放成功之后,是第一重闸;这里是第二重)。
  ``IntegrityError`` 后 session 处于失败态,须先 ``rollback()`` 再回读。
* 不可变语义:**没有任何 update 主字段方法** —— 内容修订 = 新 artifact_id +
  ``supersedes``(由 Service 组链,repos 只存)。
* 排序契约:一律 ``attempt DESC, created_at DESC, id DESC``(最新在前);
  ``latest_for_node`` 取首行即 primary 候选。
* 惯例对齐 trajectory repo:``orm_session()`` / ``flush()`` / no-commit;
  读返回 detached frozen record(ORM ``to_record()``)。持久化失败原样上抛
  (spec:"持久化写入失败必须向上返回错误"——不在观测层吞)。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskArtifactModel
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = logging.getLogger("task.artifact.repo")


class TaskArtifactRepository(TaskArtifactRepositoryProtocol):
    """Unified ORM implementation for ``task_artifact`` (SQLite + OceanBase)。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskArtifactModel

    # ------------------------------------------------------------------
    # row helpers(单点:record ↔ ORM 的 JSON 封包/解包)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_row(record: TaskArtifactRecord) -> TaskArtifactModel:
        """record → ORM 行;``record.id`` 忽略(自增);JSON 列就地 dumps。"""
        return TaskArtifactModel(
            task_id=record.task_id,
            node_id=record.node_id,
            artifact_id=record.artifact_id,
            artifact_kind=str(record.artifact_kind),
            content_kind=str(record.content_kind),
            content=json.dumps(dict(record.content), ensure_ascii=False),
            attempt=int(record.attempt or 0),
            content_hash=record.content_hash,
            supersedes=record.supersedes,
            derived_from=(
                json.dumps(list(record.derived_from), ensure_ascii=False)
                if record.derived_from is not None else None
            ),
            created_by=record.created_by,
            created_at=int(record.created_at or 0),
        )

    def _get_existing(
        self, db, record: TaskArtifactRecord
    ) -> Optional[TaskArtifactRecord]:
        """按 dedupe 键回读已存行;无 → None(数据竞争窄窗外则不该发生)。"""
        row = (
            db.query(self._model)
            .filter(
                self._model.task_id == record.task_id,
                self._model.node_id == record.node_id,
                self._model.attempt == int(record.attempt or 0),
                self._model.content_hash == record.content_hash,
            )
            .one_or_none()
        )
        return row.to_record() if row is not None else None

    # ------------------------------------------------------------------
    # create_or_get(幂等发布;失败原样上抛)
    # ------------------------------------------------------------------

    def create_or_get(self, record: TaskArtifactRecord) -> TaskArtifactRecord:
        row = self._to_row(record)
        with self._db.orm_session() as db:
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                # 幂等闸:撞 dedupe 唯一键 → 回读已存行原样返回。
                # 撞其它唯一键(artifact_id 重复等,_get_existing 回读不到)
                # → 原样上抛,不吞(spec:持久化失败必须向上返回错误)。
                db.rollback()
                existing = self._get_existing(db, record)
                if existing is None:
                    raise
                logger.info(
                    "[task][artifact] dedupe 命中(返回已存行)"
                    " task=%s node=%s attempt=%s hash=%s",
                    record.task_id, record.node_id, record.attempt,
                    record.content_hash,
                )
                return existing
            db.refresh(row)
            return row.to_record()

    def get_by_artifact_id(self, artifact_id: str) -> Optional[TaskArtifactRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.artifact_id == artifact_id)
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    # ------------------------------------------------------------------
    # 查询(排序契约:attempt DESC, created_at DESC, id DESC)
    # ------------------------------------------------------------------

    def list_by_node(
        self,
        task_id: str,
        node_id: str,
        *,
        attempt: Optional[int] = None,
    ) -> list[TaskArtifactRecord]:
        with self._db.orm_session() as db:
            query = db.query(self._model).filter(
                self._model.task_id == task_id,
                self._model.node_id == node_id,
            )
            if attempt is not None:
                query = query.filter(self._model.attempt == int(attempt))
            rows = query.order_by(
                self._model.attempt.desc(),
                self._model.created_at.desc(),
                self._model.id.desc(),
            ).all()
            return [row.to_record() for row in rows]

    def list_by_task(self, task_id: str) -> list[TaskArtifactRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .order_by(
                    self._model.attempt.desc(),
                    self._model.created_at.desc(),
                    self._model.id.desc(),
                )
                .all()
            )
            return [row.to_record() for row in rows]

    def latest_for_node(
        self,
        task_id: str,
        node_id: str,
        *,
        kind: Optional[str] = None,
    ) -> Optional[TaskArtifactRecord]:
        with self._db.orm_session() as db:
            query = db.query(self._model).filter(
                self._model.task_id == task_id,
                self._model.node_id == node_id,
            )
            if kind is not None:
                query = query.filter(self._model.artifact_kind == str(kind))
            row = query.order_by(
                self._model.attempt.desc(),
                self._model.created_at.desc(),
                self._model.id.desc(),
            ).first()
            return row.to_record() if row is not None else None