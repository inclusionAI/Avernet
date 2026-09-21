"""ArtifactService — 任务产物应用服务(阶段一契约,语雀《BCN 产物领域对象设计》§12)。

职责:组装不可变 ``Artifact`` 并发布到 append-only 仓储、执行文件就绪不变量、
维护 supersedes 派生链与血缘解析。核心设计:

- ``artifact_id`` 确定性派生(sha1 over scope+kind+canonical content+supersedes,
  先例 ``task_runner/callback_adapter.py`` 的 ``_derive_event_id``):同内容回投重放
  天然幂等(唯一键冲突 → 返回既有行),内容变化自然派生新 id 承载 supersedes 语义。
  注意:极少数"同 scope 同内容但语义上是两个产物"的发布会合并为一行 —— 内容相同即
  信息等价,可接受(文档 §8 多产物经 ``output_artifact_ids`` 列表表达,kind/scope
  通常不同,碰撞概率实际可忽略)。
- 发布后不可变:仓储无 update 成员;``publish`` 只插入旧行之上的新行。
- 枚举完整性:``artifact_kind`` 为封闭 ``ArtifactKind``;``to_artifact`` 投影经
  ``artifact_from_dict`` 唯一解析收口。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Optional

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.domain.artifact import (
    ActorRef,
    Artifact,
    ArtifactContent,
    ArtifactKind,
    ArtifactLineage,
    ArtifactScope,
    CollectionContent,
    ContentDigest,
    FileContent,
    SchemaRef,
    StructuredContent,
    TextContent,
    content_to_dict,
)
from agentclaw.community.core.task.domain.errors import (
    ArtifactFileNotReadyError,
    ArtifactReferenceError,
)
from agentclaw.community.core.task.repository.types import ArtifactRecord
from agentclaw.community.core.task.task_artifact.ports import SessionFileReadinessPort


def derive_artifact_id(
    *,
    scope: ArtifactScope,
    artifact_kind: ArtifactKind,
    content: ArtifactContent,
    supersedes: Optional[str] = None,
) -> str:
    """确定性产物 ID:``art_{sha1(scope|kind|canonical content|supersedes)[:32]}``。"""
    digest_src = json.dumps(
        {
            "scope": scope.to_dict(),
            "artifact_kind": artifact_kind.value,
            "content": content_to_dict(content),
            "supersedes": supersedes,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "art_" + hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:32]


class ArtifactService:
    """产物发布/查询应用服务 — repository + 就绪校验 seam,均由组合根注入。"""

    def __init__(
        self,
        *,
        repository: TaskArtifactRepositoryProtocol,
        readiness: Optional[SessionFileReadinessPort] = None,
        clock: Optional[Callable[[], int]] = None,
    ) -> None:
        self._repository = repository
        self._readiness = readiness
        self._clock = clock or (lambda: int(time.time() * 1000))

    # ===== 发布(四分支统一入口 + 便捷构造)=====
    def publish(
        self,
        *,
        scope: ArtifactScope,
        artifact_kind: ArtifactKind,
        content: ArtifactContent,
        created_by: ActorRef,
        lineage: Optional[ArtifactLineage] = None,
        supersedes: Optional[str] = None,
    ) -> Artifact:
        """发布一个不可变产物并落库。

        规则(文档 §7/§9):
        - File 分支先执行就绪不变量:``file_id`` 非 READY(或就绪校验器未装配)→
          fail-closed 抛 ``ArtifactFileNotReadyError``;
        - Collection 分支要求全部 item 已存在(否则 ``ArtifactReferenceError``);
        - ``supersedes`` 必须指向已存在的产物;
        - 幂等:确定性 ``artifact_id`` 撞唯一键(同 scope+kind+内容+supersedes 重放)
          → 返回既有行,不报错。
        """
        if supersedes is not None and self._repository.get(supersedes) is None:
            raise ArtifactReferenceError(f"supersedes 指向的产物不存在: {supersedes!r}")
        if content.kind == "collection":
            self._ensure_collection_items_exist(content)  # type: ignore[arg-type]
        if content.kind == "file":
            # 文档 §9 不变量:只有 READY 的会话文件才能发布为正式 Artifact。
            # 阶段一无 File 生产方,校验能力未装配时 fail-closed(测试用替身注入)。
            file_id = content.to_dict()["file_id"]
            if self._readiness is None or not self._readiness.is_ready(file_id):
                raise ArtifactFileNotReadyError(
                    f"file_id={file_id!r} 未就绪(非 READY 或就绪校验器未装配),拒绝发布"
                )
        return self._insert(
            scope=scope,
            artifact_kind=artifact_kind,
            content=content,
            created_by=created_by,
            lineage=lineage,
            supersedes=supersedes,
        )

    def publish_text(
        self,
        *,
        scope: ArtifactScope,
        text: str,
        created_by: ActorRef,
        artifact_kind: ArtifactKind = ArtifactKind.SUMMARY,
        media_type: str = "text/markdown",
        lineage: Optional[ArtifactLineage] = None,
        supersedes: Optional[str] = None,
    ) -> Artifact:
        return self.publish(
            scope=scope,
            artifact_kind=artifact_kind,
            content=TextContent(text=text, media_type=media_type),
            created_by=created_by,
            lineage=lineage,
            supersedes=supersedes,
        )

    def publish_structured(
        self,
        *,
        scope: ArtifactScope,
        value: Any,
        created_by: ActorRef,
        artifact_kind: ArtifactKind,
        schema_ref: Optional[SchemaRef] = None,
        lineage: Optional[ArtifactLineage] = None,
        supersedes: Optional[str] = None,
    ) -> Artifact:
        return self.publish(
            scope=scope,
            artifact_kind=artifact_kind,
            content=StructuredContent(value=value, schema_ref=schema_ref),
            created_by=created_by,
            lineage=lineage,
            supersedes=supersedes,
        )

    def publish_file(
        self,
        *,
        scope: ArtifactScope,
        file_id: str,
        file_name: str,
        media_type: str,
        size_bytes: int,
        created_by: ActorRef,
        artifact_kind: ArtifactKind = ArtifactKind.OTHER,
        digest: Optional[ContentDigest] = None,
        lineage: Optional[ArtifactLineage] = None,
        supersedes: Optional[str] = None,
    ) -> Artifact:
        return self.publish(
            scope=scope,
            artifact_kind=artifact_kind,
            content=FileContent(
                file_id=file_id,
                file_name=file_name,
                media_type=media_type,
                size_bytes=size_bytes,
                digest=digest,
            ),
            created_by=created_by,
            lineage=lineage,
            supersedes=supersedes,
        )

    def publish_collection(
        self,
        *,
        scope: ArtifactScope,
        items: list[str],
        created_by: ActorRef,
        artifact_kind: ArtifactKind = ArtifactKind.OTHER,
        lineage: Optional[ArtifactLineage] = None,
        supersedes: Optional[str] = None,
    ) -> Artifact:
        return self.publish(
            scope=scope,
            artifact_kind=artifact_kind,
            content=CollectionContent(items=tuple(items)),
            created_by=created_by,
            lineage=lineage,
            supersedes=supersedes,
        )

    # ===== 内容变更:新 id + supersedes(旧产物保留审计,不删不改)=====
    def supersede(
        self,
        artifact_id: str,
        *,
        content: ArtifactContent,
        artifact_kind: Optional[ArtifactKind] = None,
        created_by: Optional[ActorRef] = None,
        lineage: Optional[ArtifactLineage] = None,
    ) -> Artifact:
        old = self.get(artifact_id)
        return self.publish(
            scope=old.scope,
            artifact_kind=artifact_kind or old.artifact_kind,
            content=content,
            created_by=created_by or old.created_by,
            lineage=lineage,
            supersedes=old.artifact_id,
        )

    # ===== 查询/血缘 =====
    def get(self, artifact_id: str) -> Artifact:
        artifact = self.get_or_none(artifact_id)
        if artifact is None:
            raise ArtifactReferenceError(f"Artifact 不存在: {artifact_id!r}")
        return artifact

    def get_or_none(self, artifact_id: str) -> Optional[Artifact]:
        record = self._repository.get(artifact_id)
        return record.to_artifact() if record else None

    def list_by_task(self, task_id: str) -> list[Artifact]:
        return [r.to_artifact() for r in self._repository.list_by_task(task_id)]

    def list_by_node(
        self, task_id: str, node_id: str, *, attempt: Optional[int] = None
    ) -> list[Artifact]:
        return [
            r.to_artifact()
            for r in self._repository.list_by_node(task_id, node_id, attempt=attempt)
        ]

    def latest_by_node(
        self, task_id: str, node_id: str, *, attempt: Optional[int] = None
    ) -> Optional[Artifact]:
        record = self._repository.latest_by_node(task_id, node_id, attempt=attempt)
        return record.to_artifact() if record else None

    def resolve_lineage(self, artifact_id: str) -> dict[str, list[Artifact]]:
        """血缘解析(文档 §4.4 审计链):

        - ``chain``:沿 ``supersedes`` 反向遍历到链头,``[本产物, 前一代, ...]``;
        - ``derived_from``:``lineage.derived_from`` 各 id 的既有产物(缺失项跳过,
          阶段一不强制跨任务存在性)。
        断链(指向 id 不存在)时截断停在该代 —— ``publish`` 的存在性校验保证正常
        路径不会断链。
        """
        chain: list[Artifact] = []
        seen: set[str] = set()
        cursor: Optional[Artifact] = self.get_or_none(artifact_id)
        while cursor is not None and cursor.artifact_id not in seen:
            seen.add(cursor.artifact_id)
            chain.append(cursor)
            cursor = self.get_or_none(cursor.supersedes) if cursor.supersedes else None
        derived = [
            artifact
            for ref_id in (chain[0].lineage.derived_from if chain else ())
            if (artifact := self.get_or_none(ref_id)) is not None
        ]
        return {"chain": chain, "derived_from": derived}

    # ===== 内部 =====
    def _insert(
        self,
        *,
        scope: ArtifactScope,
        artifact_kind: ArtifactKind,
        content: ArtifactContent,
        created_by: ActorRef,
        lineage: Optional[ArtifactLineage],
        supersedes: Optional[str],
    ) -> Artifact:
        artifact_id = derive_artifact_id(
            scope=scope,
            artifact_kind=artifact_kind,
            content=content,
            supersedes=supersedes,
        )
        record = ArtifactRecord(
            id=0,
            artifact_id=artifact_id,
            task_id=scope.task_id,
            node_id=scope.node_id,
            session_id=scope.session_id,
            run_id=scope.run_id,
            attempt=scope.attempt,
            artifact_kind=artifact_kind.value,
            content=content_to_dict(content),
            lineage=lineage.to_dict() if lineage else {},
            supersedes=supersedes,
            created_by=created_by.to_dict(),
            created_at=self._clock(),
        )
        try:
            stored = self._repository.insert(record)
        except IntegrityError:
            # 同内容重放 → 唯一键冲突:幂等返回既有行(不视为错误)。
            existing = self._repository.get(artifact_id)
            if existing is None:
                raise
            stored = existing
        return stored.to_artifact()

    def _ensure_collection_items_exist(
        self, content: CollectionContent
    ) -> None:
        for item_id in content.items:
            if self._repository.get(item_id) is None:
                raise ArtifactReferenceError(f"collection item 不存在: {item_id!r}")


__all__ = ["ArtifactService", "derive_artifact_id"]