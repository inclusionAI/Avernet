"""TaskArtifactObjectStoragePlugin 实现契约(world 驱动)。

协议随 OBJECT_FILE 落在 task 模块自有位置
``core/task/task_context/task_artifact/artifact_object_storage.py``(方案下沉,
与 SessionInitiator 同款;``world`` / ``community_world`` fixtures 由本目录
conftest 再导出)。local 实现是真实内存 dict(非 MagicMock),下面的回环是
上传/下载服务消费面下的真实 put→get→delete:

- ``put_object`` / ``get_object`` / ``delete_object`` 经 world 注入的实现真实生效;
- 三态读分辨 FOUND / NOT_FOUND —— 下载路由映射 200 / 404 的判别基础;
- community 列绑的是契约形状的 fs 实现。
"""
from __future__ import annotations

from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectReadStatus,
    TaskArtifactObjectStoragePlugin,
)


def test_roundtrip_routes_through_local_impl(world) -> None:
    objects = world.get(TaskArtifactObjectStoragePlugin)
    key = "task-artifacts/t-1/9f86d081884c7d659a2feaa0c55ad015.pdf"

    assert objects.put_object(key, b"%PDF-1.4 artifact-bytes") is True

    read = objects.get_object(key)
    assert read.status is TaskArtifactObjectReadStatus.FOUND
    assert read.content == b"%PDF-1.4 artifact-bytes"
    # Three-state contract: a key never written reads NOT_FOUND, not FAILED.
    missing = objects.get_object("task-artifacts/t-1/never-written.bin")
    assert missing.status is TaskArtifactObjectReadStatus.NOT_FOUND
    assert missing.content is None

    # Idempotent delete mirrors the Protocol contract.
    assert objects.delete_object(key) is True
    assert objects.delete_object(key) is True
    assert objects.get_object(key).status is TaskArtifactObjectReadStatus.NOT_FOUND


def test_put_rejects_non_bytes_payload(world) -> None:
    objects = world.get(TaskArtifactObjectStoragePlugin)
    assert objects.put_object("task-artifacts/t-1/x.bin", "not-bytes") is False


def test_community_column_binds_contract_shaped_impl(community_world) -> None:
    from agentclaw.community.plugins.community.task_artifact_object_storage import (
        CommunityFsTaskArtifactObjectStorage,
    )

    objects = community_world.get(TaskArtifactObjectStoragePlugin)
    assert isinstance(objects, CommunityFsTaskArtifactObjectStorage)