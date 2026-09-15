from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile

import pytest

from clawevolve_diagnose.acquisition.service_export import (
    EXPORT_WAIT_TIMEOUT_SECONDS,
    ServiceSessionExportError,
    acquire_exported_sessions,
    _safe_extract_and_validate,
    _select_and_persist_sessions,
)
from clawevolve_diagnose.acquisition import service_export


def test_service_export_wait_timeout_is_30_minutes() -> None:
    assert EXPORT_WAIT_TIMEOUT_SECONDS == 30 * 60


def _session_bytes(session_id: str, created_at: str, prompt: str) -> bytes:
    events = [
        {"sessionId": session_id, "timestamp": created_at, "role": "user", "content": prompt},
        {"sessionId": session_id, "timestamp": created_at, "role": "assistant", "content": "done"},
    ]
    return ("\n".join(json.dumps(item) for item in events) + "\n").encode()


def _archive(tmp_path: Path, files: list[tuple[str, bytes, str]]) -> Path:
    manifest_files = []
    for archive_path, data, alias in files:
        manifest_files.append(
            {
                "archivePath": archive_path,
                "sessionId": Path(archive_path).name.split(".jsonl", 1)[0],
                "sourceFileName": Path(archive_path).name,
                "isDeletedArchive": ".deleted." in archive_path,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "sourceAlias": alias,
                "duplicateAliases": [],
            }
        )
    manifest = {"schemaVersion": "session-package/v1", "files": manifest_files}
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        raw = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(raw)
        handle.addfile(info, io.BytesIO(raw))
        for archive_path, data, _ in files:
            info = tarfile.TarInfo(archive_path)
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
    return archive


def test_service_export_filters_non_main_and_keeps_newest_sessions(tmp_path: Path) -> None:
    files = [
        ("sessions/s1.jsonl", _session_bytes("s1", "2026-08-25T01:00:00Z", "one"), "/agent/main"),
        ("sessions/s2.jsonl", _session_bytes("s2", "2026-08-25T02:00:00Z", "two"), "/agent/main"),
        ("sessions/helper.jsonl", _session_bytes("helper", "2026-08-25T03:00:00Z", "helper"), "/agent/bench-runner"),
    ]
    archive = _archive(tmp_path, files)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    manifest = _safe_extract_and_validate(archive, extracted)
    persisted = tmp_path / "input" / "sessions"
    persisted.mkdir(parents=True)

    rows, audit = _select_and_persist_sessions(
        manifest=manifest,
        extracted=extracted,
        sessions_dir=persisted,
        max_sessions=1,
        since="",
        until="",
    )

    assert [row.session_id for row in rows] == ["s2"]
    assert Path(rows[0].path).is_file()
    reasons = {entry.get("reason") for entry in audit["entries"]}
    assert "non_main_agent" in reasons
    assert "max_sessions" in reasons


