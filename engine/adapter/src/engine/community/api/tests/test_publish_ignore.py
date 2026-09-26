"""Runtime identity and fixed-file mutation behavior."""
import json
import hashlib
import os
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.plugins import publish_ignore as service
from engine.community.di.publish_ignore_config import PublishIgnoreModule
from engine.community.core.publish_ignore.protocol import PublishIgnoreService
from injector import Injector
from fastapi_injector import attach_injector
from engine.community.api.bot.router import router
from engine.community.shared.credentials import CredentialsService


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY", raising=False)
    monkeypatch.setattr(service, "IGNORE_FILE", tmp_path / "ignore")
    credentials = tmp_path / "credentials"
    credentials.write_text("BOT_ID=bot\nENTITY_ID=entity\nVERSION=V3\nSTAGE=online\n")
    instance = CredentialsService(credentials)
    monkeypatch.setattr(service, "get_credentials_service", lambda: instance)
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([PublishIgnoreModule()]))
    return TestClient(app), credentials


def payload(path="workspace/cache", operation="add", stage="online"):
    data = {"expected_target": {"bot_id": "bot", "entity_id": "entity", "stage": stage},
            "operation": operation, "path": path, "request_id": str(uuid.uuid4())}
    return data


def test_add_without_management_key_or_signature(setup, monkeypatch):
    client, _ = setup
    monkeypatch.delenv("SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY", raising=False)
    data = payload()
    response = client.post("/api/bot/publish-ignore", json=data)
    assert response.status_code == 200
    assert service.IGNORE_FILE.read_text() == "workspace/cache\n"


@pytest.mark.parametrize("version", ["", "V1", "V3"])
def test_draft_workspace_has_no_release_version(setup, version):
    client, credentials = setup
    credentials.write_text(f"BOT_ID=bot\nENTITY_ID=entity\nSTAGE=draft\nVERSION={version}\n")
    assert client.post("/api/bot/publish-ignore", json=payload(stage="draft")).status_code == 200
    assert service.IGNORE_FILE.read_text() == "workspace/cache\n"


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("field,value", [("BOT_ID", "other"), ("ENTITY_ID", ""), ("STAGE", "")])
def test_all_stages_require_real_workspace_identity(setup, stage, field, value):
    client, credentials = setup
    identity = {"BOT_ID": "bot", "ENTITY_ID": "entity", "STAGE": stage, "VERSION": "V3"}
    identity[field] = value
    credentials.write_text("".join(f"{key}={item}\n" for key, item in identity.items()))
    assert client.post("/api/bot/publish-ignore", json=payload(stage=stage)).status_code == 409
    assert not service.IGNORE_FILE.exists()


@pytest.mark.parametrize("stage", ["verify", "online"])
@pytest.mark.parametrize("version", ["", "V1"])
def test_release_stages_do_not_require_version(setup, stage, version):
    client, credentials = setup
    credentials.write_text(f"BOT_ID=bot\nENTITY_ID=entity\nSTAGE={stage}\nVERSION={version}\n")
    assert client.post("/api/bot/publish-ignore", json=payload(stage=stage)).status_code == 200
    assert service.IGNORE_FILE.read_text() == "workspace/cache\n"


def test_add_remove_preserves_comments_and_crlf(setup):
    client, _ = setup
    service.IGNORE_FILE.write_bytes(b"# comment\r\n\r\n./workspace/cache/\r\nother\r\n")
    result = client.post("/api/bot/publish-ignore", json=payload()).json()
    assert result["data"]["changed"] is False
    result = client.post("/api/bot/publish-ignore", json=payload(operation="remove")).json()
    assert result["data"]["entry_count"] == 1
    assert service.IGNORE_FILE.read_bytes() == b"# comment\r\n\r\nother\r\n"
    assert client.post("/api/bot/publish-ignore", json=payload(operation="remove")).json()["data"]["changed"] is False


