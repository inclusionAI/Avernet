"""Fixed-file publish exclusions; no request can select a filesystem destination.

Backend authorizes Bot managers and signs the exact mutation with its private
key. Engine holds only the public verification key, never signing authority.
"""
from __future__ import annotations

import fcntl
import hashlib
import base64
import binascii
import json
import os
import stat
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from engine.community.shared.credentials import get_credentials_service
from engine.community.core.publish_ignore.models import ExpectedTarget, PublishIgnoreRequest, PublishIgnoreError

IGNORE_FILE = Path("/home/admin/.service_bot_publish_ignore")
MAX_BYTES = 1024 * 1024


def authorize(request: PublishIgnoreRequest, public_key: str) -> None:
    """Verify a time-bounded Backend signature, never a user-supplied role."""
    payload = request.model_dump(exclude={"authorization"})
    payload["timestamp"] = request.authorization.timestamp
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    # COSEC: Engine holds only a public key, never Backend signing authority.
    if not public_key or abs(time.time() - request.authorization.timestamp) > 300:
        raise PublishIgnoreError(403, "MANAGEMENT_AUTH_REQUIRED")
    try:
        key = load_pem_public_key(public_key.encode())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("wrong key type")
        key.verify(base64.b64decode(request.authorization.signature, validate=True), encoded)
    except (ValueError, InvalidSignature, binascii.Error) as exc:
        raise PublishIgnoreError(403, "MANAGEMENT_AUTH_REQUIRED") from exc


def normalize_path(value: str) -> str:
    """Match literal source-root paths understood by service_bot_transition.sh."""
    # COSEC: forbid line injection and glob semantics in the installer manifest.
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PublishIgnoreError(422, "INVALID_IGNORE_PATH")
    while value.startswith("./"):
        value = value[2:]
    value = value.rstrip("/")
    if (not value or value.startswith(("/", "#", "!"))
            or any(char in value for char in "*?[]\\")
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise PublishIgnoreError(422, "INVALID_IGNORE_PATH")
    return value


def verify_identity(target: ExpectedTarget) -> None:
    """Reload the Bot/entity/stage identity of the selected runtime."""
    service = get_credentials_service()
    service.reload()
    creds = service.get_all()
    # COSEC: require actual bot/entity/stage, with no request/env fallback.
    if (creds.bot_id != target.bot_id or creds.entity_id != target.entity_id
            or creds.stage != target.stage):
        raise PublishIgnoreError(409, "RUNTIME_IDENTITY_MISMATCH")


def _open_regular(path: Path, flags: int) -> int:
    # COSEC: no symlink following; nonblocking open also avoids FIFO hangs.
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PublishIgnoreError(409, "UNSAFE_IGNORE_FILE")
    return fd


def _read(path: Path) -> bytes:
    try:
        fd = _open_regular(path, os.O_RDONLY)
    except FileNotFoundError:
        return b""
    with os.fdopen(fd, "rb") as stream:
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise PublishIgnoreError(409, "IGNORE_FILE_TOO_LARGE")
    return content


def _replace(path: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".publish-ignore-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _consume_request(request: PublishIgnoreRequest) -> None:
    """Persist replay protection under the mutation lock before any file write."""
    path = IGNORE_FILE.with_name(IGNORE_FILE.name + ".requests")
    try:
        journal = json.loads(_read(path) or b"{}")
        if not isinstance(journal, dict) or any(not isinstance(value, (int, float)) for value in journal.values()):
            raise ValueError("invalid journal")
    except (ValueError, UnicodeError) as exc:
        raise PublishIgnoreError(409, "INVALID_REQUEST_JOURNAL") from exc
    now = time.time()
    journal = {key: expiry for key, expiry in journal.items() if expiry >= now}
    if request.request_id in journal:
        raise PublishIgnoreError(409, "REQUEST_ALREADY_CONSUMED")
    if len(journal) >= 4096:
        raise PublishIgnoreError(409, "REQUEST_JOURNAL_FULL")
    journal[request.request_id] = request.authorization.timestamp + 300
    _replace(path, json.dumps(journal, sort_keys=True).encode())


def change(request: PublishIgnoreRequest, public_key: str) -> dict:
    """Serialize updates, preserve unrelated bytes, atomically install new rules.

    The fixed sibling lock survives replacements. Lock acquisition is bounded;
    successful updates affect the next installation, not an in-progress copy.
    """
    normalized = normalize_path(request.path)
    lock_fd = _open_regular(IGNORE_FILE.with_name(IGNORE_FILE.name + ".lock"), os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PublishIgnoreError(409, "IGNORE_LOCK_BUSY")
                time.sleep(0.05)
        verify_identity(request.expected_target)
        original = _read(IGNORE_FILE)
        text = original.decode("utf-8")
        # The shell reader splits only LF, not Unicode line separators.
        chunks = text.split("\n")
        lines = [chunk + "\n" for chunk in chunks[:-1]]
        if chunks[-1]:
            lines.append(chunks[-1])
        matches = []
        entries = 0
        for index, line in enumerate(lines):
            rule = line.rstrip("\r\n")
            if rule and not rule.startswith("#"):
                entries += 1
                if normalize_path(rule) == normalized:
                    matches.append(index)
        updated = original
        if request.operation == "add" and not matches:
            updated = original + (b"\n" if original and not original.endswith(b"\n") else b"") + normalized.encode() + b"\n"
            entries += 1
        elif request.operation == "remove" and matches:
            updated = "".join(line for index, line in enumerate(lines) if index not in matches).encode()
            entries -= len(matches)
        if len(updated) > MAX_BYTES:
            raise PublishIgnoreError(409, "IGNORE_FILE_TOO_LARGE")
        # Revalidate authorization after waiting for the lock and consume once.
        authorize(request, public_key)
        _consume_request(request)
        if updated != original:
            verify_identity(request.expected_target)
            _replace(IGNORE_FILE, updated)
        return {"changed": updated != original, "entry_count": entries,
                "revision": hashlib.sha256(updated).hexdigest(), "path": normalized}
    finally:
        os.close(lock_fd)


class FilePublishIgnoreService:
    """Production implementation of the fixed-file mutation protocol."""
    def __init__(self, public_key: str):
        self.public_key = public_key

    def change(self, request: PublishIgnoreRequest) -> dict:
        authorize(request, self.public_key)
        return change(request, self.public_key)
