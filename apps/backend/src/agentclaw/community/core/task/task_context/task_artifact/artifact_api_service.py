"""产物手动登记 API 编排服务(POST/GET ``/api/v1/collaboration/tasks/artifacts``)。

spec 2026-10-20-task-artifact-domain 之外的**独立 API 写口**:relay 回调之外的
手动新增产物(人工补录 / 外部系统登记)+ 按任务查产物清单。编排语义与 relay
上报同源,不另造口径:

- 归属交叉校验(§4 规则 3/4/5):item 的 ``attempt`` 必须等于图上
  ``harness_retries`` 快照(拒绝而非采纳,幂等键不被误导);``session_id``
  图绑定优先、上报兜底;task/node 不存在即 404 域错误。
- 名称回落(§3.6)与行构造(§3.1)复用 ``artifact_publish`` 的模块级
  compose 辅助——capture 与手动登记两条写口单一事实源。
- 幂等(§5.3):``TaskArtifactRepositoryProtocol.append`` select-first 查重,
  与 ``save_graph`` 内嵌写同键同口径;重复登记返回既有行,不插第二行。

Repo pattern(api/README"Where a Protocol is defined"):Protocol 定义于本(core)
模块,实现继承之,``api/task/task_artifact_service.py`` 仅 re-export;
HTTP adapter Inject 的是 Protocol,不 import 具体类。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
    TaskGraphRepositoryProtocol,
)
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskArtifactContentError,
    TaskNotFoundError,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    compose_artifact_record,
    graph_node_attempt,
    graph_scope_session_id,
    resolve_artifact_name,
)
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
)
from agentclaw.community.core.task.repository.types import TaskArtifactRecord

if TYPE_CHECKING:
    from agentclaw.community.core.task.task_context.task_artifact.report_item import (
        ArtifactReportItem,
    )


@runtime_checkable
class TaskArtifactApiServiceProtocol(Protocol):
    """产物手动登记 API:新增一条产物 + 按任务列产物清单。"""

    def add_artifact(self, item: "ArtifactReportItem") -> TaskArtifactRecord:
        """登记一条已解析产物(relay 同构 item;归属/attempt 与图交叉校验)。

        失败上抛域错误:``TaskNotFoundError``/``NodeNotFoundError``(404)、
        ``TaskArtifactContentError``(422);幂等命中返回既有行不重复落库。
        """
        ...

    def list_artifacts(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        limit: int = 200,
    ) -> "list[TaskArtifactRecord]":
        """列任务产物(attempt 倒序;node_id 指定时按节点过滤)。"""
        ...


class TaskArtifactApiService(TaskArtifactApiServiceProtocol):
    """编排实现:图读校验 + compose + 仓储 append;读侧直通读仓储。"""

    @inject
    def __init__(
        self,
        graph_repo: TaskGraphRepositoryProtocol,
        artifact_repo: TaskArtifactRepositoryProtocol,
    ) -> None:
        self._graph_repo = graph_repo
        self._artifact_repo = artifact_repo

    def add_artifact(self, item: "ArtifactReportItem") -> TaskArtifactRecord:
        graph = self._graph_repo.load_graph(item.task_id)
        if graph is None:
            raise TaskNotFoundError(item.task_id)
        node = next(
            (entry for entry in graph.tasks if entry.node_id == item.node_id), None
        )
        if node is None:
            raise NodeNotFoundError(item.node_id)
        # attempt 与图上快照交叉校验(§4 规则 4):不一致即拒,不采纳——
        # 防止手动登记把产物归档到不存在的执行轮次,污染幂等键。
        attempt = graph_node_attempt(node)
        if item.attempt != attempt:
            raise TaskArtifactContentError(
                f"artifacts item attempt={item.attempt} 与图上 harness_retries"
                f" 快照 attempt={attempt} 不一致 (node_id={item.node_id})"
            )
        # session_id:图绑定优先;图未绑定会话时采纳上报值(§4 规则 5)。
        session_id = graph_scope_session_id(graph)
        if session_id is None:
            session_id = item.session_id

        record = compose_artifact_record(
            task_id=node.task_id or graph.task_id,
            node_id=node.node_id,
            attempt=attempt,
            scope_session_id=session_id,
            kind=ArtifactKind(item.artifact_kind),
            content=item.content,
            artifact_name=resolve_artifact_name(item),
            creator=node.run_info.assignee or "system",
            now_ms=int(time.time() * 1000),
        )
        return self._artifact_repo.append(record)

    def list_artifacts(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        limit: int = 200,
    ) -> list[TaskArtifactRecord]:
        if node_id is None:
            return self._artifact_repo.list_by_task(task_id, limit=limit)
        return self._artifact_repo.list_by_task_node(task_id, node_id, limit=limit)
