from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from engine.community.core.resource_materialization.service import ManifestStore
from engine.community.core.session_files.models import SessionFileError
from engine.community.core.session_files.service import SessionFileService


def _service(root):
    return SessionFileService(
        workspace_root_provider=lambda: root, default_baas_tenant="test-tenant"
    )


def test_register_live_session_isolation_and_source_retention(tmp_path):
    source = tmp_path / "report.txt"
    source.write_bytes(b"original")
    service = _service(tmp_path)
    view = service.register_file(
        session_key="session:a", workspace_relative_path="report.txt"
    )
    entry = ManifestStore(tmp_path).get(view.resource_id)
    assert entry.source_kind == "workspace_live"
    assert entry.transfer_id == ""
    assert entry.task_id == ""
    assert entry.content_hash == hashlib.sha256(b"original").hexdigest()
    assert service.list_files(session_key="session:a") == [view]
    assert service.list_files(session_key="session:b") == []
    with pytest.raises(SessionFileError, match="resource_not_found"):
        service.delete_file(session_key="session:b", resource_id=view.resource_id)
    source.write_bytes(b"edited")
    content = service.open_content(
        session_key="session:a", resource_id=view.resource_id, disposition="inline"
    )
    assert content.stream.read() == b"edited"
    content.stream.close()
    service.delete_file(session_key="session:a", resource_id=view.resource_id)
    assert source.read_bytes() == b"edited"
    assert source.exists()
    assert service.list_files(session_key="session:a") == []


def test_registration_is_idempotent_by_session_and_path(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("one")
    service = _service(tmp_path)
    first = service.register_file(session_key="a", workspace_relative_path="report.txt")
    assert first.creator_type == "bot"
    assert first.created_at == first.uploaded_at
    assert first.created_at.endswith("Z")
    assert (
        service.register_file(session_key="a", workspace_relative_path="report.txt")
        == first
    )
    other = service.register_file(session_key="b", workspace_relative_path="report.txt")
    assert other.resource_id != first.resource_id
    source.write_text("two")
    second = service.register_file(
        session_key="a", workspace_relative_path="report.txt"
    )
    assert second.resource_id == first.resource_id
    assert second.created_at == first.created_at
    assert second.creator_type == "bot"
    assert len(service.list_files(session_key="a")) == 1


def test_creator_name_is_first_registration_snapshot(tmp_path):
    (tmp_path / "report").write_text("one")
    first_service = SessionFileService(workspace_root_provider=lambda: tmp_path, bot_name="  报告助手  ")
    first = first_service.register_file(session_key="a", workspace_relative_path="report")
    assert first.creator_name == "报告助手"
    assert ManifestStore(tmp_path).get(first.resource_id).creator_name == "报告助手"
    renamed_service = SessionFileService(workspace_root_provider=lambda: tmp_path, bot_name="新名称")
    (tmp_path / "report").write_text("edited")
    repeated = renamed_service.register_file(session_key="a", workspace_relative_path="report")
    assert repeated.creator_name == "报告助手"
    assert renamed_service.list_files(session_key="a")[0].creator_name == "报告助手"
    assert renamed_service.register_file(session_key="b", workspace_relative_path="report").creator_name == "新名称"
    unknown = _service(tmp_path).register_file(session_key="c", workspace_relative_path="report")
    assert unknown.creator_name is None
    assert renamed_service.register_file(session_key="c", workspace_relative_path="report").creator_name is None


@pytest.mark.parametrize("name, expected", [("  报告助手  ", "报告助手"), ("", None), ("bad\nname", None), ("x" * 256, None)])
def test_platform_runtime_name_is_default_for_all_composition_profiles(tmp_path, monkeypatch, name, expected):
    monkeypatch.setenv("TEAMCLAW_BOT_NAME", name)
    (tmp_path / "report").write_text("one")
    service = _service(tmp_path)
    monkeypatch.setenv("TEAMCLAW_BOT_NAME", "later")
    first = service.register_file(session_key="a", workspace_relative_path="report")
    assert first.creator_name == expected
    assert service.list_files(session_key="a")[0].creator_name == expected


def test_concurrent_registrations_preserve_duplicates_and_unrelated_entries(tmp_path):
    for i in range(8):
        (tmp_path / f"{i}.txt").write_text(str(i))

    def register(i):
        return (
            _service(tmp_path)
            .register_file(session_key="a", workspace_relative_path=f"{i % 8}.txt")
            .resource_id
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(register, range(32)))
    assert len(set(ids)) == 8
    assert {
        entry.resource_id for entry in ManifestStore(tmp_path).list_entries()
    } == set(ids)


def test_legacy_manifest_creation_source_remains_unknown(tmp_path):
    import json

    source = tmp_path / "report"
    source.write_bytes(b"report")
    service = _service(tmp_path)
    view = service.register_file(session_key="a", workspace_relative_path="report")
    store = ManifestStore(tmp_path)
    payload = json.loads(store.path.read_text())
    entry = payload["resources"][view.resource_id]
    entry.pop("creator_type")
    entry.pop("created_at")
    # Historical BaaS entries may have originated from either humans or Bots.
    entry["source_kind"] = "baas_session_file"
    store.path.write_text(json.dumps(payload))
    legacy = service.list_files(session_key="a")[0]
    assert legacy.creator_type is None
    assert legacy.created_at == view.uploaded_at
    repeated = service.register_file(session_key="a", workspace_relative_path="report")
    assert repeated.creator_type is None
    assert repeated.created_at == view.uploaded_at
    entry["uploaded_at"] = None
    store.path.write_text(json.dumps(payload))
    assert service.list_files(session_key="a")[0].created_at is None
    repeated = service.register_file(session_key="a", workspace_relative_path="report")
    assert repeated.creator_type is None
    assert repeated.created_at is None


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../escape",
        "a/../report",
        ".",
        "directory",
        "missing",
        "a\\b",
        "bad\x00name",
        ".teamclaw/session-files/x",
    ],
)
def test_invalid_sources_never_publish(tmp_path, path):
    (tmp_path / "directory").mkdir()
    with pytest.raises(SessionFileError):
        _service(tmp_path).register_file(session_key="a", workspace_relative_path=path)
    assert ManifestStore(tmp_path).list_entries() == []


