"""Management authorization and fixed-file mutation behavior."""
import base64
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from engine.community.plugins import publish_ignore as service
from engine.community.di.publish_ignore_config import PublishIgnoreModule, publish_ignore_public_key
from engine.community.core.publish_ignore.protocol import PublishIgnoreService
from injector import Injector
from fastapi_injector import attach_injector
from engine.community.api.bot.router import router
from engine.community.shared.credentials import CredentialsService


@pytest.fixture
def setup(tmp_path, monkeypatch):
    signing_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv("SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY", signing_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode())
    monkeypatch.setattr(service, "IGNORE_FILE", tmp_path / "ignore")
    credentials = tmp_path / "credentials"
    credentials.write_text("BOT_ID=bot\nENTITY_ID=entity\nVERSION=V3\nSTAGE=online\n")
    instance = CredentialsService(credentials)
    monkeypatch.setattr(service, "get_credentials_service", lambda: instance)
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([PublishIgnoreModule()]))
    return TestClient(app), signing_key, credentials


def payload(secret, path="workspace/cache", operation="add", stage="online"):
    data = {"expected_target": {"bot_id": "bot", "entity_id": "entity", "stage": stage},
            "operation": operation, "path": path, "request_id": str(uuid.uuid4())}
    timestamp = int(time.time())
    encoded = json.dumps({**data, "timestamp": timestamp}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    data["authorization"] = {"timestamp": timestamp, "signature": base64.b64encode(secret.sign(encoded)).decode()}
    return data


@pytest.mark.parametrize("version", ["", "V1", "V3"])
def test_draft_workspace_has_no_release_version(setup, version):
    client, signing_key, credentials = setup
    credentials.write_text(f"BOT_ID=bot\nENTITY_ID=entity\nSTAGE=draft\nVERSION={version}\n")
    assert client.post("/api/bot/publish-ignore", json=payload(signing_key, stage="draft")).status_code == 200
    assert service.IGNORE_FILE.read_text() == "workspace/cache\n"


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("field,value", [("BOT_ID", "other"), ("ENTITY_ID", ""), ("STAGE", "")])
def test_all_stages_require_real_workspace_identity(setup, stage, field, value):
    client, signing_key, credentials = setup
    identity = {"BOT_ID": "bot", "ENTITY_ID": "entity", "STAGE": stage, "VERSION": "V3"}
    identity[field] = value
    credentials.write_text("".join(f"{key}={item}\n" for key, item in identity.items()))
    assert client.post("/api/bot/publish-ignore", json=payload(signing_key, stage=stage)).status_code == 409
    assert not service.IGNORE_FILE.exists()


@pytest.mark.parametrize("stage", ["verify", "online"])
@pytest.mark.parametrize("version", ["", "V1"])
def test_release_stages_do_not_require_version(setup, stage, version):
    client, signing_key, credentials = setup
    credentials.write_text(f"BOT_ID=bot\nENTITY_ID=entity\nSTAGE={stage}\nVERSION={version}\n")
    assert client.post("/api/bot/publish-ignore", json=payload(signing_key, stage=stage)).status_code == 200
    assert service.IGNORE_FILE.read_text() == "workspace/cache\n"


def test_add_remove_preserves_comments_and_crlf(setup):
    client, secret, _ = setup
    service.IGNORE_FILE.write_bytes(b"# comment\r\n\r\n./workspace/cache/\r\nother\r\n")
    result = client.post("/api/bot/publish-ignore", json=payload(secret)).json()
    assert result["data"]["changed"] is False
    result = client.post("/api/bot/publish-ignore", json=payload(secret, operation="remove")).json()
    assert result["data"]["entry_count"] == 1
    assert service.IGNORE_FILE.read_bytes() == b"# comment\r\n\r\nother\r\n"
    assert client.post("/api/bot/publish-ignore", json=payload(secret, operation="remove")).json()["data"]["changed"] is False


def test_add_missing_and_unterminated(setup):
    client, secret, _ = setup
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 200
    service.IGNORE_FILE.write_bytes(b"other")
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 200
    assert service.IGNORE_FILE.read_bytes() == b"other\nworkspace/cache\n"


@pytest.mark.parametrize("path", ["../secret", "/etc", "a//b", "a/./b", "a\nb", "!a", "#a", "a*", "a\\b", "./"])
def test_invalid_paths(setup, path):
    client, secret, _ = setup
    assert client.post("/api/bot/publish-ignore", json=payload(secret, path)).status_code == 422
    assert not service.IGNORE_FILE.exists()


def test_authentication_and_logs(setup, caplog, monkeypatch):
    client, secret, _ = setup
    caplog.set_level(logging.INFO)
    data = payload(secret)
    signatures = [data["authorization"]["signature"]]
    assert client.post("/api/bot/publish-ignore", json=data).status_code == 200
    data["path"] = "tampered"
    assert client.post("/api/bot/publish-ignore", json=data).status_code == 403
    data = payload(secret)
    signatures.append(data["authorization"]["signature"])
    data["authorization"]["timestamp"] -= 1000
    assert client.post("/api/bot/publish-ignore", json=data).status_code == 403
    monkeypatch.delenv("SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY")
    with pytest.raises(service.PublishIgnoreError):
        Injector([PublishIgnoreModule()]).get(PublishIgnoreService).change(service.PublishIgnoreRequest(**payload(secret)))
    assert all(signature not in repr(record.__dict__) for signature in signatures for record in caplog.records)
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
    client, secret, credentials = setup
    credentials.write_text("BOT_ID=other\nENTITY_ID=entity\nSTAGE=online\n")
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 409
    assert not service.IGNORE_FILE.exists()


@pytest.mark.parametrize("lock", [False, True])
def test_symlink_refused(setup, tmp_path, lock):
    client, secret, _ = setup
    victim = tmp_path / "victim"
    victim.write_text("keep")
    dest = service.IGNORE_FILE.with_name("ignore.lock") if lock else service.IGNORE_FILE
    dest.symlink_to(victim)
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 500
    assert victim.read_text() == "keep"


def test_replace_failure_keeps_original(setup, monkeypatch):
    client, secret, _ = setup
    service.IGNORE_FILE.write_text("other\n")
    real_replace = service.os.replace
    def fail(src, dst):
        if dst == service.IGNORE_FILE:
            raise OSError("replace failed")
        return real_replace(src, dst)
    monkeypatch.setattr(service.os, "replace", fail)
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 500
    assert service.IGNORE_FILE.read_text() == "other\n"
    assert not list(service.IGNORE_FILE.parent.glob(".publish-ignore-*"))


def test_parallel_mutations_are_not_lost(setup):
    _, secret, _ = setup
    def change(index):
        return service.change(service.PublishIgnoreRequest(**payload(secret, f"cache/{index}")), publish_ignore_public_key())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(change, range(12)))
    assert all(result["changed"] for result in results)
    assert set(service.IGNORE_FILE.read_text().splitlines()) == {f"cache/{index}" for index in range(12)}


