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


def test_register_snapshot_session_isolation_and_source_retention(tmp_path):
    source = tmp_path / "report.txt"
    source.write_bytes(b"original")
    service = _service(tmp_path)
    view = service.register_file(
        session_key="session:a", workspace_relative_path="report.txt"
    )
    entry = ManifestStore(tmp_path).get(view.resource_id)
    assert entry.source_kind == "workspace_file"
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
    assert content.path.read_bytes() == b"original"
    assert content.path != source
    service.delete_file(session_key="session:a", resource_id=view.resource_id)
    assert source.read_bytes() == b"edited"
    assert not content.path.exists()
    assert service.list_files(session_key="session:a") == []


def test_registration_is_idempotent_by_session_path_and_content(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("one")
    service = _service(tmp_path)
    first = service.register_file(session_key="a", workspace_relative_path="report.txt")
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
    assert second.resource_id != first.resource_id
    assert len(service.list_files(session_key="a")) == 2


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


def test_snapshot_directory_swap_during_copy_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "report").write_text("original")
    real_replace = os.replace

    def replace(source, target, *args, **kwargs):
        if str(source).startswith(".register-"):
            (tmp_path / ".teamclaw").rename(tmp_path / "moved")
            (tmp_path / ".teamclaw").symlink_to(
                tmp_path / "moved", target_is_directory=True
            )
        return real_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(SessionFileError):
        _service(tmp_path).register_file(
            session_key="a", workspace_relative_path="report"
        )
    assert not (tmp_path / "moved/session-files/.manifest.json").exists()
    assert not list((tmp_path / "moved").rglob("report"))


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


@pytest.mark.parametrize("symlink", [False, True])
def test_snapshot_replacement_before_manifest_publication_is_rejected(tmp_path, monkeypatch, symlink):
    (tmp_path / "report").write_bytes(b"original")
    secret = tmp_path / "private"
    secret.write_bytes(b"external")
    real_replace = os.replace
    def swap(source, target, *args, **kwargs):
        result = real_replace(source, target, *args, **kwargs)
        if str(source).startswith(".register-"):
            parent = kwargs["dst_dir_fd"]
            os.unlink(target, dir_fd=parent)
            if symlink:
                os.symlink(secret, target, dir_fd=parent)
            else:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(b"replaced")
        return result
    monkeypatch.setattr(os, "replace", swap)
    with pytest.raises(SessionFileError):
        _service(tmp_path).register_file(session_key="a", workspace_relative_path="report")
    assert ManifestStore(tmp_path).list_entries() == []
    assert secret.read_bytes() == b"external"
