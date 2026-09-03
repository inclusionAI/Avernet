"""Promotion carries a teclaw bot's CLI tools by copy, not by gather (W9).

The container owns a teclaw bot's live *files*, so a promotion reads them from
the engine. It does not own the bot's tools: the platform fetched them, pinned
them, verified them and kept its own copy. So this half of the promotion is a
copy from one object-store prefix to another, and the engine is not involved —
which is what these cases pin.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.cli_tools.store import CliToolStore
from agentclaw.community.core.service_bot.services.deploy.teclaw_file_promotion import (
    TeclawFilePromotion,
    TeclawFilePromotionError,
)

from tests.community.core.bot_config_manifest.cli_tools._fakes import (
    FakeCliToolRepo,
    FakeCopyingObjectStorage,
)

_ENV = "dev"
_BASE = f"teclaw/{_ENV}/bolt_data"
_LIVE = f"{_BASE}/staff_u1/bot7_cli"


class _NoFilesDeviceFs:
    """A bot whose container holds no files at all.

    The file half of the promotion is exercised by its own suite; these cases
    are about the tool half, and an empty container keeps them from asserting
    two things at once. It also makes the engine's silence meaningful: if
    ``_promote_cli_tools`` ever asked the engine anything, ``read_file`` would
    record it.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_dir(self, ns, recursive=False):
        self.calls.append(f"list_dir:{ns}")
        return []

    async def read_file(self, logical):  # pragma: no cover - must never run
        self.calls.append(f"read_file:{logical}")
        raise AssertionError("promotion read a file while staging CLI tools")


def _row(repo, name: str, *, key: str | None = None, md5: str = "9f2c", version="1.4.2"):
    repo.upsert(
        env=_ENV,
        entity_id="u1",
        bot_id="bot7",
        name=name,
        source="https://x/mycli",
        digest="sha256:" + "a" * 64,
        subpath=None,
        md5=md5,
        size_bytes=8123456,
        version=version,
        oss_key=key or f"{_LIVE}/{name}",
        installed_by="u1",
        modifier="u1",
    )


def _promotion(*, oss=None, repo=None):
    oss = oss or FakeCopyingObjectStorage()
    repo = repo or FakeCliToolRepo()
    promotion = TeclawFilePromotion(
        oss_storage=oss,
        cli_tool_repository=repo,
        cli_tool_store=CliToolStore(object_storage=oss, store_base=lambda: _BASE),
    )
    return promotion, oss, repo


async def _stage(promotion, *, stage="verify", publish_id=9, device_fs=None):
    return await promotion.stage_files(
        device_fs=device_fs or _NoFilesDeviceFs(),
        env=_ENV,
        entity_type="staff",
        entity_id="u1",
        bot_id="bot7",
        publish_id=publish_id,
        stage=stage,
    )


@pytest.mark.asyncio
async def test_a_tool_is_copied_into_the_stages_prefix() -> None:
    promotion, oss, repo = _promotion()
    _row(repo, "mycli")
    oss.objects[f"{_LIVE}/mycli"] = b"\x7fELF"

    refs = await _stage(promotion)

    staged = f"{_BASE}/staff_u1/bot7_9_verify/teclaw/cli/mycli"
    assert oss.copies == [(f"{_LIVE}/mycli", staged)]
    assert refs.cli_tools == [
        {
            "name": "mycli",
            "store": "bot-data",
            "path": "staff_u1/bot7_9_verify/teclaw/cli/mycli",
            "md5": "9f2c",
            "version": "1.4.2",
        }
    ]


@pytest.mark.asyncio
async def test_promotion_never_calls_the_engine_for_a_tool() -> None:
    """The platform's copy is the source, so promotion costs a server-side copy
    rather than a round trip through the container — and on the one path that
    matters, a bot created from a manifest, there is no container to ask."""
    device_fs = _NoFilesDeviceFs()
    promotion, oss, repo = _promotion()
    _row(repo, "mycli")
    oss.objects[f"{_LIVE}/mycli"] = b"\x7fELF"

    await _stage(promotion, device_fs=device_fs)

    assert not any(call.startswith("read_file") for call in device_fs.calls)
    assert oss.reads == []


@pytest.mark.asyncio
async def test_the_ref_carries_the_platforms_md5_not_a_recomputed_one() -> None:
    """The platform hashed those exact bytes when it installed them, over the
    *executable* — after unpacking and selection — so re-hashing the object here
    would at best repeat the work and at worst answer about the wrong bytes."""
    promotion, oss, repo = _promotion()
    _row(repo, "mycli", md5="deadbeef")
    oss.objects[f"{_LIVE}/mycli"] = b"different bytes entirely"

    refs = await _stage(promotion)
    assert refs.cli_tools[0]["md5"] == "deadbeef"


@pytest.mark.asyncio
async def test_the_copy_reads_the_recorded_key_not_a_recomputed_one() -> None:
    """A tool whose object was written under an earlier store base still
    promotes: the row holds where its bytes actually are."""
    promotion, oss, repo = _promotion()
    stale = "teclaw/OLD/bolt_data/staff_u1/bot7_cli/mycli"
    _row(repo, "mycli", key=stale)
    oss.objects[stale] = b"\x7fELF"

    refs = await _stage(promotion)
    assert oss.copies[0][0] == stale
    assert refs.cli_tools[0]["path"].startswith("staff_u1/bot7_9_verify/")


@pytest.mark.asyncio
async def test_draft_and_verify_snapshots_do_not_share_objects() -> None:
    """Republishing a draft must not change what a published bot runs."""
    promotion, oss, repo = _promotion()
    _row(repo, "mycli")
    oss.objects[f"{_LIVE}/mycli"] = b"\x7fELF"

    draft = await _stage(promotion, stage="draft")
    verify = await _stage(promotion, stage="verify")

    assert draft.cli_tools[0]["path"] != verify.cli_tools[0]["path"]
    assert len({key for _, key in oss.copies}) == 2


@pytest.mark.asyncio
async def test_a_bot_with_no_tools_promotes_no_refs() -> None:
    promotion, oss, _ = _promotion()
    refs = await _stage(promotion)
    assert refs.cli_tools == [] and oss.copies == []


@pytest.mark.asyncio
async def test_a_failed_copy_stops_the_build_rather_than_dropping_the_tool() -> None:
    """A published artifact that silently lost a command is worse than a build
    that stopped."""
    promotion, oss, repo = _promotion()
    _row(repo, "mycli")  # the object is deliberately absent from the store
    with pytest.raises(TeclawFilePromotionError) as excinfo:
        await _stage(promotion)
    assert "mycli" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_promotion_wired_without_the_table_carries_no_tool_refs() -> None:
    """A deployment that has not enabled the category promotes files exactly as
    it did before W9 — the correct answer, rather than a silent half-promotion."""
    promotion = TeclawFilePromotion(oss_storage=FakeCopyingObjectStorage())
    refs = await _stage(promotion)
    assert refs.cli_tools == []
