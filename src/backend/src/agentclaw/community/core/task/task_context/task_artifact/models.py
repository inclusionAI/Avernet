"""任务产物 Artifact 领域模型(产物 manifest / tagged-union 内容)。

纯 dataclass / enum 内存态领域对象 —— **transport/DB-agnostic**:不依赖
SQLAlchemy / FastAPI / 任何外部框架。``created_at`` 沿用 task 模块 ``int`` 毫秒
时间戳约定(对齐 ``NodeActionEvent.ts`` / ``RuntimeInfo.start_time``),storage 层
负责 ``timestamp`` ↔ ``int`` 转换(样板:task_trajectory/time_utils)。

设计不变量(权威源 ``src/backend/specs/2026-09-23-task-artifact-manifest/spec.md``):

1. **Artifact = 不可变产物清单(manifest)**:承载业务身份(``kind``)、作用域
   (``scope``)、内容、血缘(``lineage``)与审计(``created_by``/``created_at``);
   不承载文件字节、临时 URL、Token、对象存储 key / handle。内容变化 → 创建**新**
   artifact 并经 ``lineage.supersedes`` 指向旧行;旧行永不动(**不可变语义,
   repository 不提供主字段 update**)。
2. **``content`` 是 kind-tagged 互斥分支**(``TextArtifactContent`` /
   ``FileArtifactContent``),持久化表示为带 ``"kind"`` 键的 dict —— 拒绝
   ``payload + storage_ref`` 双可空的平铺万能结构。``from_content_dict`` 遇未知
   ``kind`` 抛 ``TaskArtifactContentError``(脏值不得静默进领域)。
   ``Structured`` / ``Collection`` 分支按 spec M2 后置,本文件不建分支。
3. **双轴类型分离**:``kind``(ArtifactKind,业务用途)与 ``media_type``
   (MIME 技术格式)严格正交 —— 同一 ``analysis_report`` 可以是 ``text/markdown``
   也可以是 ``application/pdf``。``ArtifactKind`` 为受治理闭合 StrEnum(对齐
   ``ReasonCatalog`` / ``TrajectoryActionType`` 先例),新增成员必须改本枚举 + spec。
4. **版本语义分治**:``scope.attempt`` = 节点执行次数(harness_retries 口径,
   对齐 task_context_service 的 NodeActionEvent.attempt 映射);内容修订 =
   ``lineage.supersedes`` 链;**单一 version 字段不做**。
5. **File 分支引用稳定 resource_id**("sr_" 前缀,``ac_session_resource``),
   绝不引用对象存储 key / 临时下载 URL / 后端 handle;发布前须经
   SessionFile lifecycle 校验(status == READY),校验属 ArtifactService
   (artifact_service.py),领域层不持有文件仓储。

``artifact_id`` 由服务层工厂生成(``f"art_{uuid4().hex}"``,对齐 session
resources 的 ``sr_`` 先例),不藏进 default_factory —— 测试需要可注入的确定性。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskArtifactContentError


class ArtifactContentKind(StrEnum):
    """产物内容技术分支(kind-tagged union 的 ``kind`` 锚)。

    MVP 仅 ``TEXT`` / ``FILE`` 两分支;``STRUCTURED`` / ``COLLECTION`` 按设计稿
    后置(spec M2 清单),**新分支 = 新枚举成员 + from/to_dict 分派**,不在既有
    分支上加可空字段。
    """

    TEXT = "text"
    FILE = "file"


class ArtifactKind(StrEnum):
    """产物的业务用途轴(与 ``media_type`` MIME 技术轴严格正交)。

    受治理闭合枚举:开放字符串会让 content 分派失去完备性保证(库中脏值直驱
    进领域),对齐 ``ReasonCatalog`` / ``TrajectoryActionType`` 的治理先例;新增
    用途必须扩本枚举并同步 spec。
    """

    NODE_RESULT = "node_result"           # 节点业务产出(output fold 的主 catch-all)
    GRAPH_ROLLUP = "graph_rollup"         # 图级聚合(planner 根终验收口 / relay 投影)
    INTERNAL_CONTROL = "internal_control"  # 控制信号(dispatcher skipped/notify、静态 mock 等)


@dataclass
class TextArtifactContent:
    """文本内容分支:``text`` 存**全文,不截断**(截断只发生在读投影/DTO)。"""

    text: str
    media_type: str = "text/plain"


@dataclass
class FileArtifactContent:
    """文件内容分支:引用稳定 ``resource_id``(``sr_`` 前缀,ac_session_resource)。

    ``size_bytes`` / ``sha256`` 为校验性元数据(发布时从 SessionFile record 拷贝,
    可空);``sha256`` 采用仓库内容寻址先例 ``"sha256:<hex64>"`` 形态
    (对齐 ``ac_manifest_content.digest``)。**绝不携带下载 URL / Token / 对象
    存储 key** —— 访问能力由调用方按需经既有会话文件链路动态换取。
    """

    resource_id: str
    file_name: str
    media_type: str = "application/octet-stream"
    size_bytes: int | None = None
    sha256: str | None = None


@dataclass
class ArtifactScope:
    """产物作用域:谁在什么执行上下文中产生了该产物。

    ``node_id``:产物归属节点(图级聚合产物挂根节点 id);``attempt``
    = 节点执行次数(``harness_retries`` 口径),显式保存 —— 节点重试产生**新
    attempt 系列**,不覆盖上一 attempt 的产物记录(重试不抵赖)。
    """

    task_id: str
    node_id: str
    attempt: int = 0


@dataclass
class ArtifactLineage:
    """产物血缘:派生关系 + 内容修订链。

    ``derived_from``:上游产物(如 PDF → 文本提取,spec M2);``supersedes``:
    同 (task, node, attempt) 内容演进时,新行指向被替代的上一行 id(旧行保留
    供审计回放,不下场删改)。
    """

    derived_from: list[str] = field(default_factory=list)
    supersedes: str | None = None


@dataclass
class TaskArtifact:
    """单条任务产物(不可变 manifest)。

    各字段不可变性由**约定与仓储约束**保证(dataclass 不冻结 —— repository
    record 投影需要一个可组装对象),但领域层不提供任何"改内容"的方法;
    修订 = 新 artifact_id + ``lineage.supersedes``。
    """

    artifact_id: str
    kind: ArtifactKind
    scope: ArtifactScope
    content: TextArtifactContent | FileArtifactContent
    lineage: ArtifactLineage = field(default_factory=ArtifactLineage)
    created_by: str | None = None          # bot_id / "system";可空 = 未知产出者
    created_at: int = 0                    # 事件毫秒时间戳(服务层填充,样本=DONE 产出)

    # ------------------------------------------------------------------
    # 序列化 —— kind-tagged content dict(不变量 2):持久层 content TEXT 列
    # 直接存这个 dict 的 JSON;读侧回来必须经同一函数,未知 kind 必须炸。
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": _enum_value(self.kind),
            "scope": {
                "task_id": self.scope.task_id,
                "node_id": self.scope.node_id,
                "attempt": int(self.scope.attempt or 0),
            },
            "content": self.content_to_dict(),
            "derived_from": list(self.lineage.derived_from),
            "supersedes": self.lineage.supersedes,
            "created_by": self.created_by,
            "created_at": int(self.created_at or 0),
        }

    def content_to_dict(self) -> dict[str, Any]:
        """content 的 kind-tagged dict 表示(持久列即此形态)。"""
        if isinstance(self.content, TextArtifactContent):
            return {
                "kind": ArtifactContentKind.TEXT.value,
                "text": self.content.text,
                "media_type": self.content.media_type,
            }
        return {
            "kind": ArtifactContentKind.FILE.value,
            "resource_id": self.content.resource_id,
            "file_name": self.content.file_name,
            "media_type": self.content.media_type,
            "size_bytes": self.content.size_bytes,
            "sha256": self.content.sha256,
        }


def from_content_dict(raw: Any) -> TextArtifactContent | FileArtifactContent:
    """kind-tagged content dict → 分支对象;未知 kind 抛 ``TaskArtifactContentError``。

    这是"只写不可变 manifest"读侧的红线:持久化形态从宽(历史行可晚于新分支
    发布),但**进领域必须严格分派** —— 未知 ``kind`` 意味着 schema 演进断档或
    数据损坏,静默吞掉会让下游把脏行当合法产物展示。
    """
    if not isinstance(raw, dict):
        raise TaskArtifactContentError(f"artifact content is not a dict: {type(raw).__name__}")
    kind = raw.get("kind")
    if kind == ArtifactContentKind.TEXT:
        return TextArtifactContent(
            text=str(raw.get("text") or ""),
            media_type=str(raw.get("media_type") or "text/plain"),
        )
    if kind == ArtifactContentKind.FILE:
        resource_id = raw.get("resource_id")
        if not isinstance(resource_id, str) or not resource_id:
            raise TaskArtifactContentError("file artifact missing resource_id")
        return FileArtifactContent(
            resource_id=resource_id,
            file_name=str(raw.get("file_name") or ""),
            media_type=str(raw.get("media_type") or "application/octet-stream"),
            size_bytes=raw.get("size_bytes"),
            sha256=raw.get("sha256"),
        )
    raise TaskArtifactContentError(f"unknown artifact content kind: {kind!r}")


def artifact_from_dict(raw: dict[str, Any]) -> TaskArtifact:
    """持久化 manifest dict → 领域对象(经同一校验红线)。"""
    kind_raw = raw.get("kind")
    try:
        kind = ArtifactKind(kind_raw)  # type: ignore[arg-type]
    except ValueError as exc:
        raise TaskArtifactContentError(f"unknown artifact kind: {kind_raw!r}") from exc
    scope_raw = raw.get("scope") or {}
    lineage_raw = raw.get("lineage") or {}
    derived_from = raw.get("derived_from", lineage_raw.get("derived_from")) or []
    supersedes = raw.get("supersedes", lineage_raw.get("supersedes"))
    return TaskArtifact(
        artifact_id=str(raw.get("artifact_id") or ""),
        kind=kind,
        scope=ArtifactScope(
            task_id=str(scope_raw.get("task_id") or ""),
            node_id=str(scope_raw.get("node_id") or ""),
            attempt=int(scope_raw.get("attempt") or 0),
        ),
        content=from_content_dict(raw.get("content")),
        lineage=ArtifactLineage(
            derived_from=[str(x) for x in derived_from],
            supersedes=str(supersedes) if supersedes else None,
        ),
        created_by=str(raw["created_by"]) if raw.get("created_by") else None,
        created_at=int(raw.get("created_at") or 0),
    )


def new_artifact_id() -> str:
    """artifact_id 工厂:``art_`` 前缀 + uuid4 hex(对齐 session resources 的 ``sr_`` 先例)。"""
    import uuid

    return f"art_{uuid.uuid4().hex}"


def _enum_value(value: Any) -> str:
    """枚举降值 helper(StrEnum 已可直接比 str,此函数只为持久化形态显式化)。"""
    return str(getattr(value, "value", value))


__all__ = [
    "ArtifactContentKind",
    "ArtifactKind",
    "ArtifactLineage",
    "ArtifactScope",
    "FileArtifactContent",
    "TextArtifactContent",
    "TaskArtifact",
    "artifact_from_dict",
    "from_content_dict",
    "new_artifact_id",
]