@pytest.mark.parametrize("intermediate", [False, True])
def test_symlink_source_is_rejected_even_inside_workspace(tmp_path, intermediate):
    directory = tmp_path / "real"
    directory.mkdir()
    (directory / "report").write_text("private")
    (tmp_path / "link").symlink_to(directory if intermediate else directory / "report")
    with pytest.raises(SessionFileError):
        _service(tmp_path).register_file(
            session_key="a",
            workspace_relative_path="link/report" if intermediate else "link",
        )
    assert ManifestStore(tmp_path).list_entries() == []


def test_symlink_snapshot_parent_does_not_write_outside(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "report").write_text("hello")
    (root / ".teamclaw").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SessionFileError):
        _service(root).register_file(session_key="a", workspace_relative_path="report")
    assert list(outside.iterdir()) == []


def test_source_replaced_between_validation_and_open_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "report"
    source.write_text("safe")
    outside = tmp_path.parent / f"{tmp_path.name}-secret"
    outside.write_text("private-contents")
    real_open = os.open

    def racing_open(path, flags, *args, **kwargs):
        if path == "report":
            source.unlink()
            source.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", racing_open)
    try:
        with pytest.raises(SessionFileError):
            _service(tmp_path).register_file(
                session_key="a", workspace_relative_path="report"
            )
        assert ManifestStore(tmp_path).list_entries() == []
    finally:
        outside.unlink()


def test_source_mutation_during_copy_never_publishes(tmp_path, monkeypatch):
    source = tmp_path / "report"
    source.write_bytes(b"original")
    real_fstat = os.fstat
    calls = 0

    def mutate(fd):
        nonlocal calls
        observed = real_fstat(fd)
        if observed.st_ino == source.stat().st_ino:
            calls += 1
            if calls == 2:
                source.write_bytes(b"mutated-longer")
        return observed

    monkeypatch.setattr(os, "fstat", mutate)
    with pytest.raises(SessionFileError, match="resource_changed"):
        _service(tmp_path).register_file(
            session_key="a", workspace_relative_path="report"
        )
    assert ManifestStore(tmp_path).list_entries() == []


def test_manifest_failure_is_reported_and_snapshot_cleaned(tmp_path, monkeypatch):
    (tmp_path / "report").write_text("original")

    def fail(*args, **kwargs):
        raise OSError("secret-storage-error")

    monkeypatch.setattr(ManifestStore, "upsert", fail)
    with pytest.raises(SessionFileError, match="registration_failed"):
        _service(tmp_path).register_file(
            session_key="a", workspace_relative_path="report"
        )
    assert ManifestStore(tmp_path).list_entries() == []
    assert (tmp_path / "report").read_text() == "original"
    assert not list((tmp_path / ".teamclaw/session-files").rglob("*.part"))


