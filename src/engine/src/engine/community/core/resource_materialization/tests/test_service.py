from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from engine.community.core.resource_materialization.models import (
    ChatAttachmentMaterializationRequest,
    MaterializationRequest,
)
from engine.community.core.resource_materialization.service import (
    ChatAttachmentPreparationError,
    ResourceMaterializationService,
    ResourceNotMaterializedError,
    build_session_file_relative_path,
)


class _PullClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0
        self.requests = []
        self.destinations = []

    async def pull(self, request, destination: Path) -> None:
        self.calls += 1
        self.requests.append(request)
        self.destinations.append(destination)
        destination.write_bytes(self.content)


class _FailingPullClient:
    async def pull(self, request, destination: Path) -> None:
        raise RuntimeError("temporary download failed with a secret URL")


class _CallbackClient:
    def __init__(self) -> None:
        self.results = []

    async def report(self, result) -> None:
        self.results.append(result)


def test_session_file_path_builder_matches_backend_layout():
    relative = build_session_file_relative_path(
        scope_key_hash="a" * 64,
        session_key_hash="b" * 64,
        resource_id="sr_contract",
        filename="design.pdf",
    )

    assert relative.as_posix() == (
        f".teamclaw/session-files/{'a' * 64}/{'b' * 64}/sr_contract/design.pdf"
    )


@pytest.mark.asyncio
async def test_chat_attachment_reuses_materializer_without_backend_callback(
    tmp_path: Path,
):
    content = b"design"
    pull = _PullClient(content)
    callback = _CallbackClient()
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=callback,
        temporary_url_pull_client=pull,
        workspace_root_provider=lambda: tmp_path,
    )
    request = ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="design.pdf",
        temporary_url="https://files.example/temporary?secret=redacted",
        scope_key_hash="a" * 64,
        size_bytes=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
    )

    result = await service.materialize_chat_attachment(request)

    assert result.ready is True
    assert callback.results == []
    entry = service.manifest_store.get(result.resource_id)
    assert entry is not None
    assert entry.source_kind == "temporary_url"
    assert entry.source_attachment_id == "att-1"
    assert (
        entry.source_url_hash
        == hashlib.sha256(request.temporary_url.encode("utf-8")).hexdigest()
    )
    assert request.temporary_url not in service.manifest_store.path.read_text()


def _chat_request(**overrides) -> ChatAttachmentMaterializationRequest:
    values = {
        "attachment_id": "att-1",
        "session_key": "session-1",
        "filename": "design.pdf",
        "temporary_url": "https://files.example/object?token=secret",
        "scope_key_hash": "a" * 64,
    }
    values.update(overrides)
    return ChatAttachmentMaterializationRequest(**values)


_PNG_CONTENT = b"\x89PNG\r\n\x1a\n" + b"validated-image"


def test_resource_materialization_rejects_non_positive_image_limit(tmp_path: Path):
    with pytest.raises(ValueError, match="image size limit"):
        ResourceMaterializationService(
            pull_client=_PullClient(b"unused"),
            callback_client=_CallbackClient(),
            workspace_root_provider=lambda: tmp_path,
            max_chat_image_bytes=0,
        )


@pytest.mark.asyncio
async def test_prepare_chat_image_returns_validated_in_memory_content(tmp_path: Path):
    pull = _PullClient(_PNG_CONTENT)
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=pull,
        workspace_root_provider=lambda: tmp_path,
    )
    request = _chat_request(
        filename="image.png",
        media_type="image/x-png",
        size_bytes=len(_PNG_CONTENT),
        content_hash=hashlib.sha256(_PNG_CONTENT).hexdigest(),
    )

    prepared = await service.prepare_chat_image_attachment(request)

    assert prepared.attachment_id == "att-1"
    assert prepared.filename == "image.png"
    assert prepared.media_type == "image/png"
    assert prepared.content == _PNG_CONTENT
    assert pull.calls == 1
    assert pull.requests[0].download_max_bytes == 20 * 1024 * 1024
    assert not pull.destinations[0].exists()


@pytest.mark.asyncio
async def test_prepare_chat_image_rejects_non_image_and_cleans_temporary_file(
    tmp_path: Path,
):
    pull = _PullClient(b"not-an-image")
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=pull,
        workspace_root_provider=lambda: tmp_path,
    )

    with pytest.raises(ChatAttachmentPreparationError) as error:
        await service.prepare_chat_image_attachment(
            _chat_request(filename="image.png", media_type="image/png")
        )

    assert error.value.reason == "invalid_image_content"
    assert not pull.destinations[0].exists()