def test_service_export_rejects_symlink_archive_entry(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("sessions/escape.jsonl")
        info.type = tarfile.SYMTYPE
        info.linkname = "/tmp/outside"
        handle.addfile(info)
    extracted = tmp_path / "extracted"
    extracted.mkdir()

    with pytest.raises(ServiceSessionExportError, match="UNSAFE_ENTRY"):
        _safe_extract_and_validate(archive, extracted)


def test_service_export_accepts_explicit_sessions_directory(tmp_path: Path) -> None:
    data = _session_bytes("s1", "2026-08-25T02:00:00Z", "one")
    manifest = {
        "schemaVersion": "session-package/v1",
        "files": [{
            "archivePath": "sessions/s1.jsonl",
            "sessionId": "s1",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "sourceAlias": "/agent/main",
        }],
    }
    archive = tmp_path / "with-directory.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        directory = tarfile.TarInfo("sessions/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        handle.addfile(directory)
        raw = json.dumps(manifest).encode()
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(raw)
        handle.addfile(manifest_info, io.BytesIO(raw))
        session_info = tarfile.TarInfo("sessions/s1.jsonl")
        session_info.size = len(data)
        handle.addfile(session_info, io.BytesIO(data))

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    assert _safe_extract_and_validate(archive, extracted) == manifest


def test_service_export_calls_public_api_and_freezes_one_source_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive(
        tmp_path,
        [("sessions/s1.jsonl", _session_bytes("s1", "2026-08-25T02:00:00Z", "one"), "/agent/main")],
    )
    calls: list[tuple[str, str, dict[str, str]]] = []

    def request_json(url: str, *, method: str, body=None, headers=None):
        calls.append((url, method, headers or {}))
        if method == "POST":
            assert body["target"] == {
                "userId": "197444",
                "botId": "bot-1",
                "stage": "service",
                "engineType": "openclaw",
            }
            return {
                "apiVersion": "session-export/v1",
                "exportId": "SE-1",
                "status": "succeeded",
                "exportScope": "bot",
            }
        return {
            "apiVersion": "session-export/v1",
            "exportId": "SE-1",
            "status": "succeeded",
            "exportScope": "bot",
            "target": {
                "userId": "197444",
                "botId": "bot-1",
                "stage": "service",
            },
            "resolution": {"fileCount": 1},
            "artifact": {
                "downloadUrl": "https://oss.example/source.tar.gz",
                "size": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            },
        }

    def download_artifact(*, destination: Path, **_kwargs) -> None:
        shutil.copyfile(archive, destination)

    monkeypatch.setattr(service_export, "_request_json", request_json)
    monkeypatch.setattr(service_export, "_download_artifact", download_artifact)
    monkeypatch.setattr(service_export.time, "sleep", lambda _seconds: None)

    acquired = acquire_exported_sessions(
        clawweb_url="https://clawweb.example",
        task_id="EV-1",
        step_id="STEP-1",
        source_user_id="197444",
        source_bot_id="bot-1",
        download_network="office",
        input_dir=tmp_path / "task" / "diagnose" / "input",
        max_sessions=10,
    )

    assert [row.session_id for row in acquired.rows] == ["s1"]
    assert calls[0][0] == "https://clawweb.example/api/integrations/v1/session-exports"
    assert calls[0][2]["Idempotency-Key"] == (
        "clawevolve-diagnose:EV-1:STEP-1:service-session-export:v1"
    )
    assert calls[1][0].endswith("/SE-1?downloadNetwork=office")
    source_path = tmp_path / "task" / "diagnose" / "input" / "session-source" / "source.json"
    source = json.loads(source_path.read_text())
    assert source["source"] == {"userId": "197444", "botId": "bot-1", "stage": "service"}
    assert "downloadUrl" not in json.dumps(source)
    raw_path = (
        tmp_path
        / "task"
        / "diagnose"
        / "input"
        / "session-source"
        / "raw-sessions"
        / "s1.jsonl"
    )
    assert raw_path.read_bytes() == _session_bytes(
        "s1", "2026-08-25T02:00:00Z", "one"
    )
    acquisition = json.loads(
        (
            tmp_path
            / "task"
            / "diagnose"
            / "input"
            / "session-source"
            / "acquisition-manifest.json"
        ).read_text()
    )
    assert acquisition["entries"][0]["rawPath"] == str(raw_path)


def test_service_export_freezes_unparseable_session_for_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_session = b'{"unsupported":"service-session-shape"}\n'
    archive = _archive(
        tmp_path,
        [("sessions/unparseable.jsonl", raw_session, "/agent/main")],
    )

    def request_json(_url: str, *, method: str, **_kwargs):
        if method == "POST":
            return {
                "apiVersion": "session-export/v1",
                "exportId": "SE-unparseable",
                "status": "succeeded",
                "exportScope": "bot",
            }
        return {
            "apiVersion": "session-export/v1",
            "exportId": "SE-unparseable",
            "status": "succeeded",
            "exportScope": "bot",
            "target": {"userId": "197444", "botId": "bot-1", "stage": "service"},
            "artifact": {
                "downloadUrl": "https://oss.example/source.tar.gz",
                "size": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            },
        }

    monkeypatch.setattr(service_export, "_request_json", request_json)
    monkeypatch.setattr(
        service_export,
        "_download_artifact",
        lambda *, destination, **_kwargs: shutil.copyfile(archive, destination),
    )

    acquired = acquire_exported_sessions(
        clawweb_url="https://clawweb.example",
        task_id="EV-unparseable",
        step_id="STEP-unparseable",
        source_user_id="197444",
        source_bot_id="bot-1",
        download_network="office",
        input_dir=tmp_path / "diagnose" / "input",
        max_sessions=10,
    )

    assert [row.session_id for row in acquired.rows] == ["unparseable"]
    assert acquired.rows[0].first_question == ""
    assert Path(acquired.rows[0].path).read_bytes() == raw_session
    raw_path = (
        tmp_path
        / "diagnose"
        / "input"
        / "session-source"
        / "raw-sessions"
        / "unparseable.jsonl"
    )
    assert raw_path.read_bytes() == raw_session
    acquisition = json.loads(
        (
            tmp_path
            / "diagnose"
            / "input"
            / "session-source"
            / "acquisition-manifest.json"
        ).read_text()
    )
    assert acquisition["entries"][0]["status"] == "selected"
    assert acquisition["entries"][0]["rawPath"] == str(raw_path)


def test_service_export_agent_mode_never_calls_content_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _session_bytes("agent-locator", "2026-08-25T02:00:00Z", "one")
    archive = _archive(
        tmp_path,
        [("sessions/agent-locator.jsonl", data, "/agent/main")],
    )
    extracted = tmp_path / "extracted-agent"
    extracted.mkdir()
    manifest = _safe_extract_and_validate(archive, extracted)
    persisted = tmp_path / "input-agent" / "sessions"
    persisted.mkdir(parents=True)
    monkeypatch.setattr(
        service_export,
        "parse_jsonl_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Agent Judge must not parse Session content")
        ),
    )

    rows, audit = _select_and_persist_sessions(
        manifest=manifest,
        extracted=extracted,
        sessions_dir=persisted,
        max_sessions=1,
        since="",
        until="",
        parse_content=False,
    )

    assert [row.session_id for row in rows] == ["agent-locator"]
    assert rows[0].first_question == ""
    assert audit["contentParsing"] == "disabled"


