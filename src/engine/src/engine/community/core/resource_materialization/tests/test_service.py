from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
)
from engine.community.core.resource_materialization.service import (
    ResourceMaterializationService,
    ResourceNotMaterializedError,
)


class _PullClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0
        self.requests = []

    async def pull(self, request, destination: Path) -> None:
        self.calls += 1
        self.requests.append(request)
        destination.write_bytes(self.content)


class _CallbackClient:
    def __init__(self) -> None:
        self.results = []

    async def report(self, result) -> None:
        self.results.append(result)


class _FailingCallbackClient:
    def __init__(self) -> None:
        self.calls = 0

    async def report(self, result) -> None:
        self.calls += 1
        raise RuntimeError("callback unavailable")


def _request(content: bytes, **overrides) -> MaterializationRequest:
    values = {
        "resource_id": "sr_001",
        "transfer_id": "transfer-001",
        "task_id": "task-001",
        "task_version": 1,
        "scope_key_hash": "scope_abc",
        "session_key_hash": "session_abc",
        "device_path": (
            "workspace/.teamclaw/session-files/scope_abc/session_abc/"
            "sr_001/report.txt"
        ),
        "filename": "report.txt",
        "size_bytes": len(content),
        "content_hash": hashlib.sha256(content).hexdigest(),
    }
    values.update(overrides)
    return MaterializationRequest(**values)


@pytest.mark.asyncio
async def test_materialize_writes_atomic_file_manifest_and_callback(tmp_path: Path):
    content = b"risk report"
    pull = _PullClient(content)
    callback = _CallbackClient()
    service = ResourceMaterializationService(
        pull_client=pull,
        callback_client=callback,
        workspace_root_provider=lambda: tmp_path,
    )

    result = await service.materialize(_request(content))

    expected = (
        tmp_path
        / ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
    ).resolve()
    assert result.ready is True
    assert Path(result.canonical_bot_absolute_path) == expected
    assert expected.read_bytes() == content
    assert pull.calls == 1
    assert callback.results == [result]
    entry = service.manifest_store.get("sr_001")
    assert entry.status == "ready"
    assert entry.observed_size == len(content)
    assert entry.observed_mtime_ns is not None
    assert entry.observed_inode is not None


@pytest.mark.asyncio
async def test_session_v2_materialization_never_persists_raw_session_id(tmp_path: Path):
    content = b"session v2 content"
    pull = _PullClient(content)
    service = ResourceMaterializationService(
        pull_client=pull,
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )
    request = _request(
        content,
        transfer_api_version="session_v2",
        tenant="tenant-1",
        session_id="session/raw-value",
        workspace_relative_path=(
            ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
        ),
        device_path=None,
    )

    result = await service.materialize(request)

    assert result.ready is True
    assert pull.requests[0].session_id == "session/raw-value"
    manifest_text = (tmp_path / ".teamclaw/session-files/.manifest.json").read_text()
    assert "session/raw-value" not in manifest_text


@pytest.mark.asyncio
async def test_materialize_hashes_files_in_worker_thread(tmp_path: Path):
    content = b"hash on worker thread"
    service = ResourceMaterializationService(
        pull_client=_PullClient(content),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )
    threaded_functions = []

    async def run_in_worker_thread(function, /, *args, **kwargs):
        threaded_functions.append(function)
        return function(*args, **kwargs)

    with patch(
        "engine.community.core.resource_materialization.service.asyncio.to_thread",
        new=run_in_worker_thread,
    ):
        result = await service.materialize(_request(content))

    assert result.ready is True
    assert threaded_functions == [ResourceMaterializationService._sha256]


@pytest.mark.asyncio
async def test_materialize_is_idempotent_for_same_ready_task(tmp_path: Path):
    content = b"same bytes"
    pull = _PullClient(content)
    callback = _CallbackClient()
    service = ResourceMaterializationService(
        pull_client=pull,
        callback_client=callback,
        workspace_root_provider=lambda: tmp_path,
    )
    request = _request(content)

    first = await service.materialize(request)
    second = await service.materialize(request)

    assert first.ready is True and second.ready is True
    assert pull.calls == 1
    assert len(callback.results) == 2


