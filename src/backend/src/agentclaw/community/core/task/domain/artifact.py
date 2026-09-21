"""任务产物(Artifact)领域对象 — 不可变产物清单(manifest)。

权威源:语雀《BCN 产物(Artifact)领域对象设计》(``mad/enxdbg/gfap3r7tgc2aeptr``,2026-09-17)。
本模块为 shared kernel:纯 dataclass/enum,零依赖(不 import transport/框架),与 ``models.py`` 同规。

核心不变量(文档 §2/§9/§14,评审不得突破):
- Artifact 是不可变产物清单:管理业务身份、作用域、内容描述、血缘与审计;不管理存储。
- 文件字节不进入 Artifact(不含 output dict/消息/Prompt);File 分支只持有稳定 ``file_id``
  (本仓库 = SessionResource ``resource_id``,``sr_*``),不保存临时下载 URL、Token、
  对象存储 Key 或后端 handle。
- ``artifact_kind`` 表达业务用途(分析报告/图表/数据集),``media_type`` 表达技术格式
  (text/markdown、application/pdf),两者不得混用(文档 §6)。
- 内容四分支互斥(Text/Structured/File/Collection),由 ``content_from_dict`` 以
  ``kind`` snake_case 判别键解析;非法组合抛 ``ArtifactContentError``(文档 §4.2)。
- 发布后不可变:内容变化创建新 ``artifact_id`` 并以 ``supersedes`` 指向被替代产物,
  旧产物保留用于审计/历史回放;不存在原地覆盖(文档 §7)。

仓库特化(文档允许 Contract 评审时按仓库规范调整):
- ``ArtifactScope`` 增设 ``task_id``:本仓库 ``node_id`` 由 LLM 规划/yaml 供给,跨任务
  不唯一,持久化与查询以 ``(task_id, node_id)`` 为主维度。
- ``run_id`` 取框架图实例号(``TaskExecutionGraph.run_id``);executor 侧 ``ws_*`` 运行号
  已由 ``run_info.extend_props`` 承载,不重复入 scope。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from agentclaw.community.core.task.domain.errors import (
    ArtifactContentError,
    ArtifactError,
)


# ===== 枚举 =====
class ArtifactKind(StrEnum):
    """产物业务用途(文档 §6:与 media_type 分离)。

    封闭枚举 + ``OTHER`` 逃生口:新业务类型经此处单点扩展,避免开放字符串产生脏数据
    (文档待决策 #3 的落地选择)。
    """

    ANALYSIS_REPORT = "analysis_report"  # 分析报告
    SUMMARY = "summary"                  # 总结/结论文本
    SOURCE_CODE = "source_code"          # 源码/代码包
    CHART = "chart"                      # 图表
    DATASET = "dataset"                   # 数据集
    OTHER = "other"                      # 其它(逃生口)


class DigestAlgorithm(StrEnum):
    """内容摘要算法(文档 §4.5:算法必须显式标注,不留语义不明的 checksum)。"""

    SHA256 = "sha256"


class ActorType(StrEnum):
    """产物创建者类型。bot 的 ``actor_id`` 采用 ``compose_bot_identity`` 的
    ``bot_id:owner_id`` 复合身份(见 ``identity.py``;由调用方组装,本模块不依赖)。"""

    BOT = "bot"
    USER = "user"
    SYSTEM = "system"


# ===== 值对象 =====
@dataclass(frozen=True)
class ContentDigest:
    """内容摘要(canonical bytes 的完整性信息)。"""

    algorithm: DigestAlgorithm
    value: str

    def to_dict(self) -> dict[str, Any]:
        return {"algorithm": self.algorithm.value, "value": self.value}


@dataclass(frozen=True)
class SchemaRef:
    """受治理、可版本化 Schema 的引用(仅在 Structured 分支出现;文档 §4.6)。"""

    schema_id: str
    schema_version: str

    def to_dict(self) -> dict[str, Any]:
        return {"schema_id": self.schema_id, "schema_version": self.schema_version}


@dataclass(frozen=True)
class ActorRef:
    """产物创建者引用(文档 §5 示例的 ``{actor_type, actor_id}``)。"""

    actor_type: ActorType
    actor_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"actor_type": self.actor_type.value, "actor_id": self.actor_id}


@dataclass(frozen=True)
class ArtifactScope:
    """产物作用域:谁在什么执行上下文中产生了该产物(文档 §4.3)。

    ``attempt`` 显式保存(harness 重试快照,同源 ``extend_props["harness_retries"]``),
    避免节点重试后丢失每次执行对应的产物记录。
    """

    task_id: str
    node_id: str
    session_id: str | None = None
    run_id: str | None = None
    attempt: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "attempt": self.attempt,
        }


@dataclass(frozen=True)
class ArtifactLineage:
    """产物血缘(文档 §4.4):不建模成任意 JSON,保持可查询/可校验/可审计。

    当前产物由哪些产物派生(``derived_from``)、依赖哪些输入消息
    (``source_message_ids``);归属的 run/node/attempt 由 ``scope`` 表达。
    """

    derived_from: tuple[str, ...] = ()
    source_message_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "derived_from": list(self.derived_from),
            "source_message_ids": list(self.source_message_ids),
        }


# ===== 内容:互斥 tagged union(文档 §4.2)=====
@dataclass(frozen=True)
class ArtifactContent(ABC):
    """产物内容基类:四分支互斥,``kind`` 为 wire 判别键(snake_case)。

    不使用顶层 ``payload: Option<_>`` / ``storage_ref: Option<_>`` 平铺可空字段;
    分支内部结构由 ``content_from_dict`` 集中校验(domain 无 ``__post_init__``)。
    """

    kind: ClassVar[str]

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """序列化为带 ``kind`` 判别键的 dict(与文档 §5 JSON 示例对齐)。"""


@dataclass(frozen=True)
class TextContent(ArtifactContent):
    """文本产物(节点产物现状:统一文本/Markdown 输出)。"""

    kind: ClassVar[str] = "text"
    text: str
    media_type: str = "text/plain"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "text", "text": self.text, "media_type": self.media_type}


@dataclass(frozen=True)
class StructuredContent(ArtifactContent):
    """结构化数据产物(带 Schema 引用的 JSON 值)。"""

    kind: ClassVar[str] = "structured"
    value: Any
    media_type: str = "application/json"
    schema_ref: SchemaRef | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": "structured",
            "value": self.value,
            "media_type": self.media_type,
        }
        if self.schema_ref is not None:
            d["schema_ref"] = self.schema_ref.to_dict()
        return d


@dataclass(frozen=True)
class FileContent(ArtifactContent):
    """文件产物:持有稳定 ``file_id``(SessionResource ``resource_id``),不持有
    临时 URL/Token/object_handle(文档 §5.2:下载地址在鉴权后动态生成)。"""

    kind: ClassVar[str] = "file"
    file_id: str
    file_name: str
    media_type: str
    size_bytes: int
    digest: ContentDigest | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": "file",
            "file_id": self.file_id,
            "file_name": self.file_name,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
        }
        if self.digest is not None:
            d["digest"] = self.digest.to_dict()
        return d


@dataclass(frozen=True)
class CollectionContent(ArtifactContent):
    """产物集合(集合本身具有业务意义时才使用;不能以 Collection 替代节点的
    普通多产物输出 — 多产物由 ``output_artifact_ids`` 列表承载,文档 §8)。"""

    kind: ClassVar[str] = "collection"
    items: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "collection", "items": list(self.items)}


def content_to_dict(content: ArtifactContent) -> dict[str, Any]:
    """``ArtifactContent`` → 带 ``kind`` 判别键的 dict。"""
    return content.to_dict()


def content_from_dict(data: Any) -> ArtifactContent:
    """dict → ``ArtifactContent``(判别键 ``kind``;四分支互斥,非法组合抛错)。

    校验集中在此处(分支必填字段存在且类型正确、结构化值为合法 JSON 值、
    空判别键/未知 kind/非 dict 一律拒绝),是文档"避免非法字段组合"的执行点。
    """
    if not isinstance(data, dict):
        raise ArtifactContentError(f"content 必须是 dict,实际: {type(data).__name__}")
    kind = data.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ArtifactContentError(f"content 缺少非空判别键 kind: {data!r}")

    if kind == TextContent.kind:
        text = _require_str(data, "text", kind)
        return TextContent(
            text=text,
            media_type=_require_str(data, "media_type", kind, default="text/plain"),
        )

    if kind == StructuredContent.kind:
        value = data.get("value")
        if not isinstance(value, (dict, list)):
            raise ArtifactContentError(f"{kind}: value 必须是 JSON 对象/数组,实际: {type(value).__name__}")
        return StructuredContent(
            value=value,
            media_type=_require_str(data, "media_type", kind, default="application/json"),
            schema_ref=_schema_ref_from(data.get("schema_ref"), kind),
        )

    if kind == FileContent.kind:
        return FileContent(
            file_id=_require_str(data, "file_id", kind),
            file_name=_require_str(data, "file_name", kind),
            media_type=_require_str(data, "media_type", kind),
            size_bytes=_size_bytes_from(data.get("size_bytes"), kind),
            digest=_digest_from(data.get("digest"), kind),
        )

    if kind == CollectionContent.kind:
        return CollectionContent(items=_id_list_from(data.get("items", []), kind, "items"))

    raise ArtifactContentError(f"未知 content kind: {kind!r}")


# ===== 聚合根 =====
@dataclass(frozen=True)
class Artifact:
    """任务产物聚合根 — 不可变产物清单(文档 §4.1)。

    发布后不可变:无 update 方法;内容变化创建新实例并经 ``supersedes`` 关联。
    ``created_by`` 的 bot 身份由调用方以 ``compose_bot_identity`` 预组装为
    ``actor_id``(本模块不 import identity.py,保持零依赖纯度)。
    """

    artifact_id: str
    artifact_kind: ArtifactKind
    scope: ArtifactScope
    content: ArtifactContent
    created_by: ActorRef
    created_at: int
    lineage: ArtifactLineage = field(default_factory=ArtifactLineage)
    supersedes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为稳定领域数据(不含任何临时访问凭证;文档 §5)。"""
        return {
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind.value,
            "scope": self.scope.to_dict(),
            "content": content_to_dict(self.content),
            "lineage": self.lineage.to_dict(),
            "supersedes": self.supersedes,
            "created_by": self.created_by.to_dict(),
            "created_at": self.created_at,
        }


def artifact_from_dict(data: Any) -> Artifact:
    """dict → ``Artifact``;content 部分经 ``content_from_dict`` 校验,
    枚举/scope/lineage 畸形时抛 ``ArtifactError``。"""
    if not isinstance(data, dict):
        raise ArtifactError(f"artifact 必须是 dict,实际: {type(data).__name__}")

    try:
        artifact_kind = ArtifactKind(data["artifact_kind"])
    except (KeyError, ValueError) as exc:
        raise ArtifactError(f"未知/缺失 artifact_kind: {data.get('artifact_kind')!r}") from exc

    return Artifact(
        artifact_id=_require_str(data, "artifact_id", "artifact"),
        artifact_kind=artifact_kind,
        scope=_scope_from_dict(data.get("scope")),
        content=content_from_dict(data.get("content")),
        created_by=_actor_from_dict(data.get("created_by")),
        created_at=_created_at_from(data.get("created_at")),
        lineage=_lineage_from_dict(data.get("lineage")),
        supersedes=data.get("supersedes") or None,
    )


# ===== 内部解析辅助 =====
def _require_str(
    data: dict[str, Any],
    key: str,
    ctx: str,
    *,
    default: str | None = None,
) -> str:
    value = data.get(key)
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ArtifactContentError(f"{ctx}: {key!r} 必须是非空字符串,实际: {value!r}")
    return value


def _size_bytes_from(value: Any, kind: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ArtifactContentError(f"{kind}: size_bytes 必须是非负整数,实际: {value!r}")
    return value


def _digest_from(value: Any, kind: str) -> ContentDigest | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ArtifactContentError(f"{kind}: digest 必须是 {{algorithm, value}},实际: {value!r}")
    try:
        algorithm = DigestAlgorithm(value["algorithm"])
    except (KeyError, ValueError) as exc:
        raise ArtifactContentError(f"{kind}: 未知摘要算法: {value.get('algorithm')!r}") from exc
    digest_value = value.get("value")
    if not isinstance(digest_value, str) or not digest_value:
        raise ArtifactContentError(f"{kind}: digest.value 必须是非空字符串")
    return ContentDigest(algorithm=algorithm, value=digest_value)


def _schema_ref_from(value: Any, kind: str) -> SchemaRef | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ArtifactContentError(f"{kind}: schema_ref 必须是 {{schema_id, schema_version}},实际: {value!r}")
    schema_id = value.get("schema_id")
    schema_version = value.get("schema_version")
    if not isinstance(schema_id, str) or not isinstance(schema_version, str) or not schema_id:
        raise ArtifactContentError(f"{kind}: schema_ref 字段非法: {value!r}")
    return SchemaRef(schema_id=schema_id, schema_version=schema_version)


def _id_list_from(value: Any, ctx: str, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ArtifactContentError(f"{ctx}: {key} 必须是非空字符串列表,实际: {value!r}")
    return tuple(value)


def _scope_from_dict(value: Any) -> ArtifactScope:
    if not isinstance(value, dict):
        raise ArtifactError(f"scope 必须是 dict,实际: {value!r}")
    task_id = value.get("task_id")
    node_id = value.get("node_id")
    if not isinstance(task_id, str) or not task_id:
        raise ArtifactError(f"scope.task_id 必须是非空字符串,实际: {task_id!r}")
    if not isinstance(node_id, str) or not node_id:
        raise ArtifactError(f"scope.node_id 必须是非空字符串,实际: {node_id!r}")
    attempt = value.get("attempt") or 0
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        raise ArtifactError(f"scope.attempt 必须是非负整数,实际: {attempt!r}")
    session_id = value.get("session_id") or None
    run_id = value.get("run_id") or None
    if session_id is not None and not isinstance(session_id, str):
        raise ArtifactError(f"scope.session_id 必须是字符串,实际: {session_id!r}")
    if run_id is not None and not isinstance(run_id, str):
        raise ArtifactError(f"scope.run_id 必须是字符串,实际: {run_id!r}")
    return ArtifactScope(
        task_id=task_id,
        node_id=node_id,
        session_id=session_id,
        run_id=run_id,
        attempt=attempt,
    )


def _lineage_from_dict(value: Any) -> ArtifactLineage:
    if value is None:
        return ArtifactLineage()
    if not isinstance(value, dict):
        raise ArtifactError(f"lineage 必须是 dict,实际: {value!r}")
    derived = value.get("derived_from", [])
    message_ids = value.get("source_message_ids", [])
    for key, items in (("derived_from", derived), ("source_message_ids", message_ids)):
        if not isinstance(items, list) or any(not isinstance(v, str) or not v for v in items):
            raise ArtifactError(f"lineage.{key} 必须是非空字符串列表,实际: {items!r}")
    return ArtifactLineage(derived_from=tuple(derived), source_message_ids=tuple(message_ids))


def _actor_from_dict(value: Any) -> ActorRef:
    if not isinstance(value, dict):
        raise ArtifactError(f"created_by 必须是 {{actor_type, actor_id}},实际: {value!r}")
    try:
        actor_type = ActorType(value["actor_type"])
    except (KeyError, ValueError) as exc:
        raise ArtifactError(f"未知/缺失 actor_type: {value.get('actor_type')!r}") from exc
    actor_id = value.get("actor_id")
    if not isinstance(actor_id, str) or not actor_id:
        raise ArtifactError(f"created_by.actor_id 必须是非空字符串,实际: {actor_id!r}")
    return ActorRef(actor_type=actor_type, actor_id=actor_id)


def _created_at_from(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ArtifactError(f"created_at 必须是非负整数(epoch 毫秒),实际: {value!r}")
    return value