"""产物发布 capture 服务 + contextvar 暂存(spec 2026-10-20-task-artifact-domain §5.1/§5.2)。

复用 callback-audit 的暂存机制语义:发布动作发生在图变更闭包内部、fold 之后——
capture **构造并 stage** 记录,``_persist_locked`` 在 ``save_graph`` 提交前 ``drain``
并随图一并落库;版本冲突重放会重复暂存,由 select-first 查重吸收(§5.3)。
本模块纯构造 + contextvar,不碰任何仓储(事务不外泄,写行走 save_graph)。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from functools import lru_cache
from typing import TYPE_CHECKING

from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    ArtifactContent,
    TextContent,
    artifact_content_hash,
    artifact_text_value,
    canonical_json,
    new_artifact_id,
)

if TYPE_CHECKING:
    from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode
    from agentclaw.community.core.task.task_context.task_artifact.report_item import (
        ArtifactReportItem,
    )

logger = logging.getLogger("task.artifact")

_PENDING_PUBLISHED_ARTIFACTS: ContextVar[list | None] = ContextVar(
    "_PENDING_PUBLISHED_ARTIFACTS", default=None
)

_CONTROL_SIGNAL_KEYS = ("skipped", "notify_result")


def stage_published(records: list[TaskArtifactRecord]) -> None:
    """暂存待写产物行(闭包语义:失败/stale 写会连同闭包一起重放)。"""

    if not records:
        return
    pending = _PENDING_PUBLISHED_ARTIFACTS.get()
    if pending is None:
        pending = []
    pending.extend(records)
    _PENDING_PUBLISHED_ARTIFACTS.set(pending)


def drain_published() -> list[TaskArtifactRecord]:
    """取走并清空全部暂存记录;随 ``save_graph`` 的 ``published_artifacts`` 落库。"""

    pending = _PENDING_PUBLISHED_ARTIFACTS.get()
    _PENDING_PUBLISHED_ARTIFACTS.set(None)
    if not pending:
        return []
    logger.debug("[task-artifact] drain %d 条产物行随图提交", len(pending))
    return list(pending)


def discard_published(graph) -> None:
    """缺席态(内存-only,graph 仓储未接线):不发布,暂存随闭包丢弃并记 debug(§5.2)。"""

    discarded = drain_published()
    if discarded:
        logger.debug(
            "[task-artifact] graph repo 未接线,丢弃 %d 条产物暂存 (task=%s)",
            len(discarded),
            getattr(graph, "task_id", "?"),
        )


@lru_cache(maxsize=1)
def default_publish_service() -> TaskArtifactPublishService:
    """无状态发布服务的惰性取用器(模块级服务实例被架构守卫禁止;lr 惰性等同单例)。"""

    return TaskArtifactPublishService()


def capture_write_point(graph, node, *, patch, now_ms: int) -> None:
    """集中写点发布语义(spec §5.1):只有本次 mutation 真正 fold 了产物内容才发布。

    集中通道的产物内容全部有明确来源:文本正文/控制信号经 ``output_patch`` 到达,
    上报列表在 ``artifact_patch``;状态机翻转、harness 复位、派发等**零内容写不发布**。
    理由:中央通道 harness 复位不清 output(仅 relay 清),残留旧内容若照 capture
    会按新 attempt 误归档(内容造假)——且不止复位写本身,其后的 status-only 派发写
    同样不得重录(评审修正);控制信号写入点(dispatcher)也走 output_patch,不受影响。
    """

    artifact_patch = getattr(patch, "artifact_patch", None)
    if patch.output_patch is None and artifact_patch is None:
        return
    default_publish_service().capture_node(
        graph, node, artifact_patch=artifact_patch, now_ms=now_ms
    )


class TaskArtifactPublishService:
    """发布点 capture:文本双轨派生 + 报文 patch 逐条 → 构造记录并 stage。

    纯函数式(无依赖注入、无 IO);capture 发生在图变更闭包内 fold 之后,
    从提交前快照取值,保证版本重放安全。
    """

    def capture_node(
        self,
        graph: "TaskExecutionGraph",
        node: "TaskNode",
        *,
        artifact_patch: "list[ArtifactReportItem] | None",
        now_ms: int,
    ) -> list[TaskArtifactRecord]:
        task_id = node.task_id or graph.task_id
        node_id = node.node_id
        attempt = self._graph_attempt(node)
        session_id = self._graph_session_id(graph)
        creator = node.run_info.assignee or "system"

        records: list[TaskArtifactRecord] = []
        # 文本双轨:fold 后 output 派生,两类各至多 1 条(§5.1);
        # 与 relay 投影(``_relay_output_value``)是超集分叉,互不影响。
        text_value = artifact_text_value(node.run_info.output or {})
        if text_value is not None:
            records.append(
                self._record(
                    task_id=task_id,
                    node_id=node_id,
                    attempt=attempt,
                    scope_session_id=session_id,
                    kind=ArtifactKind.TEXT,
                    content=TextContent(text=text_value),
                    artifact_name=None,
                    creator=creator,
                    now_ms=now_ms,
                )
            )
        elif self._is_control_signal(node.run_info.output or {}):
            records.append(
                self._record(
                    task_id=task_id,
                    node_id=node_id,
                    attempt=attempt,
                    scope_session_id=session_id,
                    kind=ArtifactKind.TEXT,
                    content=TextContent(
                        text=canonical_json(node.run_info.output or {})
                    ),
                    artifact_name=None,
                    creator=creator,
                    now_ms=now_ms,
                )
            )

        # 报文 patch 逐条:attempt 与图上快照交叉校验,失配即拒整个回调(§4 规则 4)。
        for item in artifact_patch or []:
            if item.attempt != attempt:
                raise TaskArtifactContentError(
                    f"artifacts item attempt={item.attempt} 与图上 harness_retries"
                    f" 快照 attempt={attempt} 不一致 (node_id={node_id})"
                )
            records.append(
                self._record(
                    task_id=task_id,
                    node_id=node_id,
                    attempt=attempt,
                    # 图绑定优先;图未绑定会话时采纳上报值(§3.3)
                    scope_session_id=session_id if session_id is not None else item.session_id,
                    kind=ArtifactKind(item.artifact_kind),
                    content=item.content,
                    artifact_name=self._artifact_name(item),
                    creator=creator,
                    now_ms=now_ms,
                )
            )

        stage_published(records)
        return records

    @staticmethod
    def _graph_attempt(node: "TaskNode") -> int:
        value = (node.run_info.extend_props or {}).get("harness_retries", 0)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TaskArtifactContentError(
                f"harness_retries 快照须为 int,得到 {value!r} (node_id={node.node_id})"
            )
        return value

    @staticmethod
    def _graph_session_id(graph: "TaskExecutionGraph") -> str | None:
        if not graph.tasks:
            return None
        value = (graph.tasks[0].run_info.extend_props or {}).get("session_id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _is_control_signal(output: dict) -> bool:
        return any(key in output for key in _CONTROL_SIGNAL_KEYS)

    @staticmethod
    def _artifact_name(
        item: "ArtifactReportItem",
    ) -> str | None:
        """名称回落(§3.6):file → file_content.file_name;其余上报值 or None。"""

        if item.artifact_name is not None:
            return item.artifact_name
        if item.artifact_kind == ArtifactKind.FILE.value:
            return item.content.file_name
        return None

    def _record(
        self,
        *,
        task_id: str,
        node_id: str,
        attempt: int,
        scope_session_id: str | None,
        kind: ArtifactKind,
        content: ArtifactContent,
        artifact_name: str | None,
        creator: str,
        now_ms: int,
    ) -> TaskArtifactRecord:
        return TaskArtifactRecord(
            artifact_id=new_artifact_id(),
            artifact_name=artifact_name,
            task_id=task_id,
            node_id=node_id,
            attempt=attempt,
            scope_session_id=scope_session_id,
            kind=kind.value,
            storage=(
                content.storage_type.value if kind is ArtifactKind.FILE else "inline"
            ),
            content=content,
            content_hash=artifact_content_hash(content),
            creator=creator,
            modifier=None,
            created_at=now_ms,
            gmt_create=None,
        )