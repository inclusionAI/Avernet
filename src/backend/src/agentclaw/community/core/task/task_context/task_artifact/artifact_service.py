"""Artifact 服务(阶段一双写 + 阶段二读侧 Descriptor 口径)。

spec:``2026-09-23-task-artifact-manifest``。调用方只有 TaskGraphService 的两个
fold fire 点(node/graph 级,mutation 持久化**成功之后**调用)—— 单点覆盖全部
7 类写源(callback/poller、bbs executor、relay EXECUTION_RESULT、内部直驱、
planner rollup、dispatcher、静态 mock),无需各写源各自接线。

职责边界:
* id 工厂(``new_artifact_id``)/ 内容寻址哈希 / 同 (task,node,attempt) 同 kind
  的 ``supersedes`` 链解析 / content-kind 分派 / File 引用扫描与 READY 闸;
* 幂等:仓 embedding 层 ``create_or_get`` 按 (task,node,attempt,content_hash)
  唯一键兜底(fire 时点已消除版本重放重复,这里是第二重闸);
* 失败策略:``relay_mode=False``(集中化)持久化异常上抛
  ``TaskArtifactPublishError``(spec"必须上抛");``relay_mode=True`` 降 WARNING
  (spec 偏离记录:relay successor immutable,上抛后的重试会被不可变保护拒绝,
  形成收口残局)。文件引用未 READY / 校验失败属**内容性拒绝**,恒 WARNING
  拒该候选、不吞整批(spec I6)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Protocol, runtime_checkable

from injector import inject

from agentclaw.community.core.repository.protocols.platform import (
    SessionResourceRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.domain.errors import (
    TaskArtifactContentError,
    TaskArtifactPublishError,
)
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    ArtifactScope,
    FileArtifactContent,
    TaskArtifact,
    TextArtifactContent,
    new_artifact_id,
)

logger = logging.getLogger("task.artifact.service")

#: 文件引用的稳定 resource_id 前缀(ac_session_resource 的 "sr_" 惯例)。
_RESOURCE_ID_PREFIX = "sr_"

#: 显式控制信号键 → INTERNAL_CONTROL 归类(dispatcher skipped/notify 归口
#: shape 启发式;其余一律 NODE_RESULT —— fold 单点拿不到原始写源,启发式只
#: 认完全确定的控制形态,误判宁可保守归 NODE_RESULT)。
_CONTROL_OUTPUT_KEYS = {"skipped", "notify_result"}


def content_hash(content_dict: dict[str, Any]) -> str:
    """内容寻址摘要 ``"sha256:<hex64>"``(canonical JSON;对齐仓库 digest 惯例)。"""
    payload = json.dumps(content_dict, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class TaskArtifactServiceProtocol(Protocol):
    """Artifact 服务口(写侧发布 + 读侧 Descriptor;spec §3/§4)。"""

    def publish_output(
        self,
        task_id: str,
        node_id: str,
        attempt: int,
        output: dict[str, Any],
        *,
        kind: ArtifactKind | None = None,
        created_by: str | None = None,
        relay_mode: bool = False,
    ) -> int:
        """把 fold 后的完全的产物发布为 Artifact 行(可能 1 条 Text + N 条 File)。

        返回成功发布的行数(含 dedupe 命中)。relay_mode 见模块 docstring。
        """
        ...

    def list_artifacts_for_task(self, task_id: str) -> list[TaskArtifact]:
        """读侧:整个任务的产物 manifest 列表(仓储行 → 领域对象,经分派红线)。

        spec §4(Descriptor):dashboard/trajectory 读时富化的数据源。
        """
        ...

    def primary_artifact_ids_by_node(self, task_id: str) -> dict[str, str]:
        """读侧:每 ``node_id`` → 该节点当前可见产物的 ``artifact_id``(is_primary 判定)。

        primary = ``latest_for_node``(``kind`` 不过滤);无产物的节点不进 dict。
        """
        ...


class TaskArtifactService(TaskArtifactServiceProtocol):
    """写侧唯一发布口径 + 读侧 Descriptor 数据源;按堆调用,无内部状态。"""

    @inject
    def __init__(
        self,
        repo: TaskArtifactRepositoryProtocol,
        session_resource_repo: SessionResourceRepositoryProtocol | None = None,
    ) -> None:
        self._repo = repo
        # File 就绪闸用的会话文件仓储(可选依赖:轻量 DI 未装 → None →
        # 文件引用一律拒绝发布 + WARNING——不猜就绪状态)。
        self._session_resources = session_resource_repo

    # ------------------------------------------------------------------
    # 发布主流程
    # ------------------------------------------------------------------

    def publish_output(
        self,
        task_id: str,
        node_id: str,
        attempt: int,
        output: dict[str, Any],
        *,
        kind: ArtifactKind | None = None,
        created_by: str | None = None,
        relay_mode: bool = False,
    ) -> int:
        if not isinstance(output, dict) or not output:
            return 0
        attempt = int(attempt or 0)
        resolved_kind = kind if kind is not None else self._derive_kind(output)
        created_at = int(time.time() * 1000)

        # ① File 引用(扫描 → READY 闸 → 收集 File 分支候选)
        file_refs = self._collect_file_refs(output)
        validated = [
            (ref, meta) for ref in file_refs
            if (meta := self._resolve_file_meta(ref, task_id, node_id)) is not None
        ]

        # ② Text 分支(全量 output snapshot;控制/业务/图级归并为一行)
        published = 0
        text = TextArtifactContent(text=self._snapshot_text(output))
        published += self._publish(
            task_id, node_id, attempt, resolved_kind, text,
            created_by=created_by, created_at=created_at, relay_mode=relay_mode,
        )

        # ③ File 分支逐条(同批多文件 = 多产物;kind 随 resolved_kind 标注)
        for ref, meta in validated:
            published += self._publish(
                task_id, node_id, attempt, resolved_kind, meta,
                created_by=created_by, created_at=created_at, relay_mode=relay_mode,
            )
        return published

    # ------------------------------------------------------------------
    # 读侧 Descriptor(spec §4;双轨并存期读侧数据源,不触碰写侧)
    # ------------------------------------------------------------------

    def list_artifacts_for_task(self, task_id: str) -> list[TaskArtifact]:
        """整个任务的产物 manifest 列表(仓储排序:attempt DESC, created_at DESC, id DESC)。

        ``record.to_artifact()`` 的分派红线:坏 kind/坏 content 抛
        ``TaskArtifactContentError`` —— 这里按行捕获 + WARNING 跳过该行(不吞其它
        行的可见性,也不静默进领域);上游 DAO 异常原样上抛(读侧 caller 自行降级)。
        """
        arts: list[TaskArtifact] = []
        for row in self._repo.list_by_task(task_id):
            try:
                arts.append(row.to_artifact())
            except TaskArtifactContentError as exc:  # noqa: PERF203 单行脏数据不吞整批
                logger.warning(
                    "[task][artifact] 跳过脏产物行 task=%s node=%s artifact=%s: %s",
                    task_id, getattr(row, "node_id", None),
                    getattr(row, "artifact_id", None), exc,
                )
        return arts

    def primary_artifact_ids_by_node(self, task_id: str) -> dict[str, str]:
        """每个 ``node_id`` → 该节点当前可见产物的 ``artifact_id``(spec §4 is_primary)。

        primary 数据源 = 仓储 ``latest_for_node``(``kind`` 不传不过滤);无产物的节点
        不进 dict(缺键 = 无信号)。仅在两读方法间按 node 去重,不缓存(读侧按需查询)。
        """
        primary: dict[str, str] = {}
        for row in self._repo.list_by_task(task_id):
            if row.node_id in primary:
                continue
            latest = self._repo.latest_for_node(task_id, row.node_id)
            if latest is not None:
                primary[row.node_id] = latest.artifact_id
        return primary

    # ------------------------------------------------------------------
    # 分派辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_kind(output: dict[str, Any]) -> ArtifactKind:
        keys = set(output)
        if keys <= _CONTROL_OUTPUT_KEYS and keys:
            return ArtifactKind.INTERNAL_CONTROL
        return ArtifactKind.NODE_RESULT

    @staticmethod
    def _snapshot_text(output: dict[str, Any]) -> str:
        """Text 全文不截断(spec);折叠 dict 序列化为 canonical JSON 文本。"""
        return json.dumps(output, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _collect_file_refs(output: dict[str, Any]) -> list[dict[str, Any]]:
        """扫 top-level 条目提取 File 引用形态的原始描述(dict)。

        认两种明确形态(协议见 prompt_formatter 文案):
        * 顶层 ``{"resource_id": "sr_...", ...}`` reference dict;
        * 顶层 ``resource_id`` 键的裸 "sr_" 字符串。
        不做深层递归 —— 万形兜底会引出猜测性识别;只认执行协议明确形态。
        """
        refs: list[dict[str, Any]] = []
        for key, value in output.items():
            candidate: dict[str, Any] | None = None
            if isinstance(value, dict):
                rid = value.get("resource_id")
                if isinstance(rid, str) and rid.startswith(_RESOURCE_ID_PREFIX):
                    candidate = dict(value)
            elif isinstance(value, str) and value.startswith(_RESOURCE_ID_PREFIX):
                if key == "resource_id":
                    candidate = {"resource_id": value}
            if candidate is not None and candidate not in refs:
                refs.append(candidate)
        return refs

    def _resolve_file_meta(
        self, ref: dict[str, Any], task_id: str, node_id: str
    ) -> FileArtifactContent | None:
        """READY 闸(spec I6):存在且 status==READY 才放行;否则 WARNING 拒候选。"""
        resource_id = str(ref.get("resource_id"))
        record = None
        if self._session_resources is not None:
            try:
                record = self._session_resources.get_by_resource_id(resource_id)
            except Exception as exc:  # noqa: BLE001 就绪探测失败按未就绪拒绝
                logger.warning(
                    "[task][artifact] 文件就绪探测失败 task=%s node=%s resource=%s: %s: %s",
                    task_id, node_id, resource_id, type(exc).__name__, exc,
                )
                return None
        if record is None or str(getattr(record, "status", "")) != "ready":
            logger.warning(
                "[task][artifact] 文件引用未就绪,拒绝发布为 File 产物"
                " task=%s node=%s resource=%s status=%s",
                task_id, node_id, resource_id,
                getattr(record, "status", None),
            )
            return None
        sha = ref.get("sha256") or ref.get("content_hash") or getattr(
            record, "client_content_hash", None)
        return FileArtifactContent(
            resource_id=resource_id,
            file_name=str(
                ref.get("file_name")
                or getattr(record, "filename", None)
                or getattr(record, "display_name", None)
                or resource_id
            ),
            media_type=str(ref.get("media_type") or "application/octet-stream"),
            size_bytes=(
                ref.get("size_bytes")
                if isinstance(ref.get("size_bytes"), int)
                else getattr(record, "size_bytes", None)
            ),
            sha256=str(sha) if sha else None,
        )

    # ------------------------------------------------------------------
    # 落库(supersedes 链 + 失败策略)
    # ------------------------------------------------------------------

    def _previous_for(
        self, task_id: str, node_id: str, attempt: int, kind: ArtifactKind,
    ) -> TaskArtifactRecord | None:
        """同 (task,node,attempt) 同 kind 的最新行(supersedes 解析依据)。"""
        try:
            rows = self._repo.list_by_node(task_id, node_id, attempt=attempt)
        except Exception as exc:  # noqa: BLE001 链解析属优化,失败链首行即可
            logger.warning(
                "[task][artifact] supersedes 链解析失败(链首处理)task=%s node=%s: %s",
                task_id, node_id, exc,
            )
            return None
        for row in rows:
            if row.artifact_kind == kind.value:
                return row
        return None

    def _publish(
        self,
        task_id: str,
        node_id: str,
        attempt: int,
        kind: ArtifactKind,
        content: TextArtifactContent | FileArtifactContent,
        *,
        created_by: str | None,
        created_at: int,
        relay_mode: bool,
    ) -> int:
        content_dict = TaskArtifact(
            artifact_id="", kind=kind,
            scope=ArtifactScope(task_id=task_id, node_id=node_id, attempt=attempt),
            content=content,
        ).content_to_dict()
        previous = self._previous_for(task_id, node_id, attempt, kind)
        supersedes = (
            previous.artifact_id
            if previous is not None and previous.content_kind == content_dict["kind"]
            and previous.content_hash != content_hash(content_dict)
            else None
        )
        record = TaskArtifactRecord(
            id=0,
            artifact_id=new_artifact_id(),
            task_id=task_id,
            node_id=node_id,
            attempt=attempt,
            artifact_kind=kind.value,
            content_kind=content_dict["kind"],
            content=content_dict,
            content_hash=content_hash(content_dict),
            supersedes=supersedes,
            derived_from=None,
            created_by=created_by,
            created_at=created_at,
        )
        try:
            self._repo.create_or_get(record)
        except Exception as exc:
            if relay_mode:
                logger.warning(
                    "[task][artifact] relay 模式产物双写失败(降级不阻断收口)"
                    " task=%s node=%s kind=%s: %s: %s",
                    task_id, node_id, kind.value, type(exc).__name__, exc,
                )
                return 0
            raise TaskArtifactPublishError(
                f"artifact publish failed task={task_id} node={node_id}"
                f" kind={kind.value}: {exc}"
            ) from exc
        return 1