def test_registration_logs_business_session_but_never_content(tmp_path, caplog):
    (tmp_path / "report").write_text("secret-file-contents")
    with caplog.at_level(logging.INFO):
        service = _service(tmp_path)
        service.register_file(
            session_key="session:business-id", workspace_relative_path="report"
        )
        with pytest.raises(SessionFileError):
            service.register_file(
                session_key="session:business-id", workspace_relative_path="missing"
            )
    assert all(record.sessionKey == "session:business-id" for record in caplog.records)
    assert "register.request" in caplog.text
    assert "register.success" in caplog.text
    assert "register.failure" in caplog.text
    assert "secret-file-contents" not in caplog.text


def test_process_registrations_preserve_manifest(tmp_path):
    for i in range(4):
        (tmp_path / f"{i}.txt").write_text(str(i))
    script = """
import sys
from pathlib import Path
from engine.community.core.session_files.service import SessionFileService
service = SessionFileService(workspace_root_provider=lambda: Path(sys.argv[1]))
service.register_file(session_key='a', workspace_relative_path=sys.argv[2])
"""
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[5])}
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), f"{i % 4}.txt"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(8)
    ]
    for process in processes:
        _, error = process.communicate(timeout=30)
        assert process.returncode == 0, error.decode()
    assert len(_service(tmp_path).list_files(session_key="a")) == 4


def test_manifest_symlink_never_reads_or_overwrites_target(tmp_path):
    (tmp_path / "report").write_text("hello")
    directory = tmp_path / ".teamclaw/session-files"
    directory.mkdir(parents=True)
    external = tmp_path / "private-manifest"
    external.write_text("secret-not-json")
    (directory / ".manifest.json").symlink_to(external)
    with pytest.raises(SessionFileError):
        _service(tmp_path).register_file(
            session_key="a", workspace_relative_path="report"
        )
    assert external.read_text() == "secret-not-json"


def test_corrupt_manifest_is_not_replaced(tmp_path):
    (tmp_path / "report").write_text("hello")
    directory = tmp_path / ".teamclaw/session-files"
    directory.mkdir(parents=True)
    manifest = directory / ".manifest.json"
    manifest.write_text("invalid-json")
    with pytest.raises(SessionFileError):
        _service(tmp_path).register_file(
            session_key="a", workspace_relative_path="report"
        )
    assert manifest.read_text() == "invalid-json"



def test_live_replacement_metadata_and_independent_unlink(tmp_path):
    source = tmp_path / "report"
    source.write_bytes(b"old")
    service = _service(tmp_path)
    first = service.register_file(session_key="a", workspace_relative_path="./report")
    other = service.register_file(session_key="b", workspace_relative_path="report")
    replacement = tmp_path / "next"
    replacement.write_bytes(b"current bytes")
    replacement.replace(source)
    assert service.list_files(session_key="a")[0].size_bytes == 13
    assert service.get_file_size(session_key="a", resource_id=first.resource_id) == 13
    content = service.open_content(session_key="a", resource_id=first.resource_id, disposition="inline")
    # A later symlink swap must never affect actual response consumption.
    source.unlink()
    source.symlink_to(tmp_path / "private")
    (tmp_path / "private").write_bytes(b"secret")
    assert content.stream.read() == b"current bytes"
    assert content.size_bytes == 13
    content.stream.close()
    with pytest.raises(SessionFileError, match="resource_missing"):
        service.open_content(session_key="a", resource_id=first.resource_id, disposition="inline")
    service.delete_file(session_key="a", resource_id=first.resource_id)
    assert source.is_symlink()
    assert service.list_files(session_key="b")[0].resource_id == other.resource_id
    assert not list((tmp_path / ".teamclaw").rglob("*.part"))


@pytest.mark.parametrize("kind", ["ancestor", "final", "directory", "fifo", "missing"])
def test_live_each_access_rejects_unsafe_or_missing_sources(tmp_path, kind):
    directory = tmp_path / "real"
    directory.mkdir()
    source = directory / "report"
    source.write_bytes(b"data")
    service = _service(tmp_path)
    view = service.register_file(session_key="a", workspace_relative_path="real/report")
    if kind == "ancestor":
        directory.rename(tmp_path / "moved")
        directory.symlink_to(tmp_path / "moved", target_is_directory=True)
    else:
        source.unlink()
        if kind == "final":
            source.symlink_to(tmp_path / "private")
            (tmp_path / "private").write_bytes(b"secret")
        elif kind == "directory":
            source.mkdir()
        elif kind == "fifo":
            os.mkfifo(source)
    assert service.list_files(session_key="a")[0].availability == "missing"
    with pytest.raises(SessionFileError, match="resource_missing"):
        service.get_file_size(session_key="a", resource_id=view.resource_id)
    with pytest.raises(SessionFileError, match="resource_missing"):
        service.open_content(session_key="a", resource_id=view.resource_id, disposition="attachment")