def test_add_missing_and_unterminated(setup):
    client, _ = setup
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 200
    service.IGNORE_FILE.write_bytes(b"other")
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 200
    assert service.IGNORE_FILE.read_bytes() == b"other\nworkspace/cache\n"


@pytest.mark.parametrize("path", ["../secret", "/etc", "a//b", "a/./b", "a\nb", "!a", "#a", "a*", "a\\b", "./"])
def test_invalid_paths(setup, path):
    client, _ = setup
    assert client.post("/api/bot/publish-ignore", json=payload(path)).status_code == 422
    assert not service.IGNORE_FILE.exists()


def test_mutation_and_failure_logs(setup, caplog):
    client, _ = setup
    caplog.set_level(logging.INFO)
    data = payload()
    assert client.post("/api/bot/publish-ignore", json=data).status_code == 200
    assert client.post("/api/bot/publish-ignore", json=payload("../invalid")).status_code == 422
    assert {"engine.publish_ignore.request", "engine.publish_ignore.success", "engine.publish_ignore.failure"} <= {record.message for record in caplog.records}
    for record in caplog.records:
        if record.message.startswith("engine.publish_ignore."):
            assert record.operation_data["route"] == "/api/bot/publish-ignore"
            assert record.operation_data["request_id"]
            assert record.operation_data["expected_target"]["bot_id"] == "bot"
        if record.message == "engine.publish_ignore.success":
            assert record.result["changed"] is True and record.elapsed_ms >= 0
        if record.message == "engine.publish_ignore.failure":
            assert record.error_type == "PublishIgnoreError" and record.elapsed_ms >= 0


def test_identity_mismatch(setup):
    client, credentials = setup
    credentials.write_text("BOT_ID=other\nENTITY_ID=entity\nSTAGE=online\n")
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 409
    assert not service.IGNORE_FILE.exists()


@pytest.mark.parametrize("lock", [False, True])
def test_symlink_refused(setup, tmp_path, lock):
    client, _ = setup
    victim = tmp_path / "victim"
    victim.write_text("keep")
    dest = service.IGNORE_FILE.with_name("ignore.lock") if lock else service.IGNORE_FILE
    dest.symlink_to(victim)
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 500
    assert victim.read_text() == "keep"


def test_replace_failure_keeps_original(setup, monkeypatch):
    client, _ = setup
    service.IGNORE_FILE.write_text("other\n")
    real_replace = service.os.replace
    def fail(src, dst):
        if dst == service.IGNORE_FILE:
            raise OSError("replace failed")
        return real_replace(src, dst)
    monkeypatch.setattr(service.os, "replace", fail)
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 500
    assert service.IGNORE_FILE.read_text() == "other\n"
    assert not list(service.IGNORE_FILE.parent.glob(".publish-ignore-*"))


def test_parallel_mutations_are_not_lost(setup):
    def change(index):
        return service.change(service.PublishIgnoreRequest(**payload(f"cache/{index}")))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(change, range(12)))
    assert all(result["changed"] for result in results)
    assert set(service.IGNORE_FILE.read_text().splitlines()) == {f"cache/{index}" for index in range(12)}


def test_size_nonregular_and_invalid_encoding(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(service, "MAX_BYTES", 8)
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 409
    service.IGNORE_FILE.write_bytes(b"x" * 9)
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 409
    service.IGNORE_FILE.write_bytes(b"\xff")
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 500
    service.IGNORE_FILE.unlink()
    service.IGNORE_FILE.mkdir()
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 409


def test_replay_does_not_restore_removed_entry(setup):
    client, _ = setup
    first = payload()
    assert client.post("/api/bot/publish-ignore", json=first).status_code == 200
    assert client.post("/api/bot/publish-ignore", json=payload(operation="remove")).status_code == 200
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([PublishIgnoreModule()]))
    restarted = TestClient(app)
    assert restarted.post("/api/bot/publish-ignore", json=first).status_code == 409
    assert service.IGNORE_FILE.read_bytes() == b""