def test_service_export_reentry_reads_terminal_detail_after_compact_create_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive(
        tmp_path,
        [("sessions/s1.jsonl", _session_bytes("s1", "2026-08-25T02:00:00Z", "one"), "/agent/main")],
    )
    responses = iter([
        {
            "apiVersion": "session-export/v1",
            "exportId": "SE-terminal",
            "status": "succeeded",
            "exportScope": "bot",
        },
        {
            "apiVersion": "session-export/v1",
            "exportId": "SE-terminal",
            "status": "succeeded",
            "exportScope": "bot",
            "target": {"userId": "197444", "botId": "bot-1", "stage": "service"},
            "artifact": {
                "downloadUrl": "https://oss.example/source.tar.gz",
                "size": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            },
        },
    ])
    calls: list[str] = []

    def request_json(url: str, **_kwargs):
        calls.append(url)
        return next(responses)

    monkeypatch.setattr(service_export, "_request_json", request_json)
    monkeypatch.setattr(
        service_export,
        "_download_artifact",
        lambda *, destination, **_kwargs: shutil.copyfile(archive, destination),
    )

    acquired = acquire_exported_sessions(
        clawweb_url="https://clawweb.example",
        task_id="EV-terminal",
        step_id="STEP-terminal",
        source_user_id="197444",
        source_bot_id="bot-1",
        download_network="office",
        input_dir=tmp_path / "diagnose" / "input",
        max_sessions=10,
    )

    assert [row.session_id for row in acquired.rows] == ["s1"]
    assert len(calls) == 2
    assert calls[1].endswith("/SE-terminal?downloadNetwork=office")


def test_service_export_treats_missing_session_files_as_empty_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = iter([
        {
            "apiVersion": "session-export/v1",
            "exportId": "SE-empty",
            "status": "failed",
            "exportScope": "bot",
        },
        {
            "apiVersion": "session-export/v1",
            "exportId": "SE-empty",
            "status": "failed",
            "exportScope": "bot",
            "target": {"userId": "197444", "botId": "bot-1", "stage": "service"},
            "error": {"code": "SESSION_FILE_NOT_FOUND", "message": "none"},
        },
    ])
    monkeypatch.setattr(service_export, "_request_json", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(service_export.time, "sleep", lambda _seconds: None)

    acquired = acquire_exported_sessions(
        clawweb_url="https://clawweb.example",
        task_id="EV-empty",
        step_id="STEP-empty",
        source_user_id="197444",
        source_bot_id="bot-1",
        download_network="office",
        input_dir=tmp_path / "diagnose" / "input",
        max_sessions=10,
    )

    assert acquired.rows == []
    acquisition = json.loads((tmp_path / "diagnose" / "input" / "session-source" / "acquisition-manifest.json").read_text())
    assert acquisition["selectedCount"] == 0
    assert acquisition["warning"] == "SESSION_FILE_NOT_FOUND"
