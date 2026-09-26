"""Unit tests for PatchEngine update_file op (skill SKILL.md patches)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.harness.models import (
    PatchDefinition,
    PatchOperation,
    PatchRecord,
    PatchStatus,
    PatchTarget,
)
from agentclaw.community.core.harness.services.patch_engine import PatchEngine


ENTITY_TYPE = "staff"
ENTITY_ID = "test_user"
BOT_ID = "test_bot"


@pytest.fixture
def tmp_skill_path(tmp_path: Path) -> Path:
    """Create a dummy SKILL.md file and return its absolute path."""
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: test-skill\ndescription: Test\n---\n\n## Usage\n\nHello world.\n")
    return skill_file


@pytest.fixture
def identity_mock(tmp_path: Path):
    """IdentityService mock that does real file I/O against tmp_path."""
    mock = MagicMock()

    async def read_file(file_path: Path, bot_id=None, owner_id=None):
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return ""

    async def write_file(file_path: Path, content: str, bot_id=None, owner_id=None):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    mock.read_file = read_file
    mock.write_file = write_file
    return mock


@pytest.fixture
def bot_profile_mock():
    mock = MagicMock()
    return mock


@pytest.fixture
def patch_engine(bot_profile_mock, identity_mock):
    return PatchEngine(
        bot_profile=bot_profile_mock,
        scanner=MagicMock(),
        db=MagicMock(),
        patch_record_repo=MagicMock(),
        identity_service=identity_mock,
    )


def _make_update_file_op(target: str, src: str, dst: str) -> PatchOperation:
    return PatchOperation(
        op="update_file",
        target=target,
        template="",
        detail={"src_content": src, "dst_content": dst},
    )


def _make_record() -> PatchRecord:
    return PatchRecord(
        bot_id=BOT_ID,
        entity_id=ENTITY_ID,
        patch_id=1,
        layer=1,
        target=PatchTarget(files=[]),
        status=PatchStatus.PLANNED,
    )


# ── preview ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_update_file(patch_engine):
    op = _make_update_file_op("/tmp/skill.md", "old", "new")
    file_type, results = await patch_engine.preview(
        ENTITY_TYPE, ENTITY_ID, BOT_ID, [op],
    )
    assert len(results) == 1
    op_type, target, diff_text, content = results[0]
    assert op_type == "update_file"
    assert "old" in diff_text
    assert "new" in diff_text
    assert content == "new"


# ── apply ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_update_file_writes_dst_content(patch_engine, tmp_skill_path, bot_profile_mock):
    src_content = tmp_skill_path.read_text()
    dst_content = src_content + "\n## New Section\n\nPatched content.\n"
    op = _make_update_file_op(str(tmp_skill_path), src_content, dst_content)
    record = _make_record()

    bot_profile_mock.create_backup = MagicMock(
        return_value=MagicMock(checksum="abc", affected_types=[]),
    )

    result = await patch_engine.apply(
        ENTITY_TYPE, ENTITY_ID, BOT_ID, record, [op],
    )

    assert result.status == PatchStatus.APPLIED
    assert tmp_skill_path.read_text() == dst_content


@pytest.mark.asyncio
async def test_apply_update_file_saves_backup(patch_engine, tmp_skill_path, bot_profile_mock):
    src_content = tmp_skill_path.read_text()
    dst_content = src_content + "\n## Patched\n"
    op = _make_update_file_op(str(tmp_skill_path), src_content, dst_content)
    record = _make_record()

    bot_profile_mock.create_backup = MagicMock(
        return_value=MagicMock(checksum="abc", affected_types=[]),
    )

    await patch_engine.apply(ENTITY_TYPE, ENTITY_ID, BOT_ID, record, [op])

    backup_content = json.loads(record.backup_content)
    assert str(tmp_skill_path) in backup_content
    assert backup_content[str(tmp_skill_path)] == src_content


# ── rollback_by_patch ────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollback_update_file_restores_src(patch_engine, tmp_skill_path, bot_profile_mock):
    src_content = tmp_skill_path.read_text()
    dst_content = src_content + "\n## Patched\n"
    op = _make_update_file_op(str(tmp_skill_path), src_content, dst_content)
    record = _make_record()

    bot_profile_mock.create_backup = MagicMock(
        return_value=MagicMock(checksum="abc", affected_types=[]),
    )

    # Apply first
    await patch_engine.apply(ENTITY_TYPE, ENTITY_ID, BOT_ID, record, [op])
    assert tmp_skill_path.read_text() == dst_content

    # Then rollback
    patch_def = PatchDefinition(
        template_id=1,
        name="test",
        layer=1,
        content=json.dumps([{
            "op": "update_file",
            "target": str(tmp_skill_path),
            "template": "",
            "detail": {"src_content": src_content, "dst_content": dst_content},
        }]),
    )
    success, msg = await patch_engine.rollback_by_patch(
        ENTITY_TYPE, ENTITY_ID, BOT_ID, patch_def,
    )
    assert success, f"Rollback failed: {msg}"
    assert tmp_skill_path.read_text() == src_content


@pytest.mark.asyncio
async def test_rollback_update_file_rejects_when_modified(patch_engine, tmp_skill_path, bot_profile_mock):
    src_content = tmp_skill_path.read_text()
    dst_content = src_content + "\n## Patched\n"
    op = _make_update_file_op(str(tmp_skill_path), src_content, dst_content)
    record = _make_record()

    bot_profile_mock.create_backup = MagicMock(
        return_value=MagicMock(checksum="abc", affected_types=[]),
    )

    await patch_engine.apply(ENTITY_TYPE, ENTITY_ID, BOT_ID, record, [op])

    # Modify file externally (simulate concurrent edit)
    tmp_skill_path.write_text("externally modified content")

    patch_def = PatchDefinition(
        template_id=1,
        name="test",
        layer=1,
        content=json.dumps([{
            "op": "update_file",
            "target": str(tmp_skill_path),
            "template": "",
            "detail": {"src_content": src_content, "dst_content": dst_content},
        }]),
    )
    success, msg = await patch_engine.rollback_by_patch(
        ENTITY_TYPE, ENTITY_ID, BOT_ID, patch_def,
    )
    assert not success
    assert "changed" in msg.lower()