@pytest.mark.parametrize("contents", ["[]", "{\"invalid\":\"expiry\"}", "invalid"])
def test_invalid_replay_journal(setup, contents):
    client, _ = setup
    service.IGNORE_FILE.with_name("ignore.requests").write_text(contents)
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 409


def test_expired_journal_entries_pruned_and_full_rejected(setup):
    client, _ = setup
    journal = service.IGNORE_FILE.with_name("ignore.requests")
    journal.write_text(json.dumps({"old": time.time() - 10}))
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 200
    assert "old" not in json.loads(journal.read_text())
    journal.write_text(json.dumps({str(index): time.time() + 200 for index in range(4096)}))
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 409


def test_unicode_separator_is_literal_filename(setup):
    client, _ = setup
    path = "cache\u2028name"
    assert client.post("/api/bot/publish-ignore", json=payload(path)).status_code == 200
    assert client.post("/api/bot/publish-ignore", json=payload(path)).json()["data"]["changed"] is False


def test_di_protocol_conformance(setup):
    implementation = Injector([PublishIgnoreModule()]).get(PublishIgnoreService)
    assert isinstance(implementation, PublishIgnoreService)
    assert implementation.change(service.PublishIgnoreRequest(**payload()))["changed"] is True


def test_lock_timeout_does_not_write(setup, monkeypatch):
    client, _ = setup
    def busy(*args):
        raise BlockingIOError()
    monkeypatch.setattr(service.fcntl, "flock", busy)
    clock = iter([0, 10])
    # Avoid replacing the process-wide time module used by HTTP logging.
    from types import SimpleNamespace
    monkeypatch.setattr(service, "time", SimpleNamespace(time=time.time, monotonic=lambda: next(clock), sleep=lambda _: None))
    assert client.post("/api/bot/publish-ignore", json=payload()).status_code == 409
    assert not service.IGNORE_FILE.exists()


def test_identity_rechecked_after_waiting_for_lock(setup, monkeypatch):
    client, credentials = setup
    data = payload()
    real_flock = service.fcntl.flock
    def locked(*args):
        result = real_flock(*args)
        credentials.write_text("BOT_ID=other\nENTITY_ID=entity\nSTAGE=online\n")
        return result
    monkeypatch.setattr(service.fcntl, "flock", locked)
    assert client.post("/api/bot/publish-ignore", json=data).status_code == 409
    assert not service.IGNORE_FILE.exists()


def query_params(stage="online"):
    return {"bot_id": "bot", "entity_id": "entity", "stage": stage, "request_id": "query-1"}


@pytest.mark.parametrize("content,paths", [
    (None, []), (b"", []),
    (b"# comment\r\n\r\n./workspace/bin/\r\nworkspace/bin\nother", ["workspace/bin", "workspace/bin", "other"]),
    ("cache\u2028name\n".encode(), ["cache\u2028name"]),
])
def test_query_snapshot_is_read_only(setup, content, paths, monkeypatch):
    client, _ = setup
    if content is not None:
        service.IGNORE_FILE.write_bytes(content)
    before = {path.name: path.read_bytes() for path in service.IGNORE_FILE.parent.iterdir()}
    real_open = service.os.open

    def read_only_open(path, flags, *args):
        assert not flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_TRUNC)
        return real_open(path, flags, *args)

    monkeypatch.setattr(service.os, "open", read_only_open)
    response = client.get("/api/bot/publish-ignore", params=query_params())
    assert response.status_code == 200
    assert response.json()["data"] == {
        "paths": paths, "entry_count": len(paths),
        "revision": hashlib.sha256(content or b"").hexdigest(),
    }
    assert before == {path.name: path.read_bytes() for path in service.IGNORE_FILE.parent.iterdir()}


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
def test_query_requires_runtime_identity(setup, stage):
    client, credentials = setup
    credentials.write_text(f"BOT_ID=bot\nENTITY_ID=entity\nSTAGE={stage}\n")
    assert client.get("/api/bot/publish-ignore", params=query_params(stage)).status_code == 200
    credentials.write_text(f"BOT_ID=other\nENTITY_ID=entity\nSTAGE={stage}\n")
    response = client.get("/api/bot/publish-ignore", params=query_params(stage))
    assert response.status_code == 409
    assert response.json()["detail"] == "RUNTIME_IDENTITY_MISMATCH"
    assert sorted(path.name for path in credentials.parent.iterdir()) == ["credentials"]


