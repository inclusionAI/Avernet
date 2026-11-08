"""TaskArtifactObjectStoragePlugin —— task 模块自有的产物对象存储缝。

(与 SessionInitiator 同款下沉:task 私有协议定义在 task 自己的模块下,不占
公共 ``plugin_api/`` 面。)OBJECT_FILE 产物的字节承载(spec 2026-10-20 §13):

- community: ``task_artifact_object_storage.py``(同目录) fs 实现
  (``backend: fs``,``user_config.task_artifact_storage`` 块);
- corp(消费仓): 其自有对象存储服务(``backend: oss``),列内 DI 首次解析时经
  部署自有凭证链(SecretResolver)懒取 AK/SK,导入期零触网;
- test / singlebox: ``task_artifact_object_storage.py``(同目录) 内存实现,
  真实字节回环。

实现经 ``@plugin_impl`` 注册(Rule 20/21 卫生口径);按配置 fail-fast,禁止
散落 ``if is_local``。这个缝与其兄弟 ``plugin_api/object_storage.py`` 的一处
刻意的契约差异:三态读(:class:`TaskArtifactObjectReadResult`)是**基础**契约
而非可选 capability —— 下载路由必须区分"字节缺失"(404)与"存储不可用"(502),
任何实现都必须能回答这个问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class TaskArtifactObjectReadStatus(str, Enum):
    """Distinguish a missing object from an unavailable object store."""

    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class TaskArtifactObjectReadResult:
    status: TaskArtifactObjectReadStatus
    content: bytes | None = None


@runtime_checkable
class TaskArtifactObjectStoragePlugin(Plugin, Protocol):
    """OBJECT_FILE 产物字节的对象存储操作面。

    ``put_object`` 吞传输/SDK 错误成 ``False`` —— 上传服务据此拒绝登记
    产例行(字节没落存储的产物记录宁可没有,不可有假);``get_object``
    三态分辨 FOUND / NOT_FOUND / FAILED,下载路由映射 200 / 404 / 502。
    """

    def put_object(self, key: str, content: bytes) -> bool:
        """Store ``content`` at ``key``. Return ``True`` on success.

        Implementations should swallow transport/SDK errors and return
        ``False`` rather than raising, so the upload service can refuse
        the artifact registration instead of surfacing store internals.
        """
        ...

    def get_object(self, key: str) -> TaskArtifactObjectReadResult:
        """Read ``key`` with distinct FOUND / NOT_FOUND / FAILED outcomes."""
        ...

    def delete_object(self, key: str) -> bool:
        """Delete ``key``; an already-absent object counts as success.

        Not routed this phase — carried for future retention/cleanup.
        Implementations should swallow transport/SDK errors into
        ``False`` rather than raising.
        """
        ...