def test_size_nonregular_and_invalid_encoding(setup, monkeypatch):
    client, secret, _ = setup
    monkeypatch.setattr(service, "MAX_BYTES", 8)
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 409
    service.IGNORE_FILE.write_bytes(b"x" * 9)
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 409
    service.IGNORE_FILE.write_bytes(b"\xff")
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 500
    service.IGNORE_FILE.unlink()
    service.IGNORE_FILE.mkdir()
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 409


def test_replay_does_not_restore_removed_entry(setup):
    client, secret, _ = setup
    first = payload(secret)
    assert client.post("/api/bot/publish-ignore", json=first).status_code == 200
    assert client.post("/api/bot/publish-ignore", json=payload(secret, operation="remove")).status_code == 200
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([PublishIgnoreModule()]))
    restarted = TestClient(app)
    assert restarted.post("/api/bot/publish-ignore", json=first).status_code == 409
    assert service.IGNORE_FILE.read_bytes() == b""


@pytest.mark.parametrize("contents", ["[]", "{\"invalid\":\"expiry\"}", "invalid"])
def test_invalid_replay_journal(setup, contents):
    client, secret, _ = setup
    service.IGNORE_FILE.with_name("ignore.requests").write_text(contents)
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 409


def test_expired_journal_entries_pruned_and_full_rejected(setup):
    client, secret, _ = setup
    journal = service.IGNORE_FILE.with_name("ignore.requests")
    journal.write_text(json.dumps({"old": time.time() - 10}))
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 200
    assert "old" not in json.loads(journal.read_text())
    journal.write_text(json.dumps({str(index): time.time() + 200 for index in range(4096)}))
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 409


def test_unicode_separator_is_literal_filename(setup):
    client, secret, _ = setup
    path = "cache\u2028name"
    assert client.post("/api/bot/publish-ignore", json=payload(secret, path)).status_code == 200
    assert client.post("/api/bot/publish-ignore", json=payload(secret, path)).json()["data"]["changed"] is False


def test_di_protocol_conformance(setup):
    _, secret, _ = setup
    implementation = Injector([PublishIgnoreModule()]).get(PublishIgnoreService)
    assert isinstance(implementation, PublishIgnoreService)
    assert implementation.change(service.PublishIgnoreRequest(**payload(secret)))["changed"] is True


def test_lock_timeout_does_not_write(setup, monkeypatch):
    client, secret, _ = setup
    def busy(*args):
        raise BlockingIOError()
    monkeypatch.setattr(service.fcntl, "flock", busy)
    clock = iter([0, 10])
    # Avoid replacing the process-wide time module used by HTTP logging.
    from types import SimpleNamespace
    monkeypatch.setattr(service, "time", SimpleNamespace(time=time.time, monotonic=lambda: next(clock), sleep=lambda _: None))
    assert client.post("/api/bot/publish-ignore", json=payload(secret)).status_code == 409
    assert not service.IGNORE_FILE.exists()


def test_signature_expires_while_waiting_for_lock(setup, monkeypatch):
    client, secret, _ = setup
    data = payload(secret)
    real_flock = service.fcntl.flock
    from types import SimpleNamespace
    def locked(*args):
        result = real_flock(*args)
        monkeypatch.setattr(service, "time", SimpleNamespace(time=lambda: data["authorization"]["timestamp"] + 301))
        return result
    monkeypatch.setattr(service.fcntl, "flock", locked)
    assert client.post("/api/bot/publish-ignore", json=data).status_code == 403
    assert not service.IGNORE_FILE.exists()