@pytest.mark.parametrize("kind,status", [("symlink", 500), ("fifo", 409), ("directory", 409),
                                         ("oversize", 409), ("utf8", 500), ("invalid", 422)])
def test_query_bad_file_is_not_empty_success(setup, monkeypatch, kind, status):
    client, credentials = setup
    if kind == "symlink":
        service.IGNORE_FILE.symlink_to(credentials)
    elif kind == "fifo":
        os.mkfifo(service.IGNORE_FILE)
    elif kind == "directory":
        service.IGNORE_FILE.mkdir()
    else:
        monkeypatch.setattr(service, "MAX_BYTES", 8)
        service.IGNORE_FILE.write_bytes({"oversize": b"x" * 9, "utf8": b"\xff", "invalid": b"../bad"}[kind])
    response = client.get("/api/bot/publish-ignore", params=query_params())
    assert response.status_code == status
    assert "paths" not in response.json()
    assert sorted(path.name for path in credentials.parent.iterdir()) == ["credentials", "ignore"]


@pytest.mark.parametrize("field,value", [("bot_id", ""), ("entity_id", ""), ("stage", "bad"), ("request_id", "")])
def test_query_validates_required_fields(setup, field, value):
    client, _ = setup
    params = query_params()
    params[field] = value
    assert client.get("/api/bot/publish-ignore", params=params).status_code == 422


@pytest.mark.parametrize("exception,code", [(OSError, "IGNORE_IO_ERROR"), (RuntimeError, "IGNORE_QUERY_FAILED")])
def test_query_logs_safe_success_and_failure(setup, caplog, monkeypatch, exception, code):
    client, _ = setup
    caplog.set_level(logging.INFO)
    assert client.get("/api/bot/publish-ignore", params=query_params(),
                      headers={"Authorization": "Bearer secret-auth-sentinel", "Cookie": "secret-cookie-sentinel"}).status_code == 200

    def fail(_):
        raise exception("secret-exception-sentinel")

    monkeypatch.setattr(service, "_read", fail)
    assert client.get("/api/bot/publish-ignore", params=query_params()).status_code == 500
    records = [record for record in caplog.records if record.message.startswith("engine.publish_ignore.query_")]
    assert {record.message for record in records} == {
        "engine.publish_ignore.query_request", "engine.publish_ignore.query_success", "engine.publish_ignore.query_failure"}
    for record in records:
        assert record.operation_data["method"] == "GET"
        assert record.operation_data["request_id"] == "query-1"
        assert record.operation_data["expected_target"] == {"bot_id": "bot", "entity_id": "entity", "stage": "online"}
        if record.message.endswith("success"):
            assert record.result["paths"] == [] and record.elapsed_ms >= 0
        if record.message.endswith("failure"):
            assert record.error_code == code and record.elapsed_ms >= 0
    logged = repr([record.__dict__ for record in caplog.records])
    assert not any(secret in logged for secret in ("secret-auth-sentinel", "secret-cookie-sentinel", "secret-exception-sentinel"))


def test_query_large_paths_only_summarized_in_logs(setup, caplog):
    client, _ = setup
    caplog.set_level(logging.INFO)
    path = "large-path-" + "x" * 4096
    service.IGNORE_FILE.write_text(path + "\n")
    response = client.get("/api/bot/publish-ignore", params=query_params())
    assert response.status_code == 200
    assert response.json()["data"]["paths"] == [path]
    record = next(record for record in caplog.records if record.message == "engine.publish_ignore.query_success")
    assert record.result == {"entry_count": 1, "revision": response.json()["data"]["revision"], "paths_omitted": True}
    assert path not in repr([record.__dict__ for record in caplog.records])
