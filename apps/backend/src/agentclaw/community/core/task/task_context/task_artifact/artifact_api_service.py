"""产物手动登记 API 编排服务及 dashboard session-file 协调。

spec 2026-10-20-task-artifact-domain 之外的**独立 API 写口**:relay 回调之外的
手动新增产物(人工补录 / 外部系统登记)+ 按任务查产物清单。编排语义与 relay
上报同源,不另造口径:

- 归属交叉校验(§4 规则 3/4/5):item 的 ``attempt`` 必须等于图上
  ``harness_retries`` 快照(拒绝而非采纳,幂等键不被误导);``session_id``
  图绑定优先、上报兜底;task/node 不存在即 404 域错误。
- 名称回落(§3.6)与行构造(§3.1)复用 ``artifact_publish`` 的模块级
  compose 辅助——capture、手动登记与 session-file 补录共用单一事实源。
- 幂等(§5.3):``TaskArtifactRepositoryProtocol.append`` select-first 查重,
  与 ``save_graph`` 内嵌写同键同口径;重复登记返回既有行,不插第二行。
- dashboard 返回 manifest 前先观察节点 BCS session 的 Ready files,按完整
  ``FileContent`` task-wide 去重并 append 新引用;BCS 失败降级,DB 失败上抛。

Repo pattern(api/README"Where a Protocol is defined"):Protocol 定义于本(core)
模块,实现继承之,``api/task/task_artifact_service.py`` 仅 re-export;
HTTP adapter Inject 的是 Protocol,不 import 具体类。
"""
from __future__ import annotations

import logging
import time

from agentclaw.community.utils.app_utils import get_current_executor_app
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
    TaskGraphRepositoryProtocol,
    TaskInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskArtifactContentError,
    TaskNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    compose_artifact_record,
    graph_node_attempt,
    graph_scope_session_id,
    resolve_artifact_name,
)
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    FileContent,
    StorageType,
)
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.ports import BcsSessionFilesPort
from agentclaw.community.core.task.task_context.relay_attempt import (
    verify_relay_attempt,
)

if TYPE_CHECKING:
    from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode
    from agentclaw.community.core.task.task_context.task_artifact.report_item import (
        ArtifactReportItem,
    )

logger = logging.getLogger("task.artifact")
_DEFAULT_FILE_TYPE = "application/octet-stream"
_SESSION_FILE_PAGE_SIZE = 100


@runtime_checkable
class TaskArtifactApiServiceProtocol(Protocol):
    """产物手动登记、按任务查询及 dashboard session-file 协调。"""

    def add_artifact(
        self, item: "ArtifactReportItem", *, relay_token: str | None = None
    ) -> TaskArtifactRecord:
        """登记一条已解析产物(relay 同构 item;归属/attempt 与图交叉校验)。

        ``relay_token``(spec §14.2)是可选的 relay 尝试 fence——非空时经
        core ``verify_relay_attempt`` 对图上节点代次校验;creator 派生与其余
        语义零改动。

        失败上抛域错误:``TaskNotFoundError``/``NodeNotFoundError``(404)、
        ``TaskStateError``(409,token 失配)、``TaskArtifactContentError``(422);
        幂等命中返回既有行不重复落库。
        """
        ...

    def list_artifacts(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        limit: int = 200,
    ) -> list[TaskArtifactRecord]:
        """列任务产物(attempt 倒序;node_id 指定时按节点过滤)。"""
        ...

    async def sync_session_files_and_list(
        self,
        graph: "TaskExecutionGraph",
        *,
        limit: int = 200,
    ) -> list[TaskArtifactRecord]:
        """Discover ready session files, persist new references, then list artifacts."""
        ...