@pytest.mark.parametrize("replacement", [False, True])
def test_live_mutation_during_content_read_is_rejected(tmp_path, monkeypatch, replacement):
    from engine.community.core.session_files import registration
    source = tmp_path / "report"
    source.write_bytes(b"safe content")
    service = _service(tmp_path)
    view = service.register_file(session_key="a", workspace_relative_path="report")
    original_source = registration._source
    from contextlib import contextmanager
    @contextmanager
    def racing_source(root, relative):
        with original_source(root, relative) as stream:
            class RacingRead:
                def fileno(self):
                    return stream.fileno()
                def read(self, size):
                    value = stream.read(size)
                    if value:
                        if replacement:
                            next_path = tmp_path / "replacement"
                            next_path.write_bytes(b"new bytes")
                            next_path.replace(source)
                        else:
                            source.write_bytes(b"modified")
                    return value
            yield RacingRead()
    monkeypatch.setattr(registration, "_source", racing_source)
    with pytest.raises(SessionFileError, match="resource_changed"):
        service.open_content(session_key="a", resource_id=view.resource_id, disposition="inline")


def test_large_live_inline_rejects_before_reading_bytes(tmp_path, monkeypatch):
    from engine.community.core.session_files import registration
    from contextlib import contextmanager
    source = tmp_path / "report"
    source.write_bytes(b"small")
    service = _service(tmp_path)
    view = service.register_file(session_key="a", workspace_relative_path="report")
    with source.open("wb") as stream:
        stream.truncate(2 * 1024 * 1024 * 1024)
    original_source = registration._source
    @contextmanager
    def no_read(root, relative):
        with original_source(root, relative) as stream:
            class ReadGuard:
                def fileno(self):
                    return stream.fileno()
                def read(self, size):
                    raise AssertionError("oversized inline must not read bytes")
            yield ReadGuard()
    monkeypatch.setattr(registration, "_source", no_read)
    with pytest.raises(SessionFileError, match="resource_preview_too_large"):
        service.open_content(session_key="a", resource_id=view.resource_id, disposition="inline")


def test_registration_lock_concurrent_creator_uses_existing_pinned_lock(tmp_path, monkeypatch):
    (tmp_path / "report").write_bytes(b"current")
    original_open = os.open
    collided = False

    def competing_open(path, flags, *args, **kwargs):
        nonlocal collided
        if path == ".registration.lock" and not collided:
            collided = True
            # Another process creates the lock before this process's lookup.
            competitor = original_open(
                path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                0o600, dir_fd=kwargs["dir_fd"],
            )
            os.close(competitor)
            if flags & os.O_CREAT and not flags & os.O_EXCL:
                # Observed on this host: nonexclusive concurrent creation can
                # return ENOENT despite the directory and winner's lock existing.
                raise FileNotFoundError(2, "concurrent nonexclusive create", path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", competing_open)
    service = _service(tmp_path)
    first = service.register_file(session_key="a", workspace_relative_path="report")
    assert collided
    assert service.list_files(session_key="a") == [first]
    assert (tmp_path / ".teamclaw/session-files/.registration.lock").is_file()
    assert service.register_file(session_key="a", workspace_relative_path="report") == first


def test_deleted_pinned_registration_directory_still_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "report").write_bytes(b"current")
    original_open = os.open

    def delete_directory(path, flags, *args, **kwargs):
        if path == ".registration.lock":
            (tmp_path / ".teamclaw/session-files").rmdir()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", delete_directory)
    with pytest.raises(SessionFileError):
        _service(tmp_path).register_file(session_key="a", workspace_relative_path="report")
    assert ManifestStore(tmp_path).list_entries() == []


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_existing_registration_lock_must_be_ordinary_file(tmp_path, kind):
    (tmp_path / "report").write_bytes(b"current")
    directory = tmp_path / ".teamclaw/session-files"
    directory.mkdir(parents=True)
    lock = directory / ".registration.lock"
    secret = tmp_path / "private"
    secret.write_bytes(b"private-bytes")
    if kind == "symlink":
        lock.symlink_to(secret)
    elif kind == "fifo":
        os.mkfifo(lock)
    else:
        lock.mkdir()
    with pytest.raises(SessionFileError):
        _service(tmp_path).register_file(session_key="a", workspace_relative_path="report")
    assert ManifestStore(tmp_path).list_entries() == []
    assert secret.read_bytes() == b"private-bytes"
