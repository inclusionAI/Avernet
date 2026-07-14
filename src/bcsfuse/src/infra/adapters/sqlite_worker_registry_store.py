"""
SQLite Worker Registry Store

Worker Registry 的 SQLite 存储实现。

Stage 1 Phase 2：用于本地开发和测试。
设计目标：支持 PostgreSQL 平滑迁移。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from src.domain.models.worker import (
    Worker,
    WorkerType,
    WorkerIdentity,
    WorkerState,
    Availability,
    TrustLevel,
    Capability,
    CapabilityLevel,
    SkillRef,
    ResourceRef,
)
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.models.worker_config import WorkerConfig
from src.domain.exceptions import DuplicateWorkerException, WorkerNotFoundException
from src.infra.adapters.sqlite_schema import init_schema


class SQLiteWorkerRegistryStore:
    """
    Worker Registry SQLite 存储

    Stage 1 Phase 2 实现：
    - SQLite 本地存储
    - 支持 :memory: 模式用于测试
    - 支持 version 乐观锁

    设计考虑：
    - JSON 字段存储复杂结构
    - 索引支持常用查询
    - 可迁移到 PostgreSQL
    """

    def __init__(self, db_path: str = ":memory:"):
        """
        初始化 SQLite 存储

        Args:
            db_path: 数据库路径，默认为内存模式
        """
        self._db_path = db_path
        # check_same_thread=False 允许在多线程环境中使用
        # timeout=30.0 允许等待30秒获取锁
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        # 启用 WAL 模式提高并发性能
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        init_schema(self._conn)

    def _row_to_worker(self, row: sqlite3.Row) -> Worker:
        """
        将数据库行转换为 Worker 模型

        Args:
            row: 数据库行

        Returns:
            Worker 模型
        """
        # 解析 identity
        identity = WorkerIdentity(
            name=row["identity_name"],
            handle=row["identity_handle"],
            description=row["identity_description"],
        )

        # 解析 capabilities
        capabilities_data = json.loads(row["capabilities"])
        capabilities = [
            Capability(
                name=c["name"],
                level=CapabilityLevel(c["level"]),
            )
            for c in capabilities_data
        ]

        # 解析 skills
        skills_data = json.loads(row["skills"]) if row["skills"] else []
        skills = [
            SkillRef(
                name=s["name"],
                source=s.get("source", "file"),
                description=s.get("description"),
                trust_level=s.get("trust_level", "trusted"),
                approval_required=s.get("approval_required", False),
                openclaw_skill_path=s.get("openclaw_skill_path"),
                tool_names=s.get("tool_names", []),
            )
            for s in skills_data
        ]

        # 解析 resources
        resources_data = json.loads(row["resources"]) if row["resources"] else []
        resources = [
            ResourceRef(
                id=r.get("id", f"res_{r['name']}"),
                kind=r.get("kind", "knowledge"),
                name=r["name"],
                description=r.get("description"),
                uri=r.get("uri"),
                access=r.get("access", "read"),
                owner=r.get("owner"),
                tags=r.get("tags", []),
            )
            for r in resources_data
        ]

        # 解析 state
        state = WorkerState(
            availability=Availability(row["state_availability"]),
            trust_level=TrustLevel(row["state_trust_level"]),
            runtime_state=WorkerRuntimeState(row["state_runtime_state"]),
        )

        # 解析 config
        config_data = row["config"]
        if config_data:
            config = WorkerConfig(**(json.loads(config_data) if isinstance(config_data, str) else config_data))
        else:
            config = WorkerConfig()

        # 创建 Worker
        return Worker(
            id=row["id"],
            type=WorkerType(row["type"]),
            identity=identity,
            responsibilities=json.loads(row["responsibilities"]),
            capabilities=capabilities,
            skills=skills,
            resources=resources,
            state=state,
            domains=json.loads(row["domains"]),
            lifecycle_state=WorkerLifecycleState(row["lifecycle_state"]),
            source_type=WorkerSourceType(row["source_type"]),
            source_ref=row["source_ref"],
            external_id=row["external_id"],
            active_profile_key=row["active_profile_key"],
            config=config,
            version=row["version"],
            created_at=datetime.fromisoformat(row["gmt_create"]),
            updated_at=datetime.fromisoformat(row["gmt_modify"]),
            created_by=row["created_by"],
            updated_by=row["updated_by"],
        )

    def _get_enum_value(self, value) -> str:
        """安全获取枚举值"""
        if hasattr(value, 'value'):
            return value.value
        return str(value)

    def _worker_to_dict(self, worker: Worker) -> dict:
        """
        将 Worker 模型转换为数据库字典

        Args:
            worker: Worker 模型

        Returns:
            数据库字典
        """
        return {
            "id": worker.id,
            "type": self._get_enum_value(worker.type),
            "identity_name": worker.identity.name,
            "identity_handle": worker.identity.handle,
            "identity_description": worker.identity.description,
            "responsibilities": json.dumps(worker.responsibilities),
            "capabilities": json.dumps([
                {"name": c.name, "level": self._get_enum_value(c.level)}
                for c in worker.capabilities
            ]),
            "skills": json.dumps([
                {
                    "name": s.name,
                    "source": self._get_enum_value(s.source),
                    "description": s.description,
                    "trust_level": self._get_enum_value(s.trust_level),
                    "approval_required": s.approval_required,
                    "openclaw_skill_path": s.openclaw_skill_path,
                    "tool_names": s.tool_names,
                }
                for s in worker.skills
            ]),
            "resources": json.dumps([
                {
                    "id": r.id,
                    "kind": self._get_enum_value(r.kind),
                    "name": r.name,
                    "description": r.description,
                    "uri": r.uri,
                    "access": self._get_enum_value(r.access),
                    "owner": r.owner,
                    "tags": r.tags,
                }
                for r in worker.resources
            ]),
            "state_availability": self._get_enum_value(worker.state.availability),
            "state_trust_level": self._get_enum_value(worker.state.trust_level),
            "state_runtime_state": self._get_enum_value(worker.state.runtime_state),
            "domains": json.dumps(worker.domains),
            "lifecycle_state": self._get_enum_value(worker.lifecycle_state),
            "source_type": self._get_enum_value(worker.source_type),
            "source_ref": worker.source_ref,
            "external_id": worker.external_id,
            "active_profile_key": worker.active_profile_key,
            "config": json.dumps(worker.config.model_dump()) if worker.config else None,
            "version": worker.version,
            "gmt_create": worker.created_at.isoformat() if worker.created_at else None,
            "gmt_modify": worker.updated_at.isoformat() if worker.updated_at else None,
            "created_by": worker.created_by,
            "updated_by": worker.updated_by,
        }

    def create(self, worker: Worker) -> Worker:
        """
        创建 Worker

        Args:
            worker: 待创建的 Worker

        Returns:
            创建后的 Worker

        Raises:
            DuplicateWorkerException: ID 已存在
        """
        if self.exists(worker.id):
            raise DuplicateWorkerException(worker.id)

        # 设置时间戳
        now = datetime.utcnow()
        worker.created_at = now
        worker.updated_at = now

        # 插入数据库
        data = self._worker_to_dict(worker)
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        sql = f"INSERT INTO bcsfuse_workers ({columns}) VALUES ({placeholders})"

        cursor = self._conn.cursor()
        cursor.execute(sql, list(data.values()))
        self._conn.commit()

        return worker.model_copy(deep=True)

    def get_by_id(self, worker_id: str) -> Optional[Worker]:
        """
        根据 ID 获取 Worker

        Args:
            worker_id: Worker ID

        Returns:
            Worker 或 None
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM bcsfuse_workers WHERE id = ?", (worker_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_worker(row)

    def get_by_ids(self, worker_ids: list[str]) -> dict[str, Worker]:
        """根据 ID 列表批量获取 Worker"""
        if not worker_ids:
            return {}
        placeholders = ",".join("?" for _ in worker_ids)
        cursor = self._conn.cursor()
        cursor.execute(
            f"SELECT * FROM bcsfuse_workers WHERE id IN ({placeholders})",
            worker_ids,
        )
        rows = cursor.fetchall()
        return {row["id"]: self._row_to_worker(row) for row in rows}

    def batch_get_configs(self, worker_ids: list[str]) -> tuple[dict[str, "WorkerConfig"], list[str]]:
        """批量获取 Worker config，仅查 id + config 列"""
        from src.domain.models.worker_config import WorkerConfig
        if not worker_ids:
            return {}, []
        placeholders = ",".join("?" for _ in worker_ids)
        cursor = self._conn.cursor()
        cursor.execute(
            f"SELECT id, config FROM bcsfuse_workers WHERE id IN ({placeholders})",
            worker_ids,
        )
        rows = cursor.fetchall()
        found_ids = set()
        configs: dict[str, "WorkerConfig"] = {}
        for row in rows:
            wid = row["id"]
            found_ids.add(wid)
            config_data = row["config"]
            if config_data:
                parsed = json.loads(config_data) if isinstance(config_data, str) else config_data
                configs[wid] = WorkerConfig(**parsed)
            else:
                configs[wid] = WorkerConfig()
        not_found = [wid for wid in worker_ids if wid not in found_ids]
        return configs, not_found

    def list(
        self,
        lifecycle_states: Optional[list[WorkerLifecycleState]] = None,
        source_types: Optional[list[WorkerSourceType]] = None,
        domains: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[Worker]:
        """
        列出 Worker（支持过滤）
        """
        sql = "SELECT * FROM bcsfuse_workers WHERE 1=1"
        params = []

        # 按生命周期状态过滤
        if lifecycle_states:
            placeholders = ", ".join(["?" for _ in lifecycle_states])
            sql += f" AND lifecycle_state IN ({placeholders})"
            params.extend([s.value for s in lifecycle_states])

        # 按来源类型过滤
        if source_types:
            placeholders = ", ".join(["?" for _ in source_types])
            sql += f" AND source_type IN ({placeholders})"
            params.extend([t.value for t in source_types])

        # 按领域过滤（OR 语义，使用 JSON 函数）
        # SQLite 不直接支持 JSON 数组查询，这里简化处理
        # 实际生产环境可以用 PostgreSQL 的 JSONB 查询

        # 排序
        sql += " ORDER BY gmt_create DESC"

        # 分页（LIMIT 应该在 OFFSET 之前）
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        if offset:
            sql += " OFFSET ?"
            params.append(offset)

        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()

        workers = [self._row_to_worker(row) for row in rows]

        # 内存中过滤 domains（SQLite 限制）
        if domains:
            workers = [
                w for w in workers
                if any(d in w.domains for d in domains)
            ]

        return workers

    def update(self, worker: Worker) -> Worker:
        """
        更新 Worker

        使用乐观锁（version 字段）
        """
        existing = self.get_by_id(worker.id)
        if existing is None:
            raise WorkerNotFoundException(worker.id)

        # 乐观锁检查
        if worker.version != existing.version:
            raise ValueError(
                f"Version conflict: expected {existing.version}, got {worker.version}"
            )

        # 创建副本，避免修改传入对象
        updated_worker = worker.model_copy(deep=True)

        # 更新时间和版本
        updated_worker.updated_at = datetime.utcnow()
        updated_worker.version = existing.version + 1

        # 更新数据库
        data = self._worker_to_dict(updated_worker)
        set_clause = ", ".join([f"{k} = ?" for k in data.keys() if k != "id"])
        sql = f"UPDATE bcsfuse_workers SET {set_clause} WHERE id = ?"
        params = [v for k, v in data.items() if k != "id"] + [updated_worker.id]

        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        self._conn.commit()

        return updated_worker.model_copy(deep=True)

    def update_lifecycle_state(
        self,
        worker_id: str,
        lifecycle_state: WorkerLifecycleState,
        version: int,
    ) -> Worker:
        """
        更新生命周期状态
        """
        worker = self.get_by_id(worker_id)
        if worker is None:
            raise WorkerNotFoundException(worker_id)

        # 乐观锁检查
        if worker.version != version:
            raise ValueError(
                f"Version conflict: expected {version}, got {worker.version}"
            )

        # 更新
        worker.lifecycle_state = lifecycle_state
        return self.update(worker)

    def update_trust_level(self, worker_id: str, trust_level: TrustLevel) -> Worker:
        """更新 Worker 信任级别。"""
        worker = self.get_by_id(worker_id)
        if worker is None:
            raise WorkerNotFoundException(worker_id)
        new_state = WorkerState(
            availability=worker.state.availability,
            trust_level=trust_level,
            current_load=worker.state.current_load,
            last_seen_at=worker.state.last_seen_at,
            runtime_state=worker.state.runtime_state,
            runtime_state_updated_at=worker.state.runtime_state_updated_at,
            runtime_state_updated_by=worker.state.runtime_state_updated_by,
        )
        worker.state = new_state
        return self.update(worker)

    def delete(self, worker_id: str) -> bool:
        """
        删除 Worker（完整级联删除）

        级联删除以下关联表数据：
        1. bcsfuse_worker_profile_contents - Profile 内容（向量删除由上层处理）
        2. bcsfuse_worker_runtime_states - 运行状态
        3. bcsfuse_worker_profile_bindings - Profile 绑定
        4. bcsfuse_worker_audit_logs - 审计日志
        5. bcsfuse_workers - Worker 主记录

        注意：向量删除由上层 ProfileEmbeddingStore 处理，以保持与存储后端一致的删除语义
        """
        if not self.exists(worker_id):
            raise WorkerNotFoundException(worker_id)

        cursor = self._conn.cursor()

        # 1. 删除 Profile 内容（向量删除由上层 ProfileEmbeddingStore 处理）
        cursor.execute(
            "DELETE FROM bcsfuse_worker_profile_contents WHERE worker_id = ?",
            (worker_id,)
        )
        deleted_profiles = cursor.rowcount

        # 2. 删除运行状态
        cursor.execute(
            "DELETE FROM bcsfuse_worker_runtime_states WHERE worker_id = ?",
            (worker_id,)
        )

        # 3. 删除 Profile 绑定
        cursor.execute(
            "DELETE FROM bcsfuse_worker_profile_bindings WHERE worker_id = ?",
            (worker_id,)
        )

        # 4. 删除审计日志
        cursor.execute(
            "DELETE FROM bcsfuse_worker_audit_logs WHERE worker_id = ?",
            (worker_id,)
        )

        # 5. 删除 Worker 主记录
        cursor.execute("DELETE FROM bcsfuse_workers WHERE id = ?", (worker_id,))

        self._conn.commit()

        logger.info(
            "[SQLite] Worker deleted: %s (profiles=%d)",
            worker_id, deleted_profiles
        )
        return True

    def exists(self, worker_id: str) -> bool:
        """
        检查 Worker 是否存在
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT 1 FROM bcsfuse_workers WHERE id = ?", (worker_id,))
        return cursor.fetchone() is not None

    def count(
        self,
        lifecycle_states: Optional[list[WorkerLifecycleState]] = None,
    ) -> int:
        """
        统计 Worker 数量
        """
        sql = "SELECT COUNT(*) FROM bcsfuse_workers WHERE 1=1"
        params = []

        if lifecycle_states:
            placeholders = ", ".join(["?" for _ in lifecycle_states])
            sql += f" AND lifecycle_state IN ({placeholders})"
            params.extend([s.value for s in lifecycle_states])

        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchone()[0]

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()


__all__ = ["SQLiteWorkerRegistryStore"]