class TaskArtifactApiService(TaskArtifactApiServiceProtocol):
    """编排实现:图读校验 + compose + 仓储 append;dashboard 协调后读 manifest。"""

    def __init__(
        self,
        graph_repo: TaskGraphRepositoryProtocol,
        artifact_repo: TaskArtifactRepositoryProtocol,
        session_files: BcsSessionFilesPort | None = None,
        bcs_bot_tokens: BcsBotTokenProvider | None = None,
        task_info_repo: TaskInfoRepositoryProtocol | None = None,
    ) -> None:
        self._graph_repo = graph_repo
        self._artifact_repo = artifact_repo
        self._session_files = session_files
        self._bcs_bot_tokens = bcs_bot_tokens
        self._task_info_repo = task_info_repo

    def _task_executor_app(self, task_id: str) -> str | None:
        """Return the durable task owner, or ``None`` when ownership is unknown.

        Lightweight test wiring can omit the repository; then historical behavior
        treats the current deployment as owner. Production DI always supplies this
        repository.
        """
        if self._task_info_repo is None:
            return None
        record = self._task_info_repo.get(task_id)
        return record.executor_app if record is not None else None

    def _require_owned_task(self, task_id: str) -> None:
        owner_app = self._task_executor_app(task_id)
        current_app = get_current_executor_app()
        if owner_app is not None and owner_app != current_app:
            raise TaskStateError(
                f"task executor app mismatch: task_id={task_id}, "
                f"task={owner_app}, current={current_app}"
            )

    def add_artifact(
        self, item: "ArtifactReportItem", *, relay_token: str | None = None
    ) -> TaskArtifactRecord:
        self._require_owned_task(item.task_id)
        graph = self._graph_repo.load_graph(item.task_id)
        if graph is None:
            raise TaskNotFoundError(item.task_id)
        node = next(
            (entry for entry in graph.tasks if entry.node_id == item.node_id), None
        )
        if node is None:
            raise NodeNotFoundError(item.node_id)
        # relay 尝试 fence(spec §14.2):非空 token 对图上节点代次校验;
        # creator 派生与其余归属逻辑零改动。
        if relay_token is not None:
            verify_relay_attempt(node, relay_token)
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

    async def sync_session_files_and_list(
        self,
        graph: "TaskExecutionGraph",
        *,
        limit: int = 200,
    ) -> list[TaskArtifactRecord]:
        """Backfill ready BCS session files before returning the task manifest.

        BCS/session-token lookup is observational and degrades per session. Artifact
        reads and writes are outside that failure boundary: persistence failures are
        propagated. A successful append triggers a fresh manifest read so callers see
        all artifacts currently stored by the repository.
        """
        owner_app = self._task_executor_app(graph.task_id)
        current_app = get_current_executor_app()
        if owner_app is not None and owner_app != current_app:
            # A viewer deployment must not write the owner's artifact manifest.
            # Return already-persisted artifacts only.
            return self.list_artifacts(graph.task_id, limit=limit)

        discovered = await self._discover_session_files(graph)

        # Required ordering: inspect every visible node session before the first
        # artifact-manifest read, then compare complete FileContent values task-wide.
        # Comparison must cover the complete task manifest, not just the dashboard's
        # response window; otherwise an older matching file outside ``limit`` would be
        # re-inserted under the current node/attempt.
        rows = self._artifact_repo.list_by_task(graph.task_id, limit=None)
        known_file_contents = {
            row.content
            for row in rows
            if row.kind == ArtifactKind.FILE.value
            and isinstance(row.content, FileContent)
        }

        wrote = False
        for node, session_id, content in discovered:
            if content in known_file_contents:
                continue
            record = compose_artifact_record(
                task_id=node.task_id or graph.task_id,
                node_id=node.node_id,
                attempt=graph_node_attempt(node),
                scope_session_id=session_id,
                kind=ArtifactKind.FILE,
                content=content,
                artifact_name=content.file_name,
                creator=node.run_info.assignee or "system",
                now_ms=int(time.time() * 1000),
            )
            self._artifact_repo.append(record)
            known_file_contents.add(content)
            wrote = True

        if wrote:
            return self._artifact_repo.list_by_task(graph.task_id, limit=limit)
        return rows[:limit]

    async def _discover_session_files(
        self, graph: "TaskExecutionGraph"
    ) -> list[tuple["TaskNode", str, FileContent]]:
        if self._session_files is None:
            return []

        discovered: list[tuple["TaskNode", str, FileContent]] = []
        inspected: set[tuple[str, str | None]] = set()
        for node in graph.tasks:
            session_id = (node.run_info.extend_props or {}).get("session_id")
            if not isinstance(session_id, str) or not session_id:
                continue
            if not isinstance(node.node_id, str) or not node.node_id:
                logger.warning(
                    "[task-artifact] session file skipped: node_id missing task=%s session=%s",
                    graph.task_id,
                    session_id,
                )
                continue

            holder = self._session_holder(node)
            lookup_key = (session_id, holder)
            if lookup_key in inspected:
                continue
            inspected.add(lookup_key)
            caller_bearer = self._holder_bearer_token(holder)
            try:
                items = await self._list_ready_session_files(
                    session_id, caller_bearer=caller_bearer
                )
            except Exception as exc:  # noqa: BLE001 external observation degrades
                logger.warning(
                    "[task-artifact] session file lookup failed; skip task=%s node=%s "
                    "session=%s error=%s: %s",
                    graph.task_id,
                    node.node_id,
                    session_id,
                    type(exc).__name__,
                    exc,
                )
                continue

            for item in items:
                content = self._session_file_content(session_id, item)
                if content is not None:
                    discovered.append((node, session_id, content))
        return discovered

    async def _list_ready_session_files(
        self, session_id: str, *, caller_bearer: str | None
    ) -> list[dict[str, object]]:
        """Read every Ready page while preserving the client's default first call.

        BCS defaults to 100 rows when ``limit``/``offset`` are omitted. Most sessions
        fit in that first request; a full page is the signal to continue explicitly.
        """
        if self._session_files is None:  # defensive; caller already guards this
            return []
        items = await self._session_files.list_files(
            session_id,
            status="Ready",
            caller_bearer=caller_bearer,
        )
        all_items: list[dict[str, object]] = list(items)
        offset = len(items)
        while len(items) == _SESSION_FILE_PAGE_SIZE:
            items = await self._session_files.list_files(
                session_id,
                status="Ready",
                limit=_SESSION_FILE_PAGE_SIZE,
                offset=offset,
                caller_bearer=caller_bearer,
            )
            all_items.extend(items)
            offset += len(items)
        return all_items

    @staticmethod
    def _session_holder(node: "TaskNode") -> str | None:
        props = node.run_info.extend_props or {}
        holder = props.get("driver_bot_id") or node.run_info.assignee
        return holder if isinstance(holder, str) and holder else None

    def _holder_bearer_token(self, holder: str | None) -> str | None:
        if holder is None or self._bcs_bot_tokens is None:
            return None
        try:
            return self._bcs_bot_tokens.get_token(holder) or None
        except Exception as exc:  # noqa: BLE001 identity lookup is observational
            logger.warning(
                "[task-artifact] BCS bot token lookup failed; using HMAC only "
                "holder=%s error=%s: %s",
                holder,
                type(exc).__name__,
                exc,
            )
            return None

    @staticmethod
    def _session_file_content(
        session_id: str, item: object
    ) -> FileContent | None:
        if not isinstance(item, dict):
            return None
        status = item.get("status")
        if status is not None and status != "Ready":
            return None
        file_id = item.get("file_id")
        file_name = item.get("file_name")
        if not isinstance(file_id, str) or not file_id:
            return None
        if not isinstance(file_name, str) or not file_name:
            return None
        mime_type = item.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type:
            mime_type = _DEFAULT_FILE_TYPE
        try:
            return FileContent(
                storage_type=StorageType.SESSION_FILE,
                storage_id=session_id,
                file_type=mime_type,
                file_name=file_name,
                file_id=file_id,
            )
        except TaskArtifactContentError as exc:
            logger.warning(
                "[task-artifact] invalid session file metadata skipped "
                "session=%s file_id=%s error=%s",
                session_id,
                file_id,
                exc,
            )
            return None
