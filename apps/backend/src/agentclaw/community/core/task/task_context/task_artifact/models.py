"""TaskArtifact 领域模型:kind-tagged 内容分支与不可变产物(spec 2026-10-20-task-artifact-domain §3)。

- 内容分支是 frozen dataclass,互斥校验在 ``__post_init__`` fail-loud(``TaskArtifactContentError``);
- 序列化形为 kind 前缀 canonical JSON(tag 由序列化器派发,领域模型不携带重复字段);
- ``storage`` 为派生一致性列:file 分支 = ``StorageType`` 值,其余恒 ``inline``。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskArtifactContentError

_MAX_URL_LENGTH = 2048
_MAX_TYPE_LENGTH = 64
_MAX_PR_DESC_LENGTH = 8 * 1024
_MAX_FILE_PATH_LENGTH = 512


class ArtifactKind(StrEnum):
    """内容形态,与 ``ArtifactContent`` 分支一一对应(governed 枚举,加值即引入新分支)。"""

    TEXT = "text"
    FILE = "file"
    URL_LINK = "url_link"
    CODE = "code"


class StorageType(StrEnum):
    """文件字节所在介质的寻址形态。"""

    BOT_WORKSPACE = "bot_workspace"
    SESSION_FILE = "session_file"


@dataclass(frozen=True)
class ArtifactScope:
    """产物归属:run 节点 + attempt(harness_retries 快照)+ 会话(图绑定优先)。"""

    session_id: str | None
    task_id: str
    node_id: str
    attempt: int


@dataclass(frozen=True)
class ArtifactContent:
    """内容分支基类:互斥表示的 tagged union,分支不带 kind 字段。"""


@dataclass(frozen=True)
class TextContent(ArtifactContent):
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TaskArtifactContentError(f"text 分支要求 str,得到 {type(self.text).__name__}")


@dataclass(frozen=True)
class FileContent(ArtifactContent):
    """文件引用:唯一存储寻址是 ``(storage_type, storage_id[, file_id])``,不携带任何字节凭证。"""

    storage_type: StorageType
    storage_id: str
    file_type: str
    file_name: str
    file_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.storage_type, StorageType):
            raise TaskArtifactContentError(f"storage_type 非法: {self.storage_type!r}")
        for name in ("storage_id", "file_type", "file_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise TaskArtifactContentError(f"file 分支字段 {name} 不得为空")
        if len(self.storage_id) > _MAX_FILE_PATH_LENGTH:
            raise TaskArtifactContentError(f"storage_id 超长(>{_MAX_FILE_PATH_LENGTH})")
        if len(self.file_name) > _MAX_FILE_PATH_LENGTH:
            raise TaskArtifactContentError(f"file_name 超长(>{_MAX_FILE_PATH_LENGTH})")
        if self.file_id is not None and (not isinstance(self.file_id, str) or len(self.file_id) > 128):
            raise TaskArtifactContentError("file_id 非空时须为不超过 128 字符的字符串")
        if self.storage_type is StorageType.BOT_WORKSPACE:
            if self.file_id is not None:
                raise TaskArtifactContentError(
                    "BOT_WORKSPACE 按工作区相对路径寻址,file_id 必须为 None"
                )
            _validate_workspace_relative_path(self.storage_id)
        else:
            if not self.file_id:
                raise TaskArtifactContentError("SESSION_FILE 必须提供 file_id")


def _validate_workspace_relative_path(path: str) -> None:
    if path.startswith("/") or path.startswith("~"):
        raise TaskArtifactContentError(f"工作区 storage_id 须为相对路径,拒绝: {path!r}")
    segments = path.replace("\\", "/").split("/")
    if any(segment == ".." for segment in segments):
        raise TaskArtifactContentError(f"工作区 storage_id 不允许 .. 逃逸: {path!r}")


@dataclass(frozen=True)
class UrlLinkContent(ArtifactContent):
    """稳定业务链接(需求条目/语雀文档)——链接是产物内容本身,非临时下载能力。"""

    url: str
    type: str

    def __post_init__(self) -> None:
        _validate_http_url(self.url, "url")
        if not isinstance(self.type, str) or not self.type.strip():
            raise TaskArtifactContentError("url_link 分支 type 不得为空")
        if len(self.type) > _MAX_TYPE_LENGTH:
            raise TaskArtifactContentError(f"type 超长(>{_MAX_TYPE_LENGTH})")


@dataclass(frozen=True)
class CodeContent(ArtifactContent):
    code_base_name: str
    code_base_address: str
    pr_link: str
    pr_desc: str

    def __post_init__(self) -> None:
        if not isinstance(self.code_base_name, str) or not self.code_base_name.strip():
            raise TaskArtifactContentError("code 分支 code_base_name 不得为空")
        _validate_http_url(self.code_base_address, "code_base_address")
        _validate_http_url(self.pr_link, "pr_link")
        if not isinstance(self.pr_desc, str):
            raise TaskArtifactContentError("pr_desc 须为 str(允许空串)")
        if len(self.pr_desc) > _MAX_PR_DESC_LENGTH:
            raise TaskArtifactContentError(f"pr_desc 超长(>{_MAX_PR_DESC_LENGTH})")


def _validate_http_url(url: str, field: str) -> None:
    if not isinstance(url, str) or not url.strip():
        raise TaskArtifactContentError(f"{field} 不得为空")
    if len(url) > _MAX_URL_LENGTH:
        raise TaskArtifactContentError(f"{field} 超长(>{_MAX_URL_LENGTH})")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise TaskArtifactContentError(f"{field} 必须是 http(s) 链接,得到: {url!r}")


_KIND_TO_CONTENT: dict[ArtifactKind, type[ArtifactContent]] = {
    ArtifactKind.TEXT: TextContent,
    ArtifactKind.FILE: FileContent,
    ArtifactKind.URL_LINK: UrlLinkContent,
    ArtifactKind.CODE: CodeContent,
}


@dataclass(frozen=True)
class TaskArtifact:
    """不可变产物(聚合根):发布后不提供任何原地修改入口。"""

    artifact_id: str
    artifact_name: str | None
    kind: ArtifactKind
    scope: ArtifactScope
    content: ArtifactContent
    content_hash: str
    creator: str
    created_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ArtifactKind):
            raise TaskArtifactContentError(f"kind 非法: {self.kind!r}")
        expected = _KIND_TO_CONTENT[self.kind]
        if type(self.content) is not expected:
            raise TaskArtifactContentError(
                f"kind {self.kind.value} 与内容分支 {type(self.content).__name__} 不一致"
            )
        if not isinstance(self.content_hash, str) or not self.content_hash:
            raise TaskArtifactContentError("content_hash 发布路径必填")
        if not isinstance(self.creator, str) or not self.creator:
            raise TaskArtifactContentError("creator 发布路径必填")
        if self.artifact_name is not None and not isinstance(self.artifact_name, str):
            raise TaskArtifactContentError("artifact_name 必须为 str 或 None")
        if len(self.artifact_name or "") > 255:
            raise TaskArtifactContentError("artifact_name 超长(>255)")

    @property
    def storage(self) -> str:
        """存储介质派生列:file 分支与 ``storage_type`` 同值,其余恒 inline。"""

        if self.kind is ArtifactKind.FILE:
            return self.content.storage_type.value
        return "inline"


def canonical_json(value: Any) -> str:
    """控制信号用 canonical JSON:键序无关、无冗余空白。"""

    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_to_json(content: ArtifactContent) -> str:
    """序列化为 kind 前缀 canonical JSON(tag 由序列化器派发)。"""

    branch = type(content)
    kind = next((k for k, cls in _KIND_TO_CONTENT.items() if cls is branch), None)
    if kind is None:
        raise TaskArtifactContentError(f"未知内容分支: {branch.__name__}")
    payload = {"kind": kind.value}
    for field in fields(branch):
        payload[field.name] = getattr(content, field.name)
    return canonical_json(payload)


def content_from_json(payload: str) -> ArtifactContent:
    """按 kind tag 选构造类;未知 tag / 非法 JSON / 字段失配 fail-loud。"""

    if not isinstance(payload, str):
        raise TaskArtifactContentError("content 须为 JSON 字符串")
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise TaskArtifactContentError(f"content JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict) or "kind" not in data:
        raise TaskArtifactContentError(f"content 缺少 kind tag: {payload[:100]!r}")
    raw_kind = data["kind"]
    try:
        kind = ArtifactKind(raw_kind)
    except ValueError as exc:
        raise TaskArtifactContentError(f"未知 kind tag: {raw_kind!r}") from exc
    branch = _KIND_TO_CONTENT[kind]
    field_names = {field.name for field in fields(branch)}
    provided = {key: value for key, value in data.items() if key != "kind"}
    unknown = set(provided) - field_names
    if unknown:
        raise TaskArtifactContentError(f"{kind.value} 分支不接受字段: {sorted(unknown)}")
    missing = field_names - set(provided)
    if missing:
        raise TaskArtifactContentError(f"{kind.value} 分支缺少字段: {sorted(missing)}")
    if branch is FileContent and isinstance(provided.get("storage_type"), str):
        try:
            provided["storage_type"] = StorageType(provided["storage_type"])
        except ValueError as exc:
            raise TaskArtifactContentError(f"storage_type 非法: {provided['storage_type']!r}") from exc
    try:
        return branch(**provided)
    except TaskArtifactContentError:
        raise
    except TypeError as exc:
        raise TaskArtifactContentError(f"{kind.value} 分支字段失配: {exc}") from exc


def artifact_content_hash(content: ArtifactContent) -> str:
    """幂等查重键成分:sha256(canonical 序列化)。"""

    return hashlib.sha256(content_to_json(content).encode("utf-8")).hexdigest()


def artifact_text_value(output: dict[str, Any]) -> str | None:
    """文本归约器:单键 ``result``/``markdown``/``output`` 且值为非空字符串 → 该字符串。

    与 relay 投影(``_relay_output_value``)是超集分叉:此处独立新增 capture 语义,互不影响。
    """

    if not isinstance(output, dict) or len(output) != 1:
        return None
    key, value = next(iter(output.items()))
    if key not in ("result", "markdown", "output"):
        return None
    if isinstance(value, str) and value:
        return value
    return None


def new_artifact_id() -> str:
    """产物 ID:``task-art-`` + uuid4 hex 前 16 位(唯一性由生成器保证)。"""

    return "task-art-" + uuid.uuid4().hex[:16]
