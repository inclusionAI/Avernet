"""Integration tests for BotProfile with real filesystem I/O.

Uses tmp_path + monkeypatch so files land in a temporary directory
instead of /aidesktop or ~/.moltis.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.core.harness.services.bot_profile import BotProfile
from agentclaw.community.core.harness.documents import MarkdownDocument


class _LocalIdentityDispatcher:
    """Test dispatcher: build a real LocalDeviceFileSystem (pathlib mode) whose
    identity mapper composes the on-disk OSS-layout path under tmp_path — so the
    identity flow lands files at the real path through the real seam (no fallback)."""

    def dispatch_addressed(self, ctx, *, namespace, entity_type, entity_id, bot_id, engine_type):
        from agentclaw.community.core.services.identity_addressing import build_arca_identity_mapper
        from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem
        mapper = build_arca_identity_mapper(entity_type, entity_id, bot_id, engine_type)
        return LocalDeviceFileSystem(path_mapper=mapper)


@pytest.fixture
def bot_profile(tmp_path: Path):
    """Yield a BotProfile wired to a temporary filesystem."""
    with patch("agentclaw.community.core.workspace.path_factory.get_bolt_base_dir", return_value=tmp_path):
        from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin
        factory = WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin())
        # Bot resolves to a (dummy) device context → identity I/O routes through the
        # dispatcher's LocalDeviceFileSystem onto tmp_path (no local-OSS fallback).
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = MagicMock()
        # No bot record → resolve_engine_for_bot falls back to DEFAULT_ENGINE_TYPE (openclaw).
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bot_repo.get_by_id.return_value = None
        identity = IdentityService(
            path_factory=factory,
            publish_repo=MagicMock(),
            bot_repo=bot_repo,
            resolver=resolver,
            device_fs_dispatcher=_LocalIdentityDispatcher(),
        )
        yield BotProfile(
            identity_service=identity,
            path_factory=factory,
            skill_set_service_factory=MagicMock(),
        )


ENTITY_TYPE = "staff"
ENTITY_ID = "test_user"
BOT_ID = "test_bot"


# ── list_files ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_files_returns_valid_identity_files(bot_profile):
    files = await bot_profile.list_files(ENTITY_TYPE, ENTITY_ID, BOT_ID)
    from agentclaw.community.core.services.identity import VALID_IDENTITY_FILES
    assert files == list(VALID_IDENTITY_FILES)


# ── read / write roundtrip ──────────────────────────────────


@pytest.mark.asyncio
async def test_read_file_empty_when_not_exists(bot_profile):
    ref = await bot_profile.read_file(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md")
    assert ref.content == ""
    assert ref.file_type == "AGENTS.md"
    assert ref.checksum is not None


@pytest.mark.asyncio
async def test_write_and_read_roundtrip(bot_profile):
    doc = MarkdownDocument.parse("# Hello Bot\n\nContent here.", bot_id=BOT_ID, file_type="AGENTS.md")
    written = await bot_profile.write_file(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md", doc)
    assert "Hello Bot" in written.content

    ref = await bot_profile.read_file(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md")
    assert "Hello Bot" in ref.content
    assert ref.checksum == written.checksum


# ── read_document ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_document_returns_markdown(bot_profile):
    raw = "# Title\n\nLine 1\nLine 2\n"
    doc = MarkdownDocument.parse(raw, bot_id=BOT_ID, file_type="AGENTS.md")
    await bot_profile.write_file(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md", doc)

    read_doc = await bot_profile.read_document(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md")
    assert isinstance(read_doc, MarkdownDocument)
    assert read_doc.raw_text == raw


# ── backup / restore ────────────────────────────────────────


def test_create_backup_with_checksum(bot_profile):
    files = {"AGENTS.md": "content A", "RULES.md": "content B"}
    snap = bot_profile.create_backup(BOT_ID, "app1", ["AGENTS.md", "RULES.md"], files)

    assert snap.bot_id == BOT_ID
    assert snap.app_id == "app1"
    assert snap.files == files
    assert snap.checksum
    assert snap.id.startswith(f"{BOT_ID}_app1_")


def test_restore_backup_returns_original(bot_profile):
    files = {"AGENTS.md": "original"}
    snap = bot_profile.create_backup(BOT_ID, "app1", ["AGENTS.md"], files)
    restored = bot_profile.restore_backup(snap)
    assert restored == files


# ── diff ────────────────────────────────────────────────────


def test_generate_diff_unified_format(bot_profile):
    from agentclaw.community.core.harness.models import BotFileRef

    old = BotFileRef(
        entity_type=ENTITY_TYPE, entity_id=ENTITY_ID, bot_id=BOT_ID,
        file_type="AGENTS.md", content="line1\nline2\n",
    )
    new = BotFileRef(
        entity_type=ENTITY_TYPE, entity_id=ENTITY_ID, bot_id=BOT_ID,
        file_type="AGENTS.md", content="line1\nmodified\n",
    )
    diff = bot_profile.generate_diff(old, new)
    assert "---" in diff
    assert "+++" in diff
    assert "-line2" in diff
    assert "+modified" in diff


# ── file path resolution ────────────────────────────────────


@pytest.mark.asyncio
async def test_files_land_under_tmp_path(bot_profile, tmp_path):
    doc = MarkdownDocument.parse("# Test", bot_id=BOT_ID, file_type="AGENTS.md")
    await bot_profile.write_file(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md", doc)

    # Verify physical file exists under tmp_path
    bot_workspace = tmp_path / f"{ENTITY_TYPE}_{ENTITY_ID}" / BOT_ID / "openclaw" / "workspace"
    expected = bot_workspace / "AGENTS.md"
    assert expected.exists()
    assert "# Test" in expected.read_text(encoding="utf-8")