@pytest.mark.asyncio
async def test_prepare_chat_image_rejects_mime_mismatch(tmp_path: Path):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=_PullClient(_PNG_CONTENT),
        workspace_root_provider=lambda: tmp_path,
    )

    with pytest.raises(ChatAttachmentPreparationError) as error:
        await service.prepare_chat_image_attachment(
            _chat_request(filename="image.jpg", media_type="image/jpeg")
        )

    assert error.value.reason == "media_type_mismatch"


@pytest.mark.asyncio
async def test_prepare_chat_image_rejects_expired_url_before_download(
    tmp_path: Path,
):
    pull = _PullClient(_PNG_CONTENT)
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=pull,
        workspace_root_provider=lambda: tmp_path,
    )

    with pytest.raises(ChatAttachmentPreparationError) as error:
        await service.prepare_chat_image_attachment(
            _chat_request(filename="image.png", expires_at_ms=1)
        )

    assert error.value.reason == "temporary_url_expired"
    assert pull.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected_media_type"),
    [
        (b"\xff\xd8\xffimage", "image/jpeg"),
        (b"GIF89aimage", "image/gif"),
        (b"RIFF\x04\x00\x00\x00WEBPimage", "image/webp"),
    ],
)
async def test_prepare_chat_image_detects_supported_magic_bytes(
    tmp_path: Path,
    content: bytes,
    expected_media_type: str,
):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=_PullClient(content),
        workspace_root_provider=lambda: tmp_path,
    )

    prepared = await service.prepare_chat_image_attachment(
        _chat_request(filename="image.bin")
    )

    assert prepared.media_type == expected_media_type


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_overrides", "expected_reason"),
    [
        ({"filename": "../image.png"}, "invalid_filename"),
        ({"filename": "image.png", "size_bytes": 9}, "size_mismatch"),
        ({"filename": "image.png", "content_hash": "0" * 64}, "hash_mismatch"),
    ],
)
async def test_prepare_chat_image_rejects_invalid_provider_metadata(
    tmp_path: Path,
    request_overrides: dict,
    expected_reason: str,
):
    pull = _PullClient(_PNG_CONTENT)
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=pull,
        workspace_root_provider=lambda: tmp_path,
    )

    with pytest.raises(ChatAttachmentPreparationError) as error:
        await service.prepare_chat_image_attachment(_chat_request(**request_overrides))

    assert error.value.reason == expected_reason


@pytest.mark.asyncio
async def test_prepare_chat_image_rejects_declared_size_before_download(tmp_path: Path):
    pull = _PullClient(_PNG_CONTENT)
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=pull,
        workspace_root_provider=lambda: tmp_path,
        max_chat_image_bytes=8,
    )

    with pytest.raises(ChatAttachmentPreparationError) as error:
        await service.prepare_chat_image_attachment(
            _chat_request(filename="image.png", size_bytes=9)
        )

    assert error.value.reason == "size_limit_exceeded"
    assert pull.calls == 0


@pytest.mark.asyncio
async def test_prepare_chat_image_fails_closed_without_downloader(tmp_path: Path):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )

    with pytest.raises(ChatAttachmentPreparationError) as error:
        await service.prepare_chat_image_attachment(
            _chat_request(filename="image.png")
        )

    assert error.value.reason == "temporary_url_pull_not_configured"


@pytest.mark.asyncio
async def test_prepare_chat_image_maps_downloader_error_to_safe_reason(
    tmp_path: Path,
    caplog,
):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=_FailingPullClient(),
        workspace_root_provider=lambda: tmp_path,
    )

    with pytest.raises(ChatAttachmentPreparationError) as error:
        await service.prepare_chat_image_attachment(
            _chat_request(filename="image.png")
        )

    assert error.value.reason == "pull_failed"
    assert "secret URL" not in caplog.text


@pytest.mark.asyncio
async def test_chat_attachment_rejects_expired_url_before_download(tmp_path: Path):
    pull = _PullClient(b"unused")
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=pull,
        workspace_root_provider=lambda: tmp_path,
    )

    result = await service.materialize_chat_attachment(_chat_request(expires_at_ms=1))

    assert result.ready is False
    assert result.error_code == "temporary_url_expired"
    assert pull.calls == 0


@pytest.mark.asyncio
async def test_chat_attachment_fails_closed_without_temporary_url_client(
    tmp_path: Path,
):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )

    result = await service.materialize_chat_attachment(_chat_request())

    assert result.ready is False
    assert result.error_code == "temporary_url_pull_not_configured"


@pytest.mark.asyncio
async def test_chat_attachment_maps_download_exception_to_safe_failure(
    tmp_path: Path,
    caplog,
):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=_FailingPullClient(),
        workspace_root_provider=lambda: tmp_path,
    )

    result = await service.materialize_chat_attachment(_chat_request())

    assert result.ready is False
    assert result.error_code == "pull_failed"
    assert "token=secret" not in caplog.text