@pytest.mark.asyncio
async def test_hash_mismatch_removes_partial_file_and_reports_failure(tmp_path: Path):
    pull = _PullClient(b"tampered")
    callback = _CallbackClient()
    service = ResourceMaterializationService(
        pull_client=pull,
        callback_client=callback,
        workspace_root_provider=lambda: tmp_path,
    )

    result = await service.materialize(_request(b"expected"))

    target = tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
    assert result.ready is False
    assert result.error_code == "hash_mismatch"
    assert not target.exists()
    assert not list(target.parent.glob("*.part-*"))
    assert callback.results == [result]


@pytest.mark.asyncio
async def test_materialize_rejects_untrusted_device_path_escape(tmp_path: Path, caplog):
    callback = _CallbackClient()
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"x"),
        callback_client=callback,
        workspace_root_provider=lambda: tmp_path,
    )
    request = _request(
        b"x",
        device_path="../../outside/report.txt",
    )

    result = await service.materialize(request)

    assert result.ready is False
    assert result.error_code == "invalid_device_path"
    assert callback.results == [result]
    assert not (tmp_path.parent / "outside/report.txt").exists()
    assert "reason=device_path traversal is forbidden" in caplog.text
    assert "../../outside/report.txt" not in caplog.text


@pytest.mark.asyncio
async def test_materialize_rejects_symlink_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside-resource-target"
    outside.mkdir()
    controlled_parent = (
        tmp_path / ".teamclaw/session-files/scope_abc/session_abc"
    )
    controlled_parent.mkdir(parents=True)
    (controlled_parent / "sr_001").symlink_to(outside, target_is_directory=True)
    pull = _PullClient(b"x")
    callback = _CallbackClient()
    service = ResourceMaterializationService(
        pull_client=pull,
        callback_client=callback,
        workspace_root_provider=lambda: tmp_path,
    )

    result = await service.materialize(_request(b"x"))

    assert result.ready is False
    assert result.error_code == "invalid_device_path"
    assert pull.calls == 0
    assert callback.results == [result]
    assert not (outside / "report.txt").exists()


@pytest.mark.asyncio
async def test_callback_failure_does_not_reclassify_or_redownload_ready_file(
    tmp_path: Path,
):
    content = b"durable bytes"
    pull = _PullClient(content)
    callback = _FailingCallbackClient()
    service = ResourceMaterializationService(
        pull_client=pull,
        callback_client=callback,
        workspace_root_provider=lambda: tmp_path,
    )
    request = _request(content)

    with pytest.raises(RuntimeError, match="callback unavailable"):
        await service.materialize(request)

    assert callback.calls == 3
    assert pull.calls == 1
    assert service.manifest_store.get(request.resource_id).status == "ready"

    service._callback_client = _CallbackClient()
    result = await service.materialize(request)

    assert result.ready is True
    assert pull.calls == 1


@pytest.mark.asyncio
async def test_open_content_resolves_ready_manifest_to_controlled_file(tmp_path: Path):
    content = b"ready content"
    service = ResourceMaterializationService(
        pull_client=_PullClient(content),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )
    await service.materialize(_request(content))

    resolved = service.open_content(resource_id="sr_001", disposition="inline")

    assert resolved.path.read_bytes() == content
    assert resolved.media_type == "text/plain"
    assert resolved.content_disposition.startswith("inline;")
    assert resolved.size_bytes == len(content)


def test_open_content_rejects_missing_or_non_ready_manifest_file(tmp_path: Path):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )

    with pytest.raises(ResourceNotMaterializedError, match="resource_not_materialized"):
        service.open_content(resource_id="missing", disposition="inline")


@pytest.mark.asyncio
async def test_open_content_rejects_same_size_hash_changed_file(tmp_path: Path):
    content = b"ready content"
    service = ResourceMaterializationService(
        pull_client=_PullClient(content),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )
    await service.materialize(_request(content))
    target = tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
    target.write_bytes(b"changed bytes")

    with pytest.raises(ResourceNotMaterializedError, match="resource_not_materialized"):
        service.open_content(resource_id="sr_001", disposition="inline")


@pytest.mark.asyncio
async def test_open_content_rejects_file_replaced_by_outside_symlink(tmp_path: Path):
    content = b"controlled content"
    service = ResourceMaterializationService(
        pull_client=_PullClient(content),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )
    await service.materialize(_request(content))
    target = tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
    outside = tmp_path.parent / "outside-content.txt"
    outside.write_bytes(content)
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(ResourceNotMaterializedError, match="resource_not_materialized"):
        service.open_content(resource_id="sr_001", disposition="attachment")
