"""End-to-end tests for PatchEngine on local filesystem (single box).

bot_repo=MagicMock → get_device_info returns non-'arca' → IdentityService
uses local read_text/write_text under tmp_path. db is unused by FS path.
"""
from __future__ import annotations

import json

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.core.harness.services.bot_profile import BotProfile
from agentclaw.community.core.harness.services.patch_engine import PatchEngine, PatchEngineError
from agentclaw.community.core.harness.models import (
    FindingsReport, Layer, PatchDefinition, PatchOperation, PatchRecord, PatchStatus, PatchTarget,
)

ENTITY_TYPE = "staff"
ENTITY_ID = "test_user"
BOT_ID = "test_bot"


class _LocalIdentityDispatcher:
    """Build a real LocalDeviceFileSystem whose identity mapper composes the on-disk
    OSS-layout path under tmp_path (identity flow lands files via the real seam)."""

    def dispatch_addressed(self, ctx, *, namespace, entity_type, entity_id, bot_id, engine_type):
        from agentclaw.community.core.services.identity_addressing import build_arca_identity_mapper
        from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem
        mapper = build_arca_identity_mapper(entity_type, entity_id, bot_id, engine_type)
        return LocalDeviceFileSystem(path_mapper=mapper)


@pytest.fixture
def bot_profile(tmp_path: Path):
    with patch("agentclaw.community.core.workspace.path_factory.get_bolt_base_dir", return_value=tmp_path):
        from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin
        factory = WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin())
        # Bot resolves to a (dummy) ctx → identity I/O routes through the dispatcher's
        # LocalDeviceFileSystem onto tmp_path (no local-OSS fallback).
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = MagicMock()
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


def _engine(bot_profile, scanner=None):
    return PatchEngine(
        bot_profile=bot_profile,
        scanner=scanner or MagicMock(),
        db=MagicMock(),
        patch_record_repo=MagicMock(),
        identity_service=MagicMock(),
    )


def _record():
    return PatchRecord(
        bot_id=BOT_ID, entity_id=ENTITY_ID, patch_id=1, layer=Layer.L1,
        target=PatchTarget(files=["AGENTS.md"], sections=[]),
    )


def _update_md_op(src="# old", dst="# new content"):
    return PatchOperation(
        op="update_md", target="AGENTS.md",
        detail={"src_content": src, "dst_content": dst},
    )


def _report(status="completed", score=88, reason=None):
    return FindingsReport(
        bot_id=BOT_ID, entity_id=ENTITY_ID,
        health_score=score, status=status, failed_reason=reason,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_update_md_writes_dst_content(bot_profile, tmp_path):
    eng = _engine(bot_profile)
    op = _update_md_op(dst="# patched")
    rec = await eng.apply(ENTITY_TYPE, ENTITY_ID, BOT_ID, _record(), [op])
    assert rec.status == PatchStatus.APPLIED
    assert rec.backup_checksum is not None
    ref = await bot_profile.read_file(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md")
    assert "# patched" in ref.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_unsupported_op_raises(bot_profile):
    eng = _engine(bot_profile)
    op = PatchOperation(op="rewrite_section", target="AGENTS.md", detail={})
    with pytest.raises(PatchEngineError):
        await eng.apply(ENTITY_TYPE, ENTITY_ID, BOT_ID, _record(), [op])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_completed_sets_verified(bot_profile):
    scanner = MagicMock()
    scanner.scan = AsyncMock(return_value=_report(status="completed", score=90))
    eng = _engine(bot_profile, scanner=scanner)
    rec = _record()
    rec.backup_file_types = ["AGENTS.md"]
    out = await eng.verify(ENTITY_TYPE, ENTITY_ID, BOT_ID, rec)
    assert out.status == PatchStatus.VERIFIED
    assert out.health_score == 90
    # verify re-scans the record's backed-up file types
    assert scanner.scan.await_args.kwargs["file_types"] == ["AGENTS.md"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_non_completed_sets_failed(bot_profile):
    scanner = MagicMock()
    scanner.scan = AsyncMock(return_value=_report(status="error", score=10, reason="boom"))
    eng = _engine(bot_profile, scanner=scanner)
    out = await eng.verify(ENTITY_TYPE, ENTITY_ID, BOT_ID, _record())
    assert out.status == PatchStatus.FAILED
    assert out.failed_reason == "boom"


def _patch_with_ops(ops):
    return PatchDefinition(
        template_id=1, name="p", layer=Layer.L1,
        content=json.dumps([{"op": o.op, "target": o.target, "detail": o.detail} for o in ops]),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rollback_restores_src_when_content_matches(bot_profile):
    eng = _engine(bot_profile)
    op = _update_md_op(src="# original", dst="# patched")
    await eng.apply(ENTITY_TYPE, ENTITY_ID, BOT_ID, _record(), [op])
    ok, msg = await eng.rollback_by_patch(
        ENTITY_TYPE, ENTITY_ID, BOT_ID, _patch_with_ops([op]), operations=[op],
    )
    assert ok is True
    ref = await bot_profile.read_file(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md")
    assert "# original" in ref.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rollback_rejected_when_content_changed(bot_profile):
    eng = _engine(bot_profile)
    op = _update_md_op(src="# original", dst="# patched")
    # current file is empty (never applied) → mismatch dst → reject
    ok, msg = await eng.rollback_by_patch(
        ENTITY_TYPE, ENTITY_ID, BOT_ID, _patch_with_ops([op]), operations=[op],
    )
    assert ok is False
    assert "changed" in msg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rollback_no_content_fails(bot_profile):
    eng = _engine(bot_profile)
    patch_def = PatchDefinition(template_id=1, name="p", layer=Layer.L1, content=None)
    ok, msg = await eng.rollback_by_patch(ENTITY_TYPE, ENTITY_ID, BOT_ID, patch_def)
    assert ok is False
    assert "no content" in msg.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rollback_parses_operations_from_patch_content(bot_profile):
    # operations omitted → rollback_by_patch must parse them from patch.content JSON
    eng = _engine(bot_profile)
    op = _update_md_op(src="# original", dst="# patched")
    await eng.apply(ENTITY_TYPE, ENTITY_ID, BOT_ID, _record(), [op])
    ok, msg = await eng.rollback_by_patch(
        ENTITY_TYPE, ENTITY_ID, BOT_ID, _patch_with_ops([op]),
    )
    assert ok is True
    ref = await bot_profile.read_file(ENTITY_TYPE, ENTITY_ID, BOT_ID, "AGENTS.md")
    assert "# original" in ref.content