@pytest.mark.asyncio
async def test_remove_chat_materialization_removes_manifest_and_file(tmp_path: Path):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        temporary_url_pull_client=_PullClient(b"design"),
        workspace_root_provider=lambda: tmp_path,
    )
    result = await service.materialize_chat_attachment(_chat_request())
    target = Path(result.canonical_bot_absolute_path)

    await service.remove_chat_materialization(result.resource_id)

    assert not target.exists()
    assert service.manifest_store.get(result.resource_id) is None


@pytest.mark.asyncio
async def test_remove_chat_materialization_ignores_unknown_resource(tmp_path: Path):
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )

    await service.remove_chat_materialization("sr_unknown")


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
            "workspace/.teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
        ),
        "filename": "report.txt",
        "size_bytes": len(content),
        "content_hash": hashlib.sha256(content).hexdigest(),
        "uploaded_at": "2026-08-03T10:20:30Z",
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
        tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
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
    assert entry.uploaded_at == datetime(2026, 8, 3, 10, 20, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_materialize_supports_filename_near_filesystem_segment_limit(
    tmp_path: Path,
):
    content = b"long filename content"
    filename = f"{'a' * 240}.txt"
    service = ResourceMaterializationService(
        pull_client=_PullClient(content),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )
    request = _request(
        content,
        filename=filename,
        device_path=(
            f"workspace/.teamclaw/session-files/scope_abc/session_abc/sr_001/{filename}"
        ),
    )

    result = await service.materialize(request)

    target = (
        tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001" / filename
    )
    assert result.ready is True
    assert target.read_bytes() == content
    assert not list(target.parent.glob(".part-*"))


@pytest.mark.asyncio
async def test_materialize_supports_unicode_filename(tmp_path: Path):
    content = b"unicode filename content"
    filename = "\u4e2d\u6587 \u62a5\u544a (final)\uff08\u5df2\u5ba1\uff09.txt"
    service = ResourceMaterializationService(
        pull_client=_PullClient(content),
        callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )
    request = _request(
        content,
        filename=filename,
        device_path=(
            f"workspace/.teamclaw/session-files/scope_abc/session_abc/sr_001/{filename}"
        ),
    )

    result = await service.materialize(request)

    target = (
        tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001" / filename
    )
    assert result.ready is True
    assert target.read_bytes() == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    [
        ".",
        "..",
        "folder/report.txt",
        r"folder\report.txt",
        "report?.txt",
        "report\n.txt",
    ],
)
async def test_materialize_rejects_unsafe_filename_characters(
    tmp_path: Path,
    filename: str,
):
    content = b"unsafe filename content"
    callback = _CallbackClient()
    service = ResourceMaterializationService(
        pull_client=_PullClient(content),
        callback_client=callback,
        workspace_root_provider=lambda: tmp_path,
    )
    request = _request(
        content,
        filename=filename,
        device_path=(
            f"workspace/.teamclaw/session-files/scope_abc/session_abc/sr_001/{filename}"
        ),
    )

    result = await service.materialize(request)

    assert result.ready is False
    assert result.error_code == "invalid_device_path"
    assert callback.results == [result]


@pytest.mark.asyncio
async def test_materialize_rejects_filename_exceeding_utf8_segment_limit(
    tmp_path: Path,
):
    content = b"too long unicode filename"
    filename = f"{'\u4e2d' * 86}.txt"
    callback = _CallbackClient()
    service = ResourceMaterializationService(
        pull_client=_PullClient(content),
        callback_client=callback,
        workspace_root_provider=lambda: tmp_path,
    )
    request = _request(
        content,
        filename=filename,
        device_path=(
            f"workspace/.teamclaw/session-files/scope_abc/session_abc/sr_001/{filename}"
        ),
    )

    result = await service.materialize(request)

    assert result.ready is False
    assert result.error_code == "invalid_device_path"
    assert callback.results == [result]


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
    entry = service.manifest_store.get("sr_001")
    assert entry is not None
    assert entry.baas_tenant == "tenant-1"


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

    target = (
        tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
    )
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
    controlled_parent = tmp_path / ".teamclaw/session-files/scope_abc/session_abc"
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
    target = (
        tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
    )
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
    target = (
        tmp_path / ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
    )
    outside = tmp_path.parent / "outside-content.txt"
    outside.write_bytes(content)
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(ResourceNotMaterializedError, match="resource_not_materialized"):
        service.open_content(resource_id="sr_001", disposition="attachment")
