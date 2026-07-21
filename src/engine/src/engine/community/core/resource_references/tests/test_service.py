from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from engine.community.core.resource_materialization.models import hash_identifier
from engine.community.core.resource_references.service import (
    ResourceReferenceError,
    ResourceReferenceService,
)


def _write_ready_manifest(root: Path, session_key: str, content: bytes = b"hello") -> Path:
    relative = ".teamclaw/session-files/scope_abc/{}/sr_001/report.txt".format(
        hash_identifier(session_key)
    )
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    manifest = {
        "version": 1,
        "resources": {
            "sr_001": {
                "resource_id": "sr_001",
                "transfer_id": "transfer-001",
                "task_id": "task-001",
                "task_version": 1,
                "scope_key_hash": "scope_abc",
                "session_key_hash": hash_identifier(session_key),
                "filename": "report.txt",
                "relative_path": relative,
                "canonical_bot_absolute_path": str(target.resolve()),
                "size_bytes": len(content),
                "content_hash": hashlib.sha256(content).hexdigest(),
                "status": "ready",
            }
        },
    }
    manifest_path = root / ".teamclaw/session-files/.manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return target.resolve()


def test_rewrite_preserves_position_and_uses_bot_absolute_path(tmp_path: Path):
    session_key = "agent:test:session:one"
    target = _write_ready_manifest(tmp_path, session_key)
    service = ResourceReferenceService(workspace_root_provider=lambda: tmp_path)

    result = service.rewrite(
        prompt='before <file-ref insert_id="ins_1"></file-ref> after',
        session_key=session_key,
        resource_references=[
            {"resource_id": "sr_001", "insert_id": "ins_1", "type": "file"}
        ],
        prompt_file_refs=[{"resource_id": "sr_001", "insert_id": "ins_1"}],
    )

    assert result.prompt == (
        'before <file-ref name="report.txt" path="{}"></file-ref> after'.format(
            target
        )
    )
    assert result.materialized_files[0]["canonical_bot_absolute_path"] == str(target)


def test_rewrite_without_references_is_exact_pass_through(tmp_path: Path):
    service = ResourceReferenceService(workspace_root_provider=lambda: tmp_path)
    prompt = "plain chat"

    result = service.rewrite(prompt, "session", None, None)

    assert result.prompt == prompt
    assert result.materialized_files == []


def test_rewrite_rejects_cross_session_manifest(tmp_path: Path):
    _write_ready_manifest(tmp_path, "session-a")
    service = ResourceReferenceService(workspace_root_provider=lambda: tmp_path)

    with pytest.raises(ResourceReferenceError, match="cross_session_resource"):
        service.rewrite(
            '<file-ref insert_id="ins_1"></file-ref>',
            "session-b",
            [{"resource_id": "sr_001", "insert_id": "ins_1"}],
            None,
        )


def test_rewrite_rejects_caller_supplied_path(tmp_path: Path):
    _write_ready_manifest(tmp_path, "session-a")
    service = ResourceReferenceService(workspace_root_provider=lambda: tmp_path)

    with pytest.raises(ResourceReferenceError, match="caller_path_forbidden"):
        service.rewrite(
            '<file-ref insert_id="ins_1"></file-ref>',
            "session-a",
            [
                {
                    "resource_id": "sr_001",
                    "insert_id": "ins_1",
                    "workspace_path": "/etc/passwd",
                }
            ],
            None,
        )


def test_rewrite_rejects_hash_changed_file(tmp_path: Path):
    target = _write_ready_manifest(tmp_path, "session-a")
    target.write_bytes(b"HELLO")
    service = ResourceReferenceService(workspace_root_provider=lambda: tmp_path)

    with pytest.raises(ResourceReferenceError, match="content_hash_mismatch"):
        service.rewrite(
            '<file-ref insert_id="ins_1"></file-ref>',
            "session-a",
            [{"resource_id": "sr_001", "insert_id": "ins_1"}],
            None,
        )


def test_rewrite_rejects_manifest_file_replaced_by_outside_symlink(tmp_path: Path):
    target = _write_ready_manifest(tmp_path, "session-a")
    outside = tmp_path.parent / "outside-chat-file.txt"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)
    service = ResourceReferenceService(workspace_root_provider=lambda: tmp_path)

    with pytest.raises(ResourceReferenceError, match="path_mismatch"):
        service.rewrite(
            '<file-ref insert_id="ins_1"></file-ref>',
            "session-a",
            [{"resource_id": "sr_001", "insert_id": "ins_1"}],
            None,
        )
