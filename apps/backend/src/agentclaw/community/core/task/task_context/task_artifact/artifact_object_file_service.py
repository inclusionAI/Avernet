"""OBJECT_FILE 产物的后端代理上传/下载编排服务。

spec 2026-10-20-task-artifact-domain §1.3 的 2026-10-26 修订:OBJECT_FILE 介质的
字节经后端流式代理进出对象存储(``TaskArtifactObjectStoragePlugin`` seam,task 模块
自有 ``task_artifact_storage`` 配置),BOT_WORKSPACE/SESSION_FILE 两既有介质维持
原"不搬字节"裁决与既有读口。

编排语义与手动登记(``artifact_api_service.add_artifact``)同源,不另造口径:

- 归属交叉校验(§4 规则 3/4):task/node 不存在即 404 域错误;``attempt`` 必须等于图上
  ``harness_retries`` 快照(拒绝而非采纳,幂等键不被误导)。
- **命名寻址 key**:{``key_prefix``}/{task_id}/{node_id}/{file_name} —— 按任务/节点/
  文件名落位,``file_name`` 取 basename 并剥路径/控制字符防注入。同 (task,node,文件名)
  重传:OSS 同 key 后传覆盖前次字节(跨 attempt 不隔离,最新为准),行侧仍 append 一行新
  产物记录(§5.3 select-first dedupe 改为按 (storage_id → 同 FileContent) 收敛)。
- **单向失败安全**:``put_object`` 失败(显式 False)时**不登记产品行**;行写失败至多遗留
  一个无引用对象,决不出现"行在、字节无"的引用悬空。
- 下载逐次校验:按 artifact_id 查行 → 校验 key 落在本任务前缀域内 → 取字节;FOUND/NOT_FOUND/
  FAILED 三态映射 200/404/502。任何临时凭证不落产排行(§1.4 不变:key 是稳定引用)。

Repo pattern:Protocol 定义于本(core)模块,``api/task/task_artifact_object_file_service.py``
仅 re-export;HTTP adapter Inject 的是 Protocol。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
    TaskGraphRepositoryProtocol,
)
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskArtifactContentError,
    TaskArtifactObjectNotFoundError,
    TaskArtifactStorageError,
    TaskArtifactStorageNotConfiguredError,
    TaskArtifactUploadTooLargeError,
    TaskStateError,
    TaskNotFoundError,
)
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    compose_artifact_record,
    graph_node_attempt,
    graph_scope_session_id,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage_config import (
    TaskArtifactStorageConfig,
)
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    FileContent,
    StorageType,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectReadStatus,
    TaskArtifactObjectStoragePlugin,
)
from agentclaw.community.core.task.task_context.relay_attempt import (
    verify_relay_attempt,
)

logger = logging.getLogger("task.artifact")

_DEFAULT_FILE_TYPE = "application/octet-stream"
_DEFAULT_FILE_NAME = "artifact.bin"
_EXT_FILE_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".json": "application/json",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".zip": "application/zip",
}


@dataclass(frozen=True)
class TaskArtifactObjectDownload:
    """下载口结果:产物行(取 Content-Disposition 文件名/类型)+ 对象字节。"""

    record: TaskArtifactRecord
    payload: bytes


@runtime_checkable
class TaskArtifactObjectFileServiceProtocol(Protocol):
    """OBJECT_FILE 产物的上传与下载编排。"""

    def upload_object_file(
        self,
        *,
        task_id: str,
        node_id: str,
        attempt: int,
        file_name: str,
        payload: bytes,
        creator: str | None = None,
        artifact_name: str | None = None,
        content_type: str | None = None,
        relay_token: str | None = None,
    ) -> TaskArtifactRecord:
        """一步原子上传:字节先落对象存储,成功后登记产排行(幂等)。

        凭证二选一(spec §14/§14.2):``relay_token``(relay 尝试;creator 取节点
        assignee,入参 creator 被忽略)或 ``creator``(用户登录态)。两者皆空 →
        ``TaskArtifactContentError``。

        失败方向安全:put 失败→无行;遗留物至多是无引用对象。上抛域错误:
        ``TaskNotFoundError``/``NodeNotFoundError``(404)、``TaskStateError``(409,
        token 失配/无认领)、``TaskArtifactContentError``(422)、
        ``TaskArtifactStorageError``(502)、``TaskArtifactStorageNotConfiguredError``
        (503)、``TaskArtifactUploadTooLargeError``(413)。
        """
        ...

    def read_object_file(self, artifact_id: str) -> TaskArtifactObjectDownload:
        """按 artifact_id 取产物行并读回字节(逐次校验,无缓存凭证)。

        上抛域错误:``TaskArtifactObjectNotFoundError``(404,行未知/对象 NOT_FOUND/
        非本介质)、``TaskArtifactStorageError``(502)、
        ``TaskArtifactStorageNotConfiguredError``(503)。
        """
        ...


class TaskArtifactObjectFileService(TaskArtifactObjectFileServiceProtocol):
    """编排实现:图读校验 + key 派生 + 插件 put/get + 仓储 append/get。"""

    def __init__(
        self,
        graph_repo: TaskGraphRepositoryProtocol,
        artifact_repo: TaskArtifactRepositoryProtocol,
        object_storage: TaskArtifactObjectStoragePlugin | None,
        config: TaskArtifactStorageConfig,
    ) -> None:
        self._graph_repo = graph_repo
        self._artifact_repo = artifact_repo
        self._object_storage = object_storage
        self._config = config

    # ------------------------------------------------------------ upload

    def upload_object_file(
        self,
        *,
        task_id: str,
        node_id: str,
        attempt: int,
        file_name: str,
        payload: bytes,
        creator: str | None = None,
        artifact_name: str | None = None,
        content_type: str | None = None,
        relay_token: str | None = None,
    ) -> TaskArtifactRecord:
        if self._object_storage is None:
            raise TaskArtifactStorageNotConfiguredError(
                "task_artifact object storage 未绑定 (task_artifact_storage block)"
            )
        if not isinstance(payload, (bytes, bytearray)):
            raise TaskArtifactContentError("上传 payload 须为字节")
        payload = bytes(payload)
        max_bytes = self._config.max_upload_mb * 1024 * 1024
        if len(payload) > max_bytes:
            raise TaskArtifactUploadTooLargeError(
                f"上传 {len(payload)} 字节超过上限 {self._config.max_upload_mb}MB"
            )

        graph = self._graph_repo.load_graph(task_id)
        if graph is None:
            raise TaskNotFoundError(task_id)
        node = next(
            (entry for entry in graph.tasks if entry.node_id == node_id), None
        )
        if node is None:
            raise NodeNotFoundError(node_id)
        # 凭证二选一(§14.2/§14.5 校验序:token fence → assignee → attempt 快照)。
        if relay_token is not None:
            verify_relay_attempt(node, relay_token)
            if not (node.run_info.assignee or None):
                raise TaskStateError(
                    f"relay upload requires claimed assignee: "
                    f"task_id={task_id}, node_id={node_id}"
                )
            creator = node.run_info.assignee
        elif creator is None:
            raise TaskArtifactContentError(
                "上传须提供 creator(用户身份)或 relay_token(relay 尝试)之一"
            )
        graph_attempt = graph_node_attempt(node)
        if attempt != graph_attempt:
            raise TaskArtifactContentError(
                f"上传 attempt={attempt} 与图上 harness_retries 快照 "
                f"attempt={graph_attempt} 不一致 (node_id={node_id})"
            )

        key = self._derive_key(task_id, node_id, file_name)
        content = FileContent(
            storage_type=StorageType.OBJECT_FILE,
            storage_id=key,
            file_type=self._resolve_file_type(file_name, content_type),
            file_name=file_name,
            file_id=None,
        )

        if not self._object_storage.put_object(key, payload):
            # 单向失败安全:字节未落存储,绝不登记产物行。
            raise TaskArtifactStorageError(
                f"对象存储写入失败,产物未登记 task_id={task_id} key={key}"
            )

        record = compose_artifact_record(
            task_id=node.task_id or graph.task_id,
            node_id=node.node_id,
            attempt=graph_attempt,
            scope_session_id=graph_scope_session_id(graph),
            kind=ArtifactKind.FILE,
            content=content,
            artifact_name=artifact_name or file_name,
            creator=creator,
            now_ms=int(time.time() * 1000),
        )
        try:
            return self._artifact_repo.append(record)
        except Exception:
            # 行写失败:遗留一个无引用对象(可被留存清理回收),不上抛假成功。
            logger.exception(
                "[task-artifact] object-file row write failed after successful "
                "object put (orphan key=%s task_id=%s)",
                key,
                task_id,
            )
            raise

    # ----------------------------------------------------------- download

    def read_object_file(self, artifact_id: str) -> TaskArtifactObjectDownload:
        if self._object_storage is None:
            raise TaskArtifactStorageNotConfiguredError(
                "task_artifact object storage 未绑定 (task_artifact_storage block)"
            )
        record = self._artifact_repo.get_by_artifact_id(artifact_id)
        if record is None:
            raise TaskArtifactObjectNotFoundError(artifact_id)
        content = record.content
        if (
            record.kind != ArtifactKind.FILE.value
            or not isinstance(content, FileContent)
            or content.storage_type is not StorageType.OBJECT_FILE
        ):
            raise TaskArtifactContentError(
                f"artifact {artifact_id} 非 object_file 产物,本读口不承载其字节"
            )
        # 前缀域校验:key 必须落在本部署前缀下、且归属该行 task_id 的域内——
        # 防止任何手写行把下载口挪去读本任务之外的对象。
        expected_prefix = f"{self._config.key_prefix}/{record.task_id}/"
        if not content.storage_id.startswith(expected_prefix):
            raise TaskArtifactContentError(
                f"artifact {artifact_id} 的 key 不在本任务前缀域内: {content.storage_id!r}"
            )

        read = self._object_storage.get_object(content.storage_id)
        if read.status is TaskArtifactObjectReadStatus.NOT_FOUND:
            raise TaskArtifactObjectNotFoundError(
                f"artifact {artifact_id} 对象缺失 key={content.storage_id}"
            )
        if read.status is not TaskArtifactObjectReadStatus.FOUND or read.content is None:
            raise TaskArtifactStorageError(
                f"对象存储读取失败 key={content.storage_id}"
            )
        return TaskArtifactObjectDownload(record=record, payload=read.content)

    # ------------------------------------------------------------- helpers

    def _derive_key(self, task_id: str, node_id: str, file_name: str) -> str:
        """命名寻址 key:{prefix}/{task_id}/{node_id}/{file_name}。

        按任务+节点+文件名落位,同 (task,node,文件名) 重传覆盖前次字节(跨 attempt
        不隔离,最新 attempt 为准)。``file_name`` 经 ``_sanitize_file_name`` 取
        basename、剥路径/控制字符防 key 注入;最终结构与前缀域归属校验分别由
        ``_validate_object_key`` 与下载侧 ``expected_prefix`` 兜底。
        """
        return (
            f"{self._config.key_prefix}/{task_id}/{node_id}/"
            f"{self._sanitize_file_name(file_name)}"
        )

    @staticmethod
    def _sanitize_file_name(file_name: str) -> str:
        """取末段 basename(剥任一侧斜杠目录)、去控制字符与首尾空白;空则回落缺省名。

        ``file_name`` 来自 HTTP 上传的 ``file.filename``,完全用户可控,故必取 basename
        以防 ``../``、``sub/x`` 之类把 key 挪出预期段位;结构兜底仍在 _validate_object_key。
        """
        name = file_name.replace("\\", "/").rsplit("/", 1)[-1]
        name = "".join(ch for ch in name if ord(ch) >= 0x20).strip()
        return name or _DEFAULT_FILE_NAME

    @staticmethod
    def _extension_of(file_name: str) -> str:
        lowered = file_name.rsplit(".", 1)
        if len(lowered) == 2 and lowered[1] and len(lowered[1]) <= 8:
            ext = "." + lowered[1].lower()
            # 只保留字母数字,防 key 注入(模型层还有结构化校验兜底)。
            safe = "".join(c for c in ext if c.isalnum() or c == ".")
            return safe
        return ""

    @staticmethod
    def _resolve_file_type(file_name: str, content_type: str | None) -> str:
        if isinstance(content_type, str) and content_type.strip():
            return content_type.strip()
        ext = TaskArtifactObjectFileService._extension_of(file_name)
        return _EXT_FILE_TYPES.get(ext, _DEFAULT_FILE_TYPE)