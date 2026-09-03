"""``CliToolService`` — the one component that installs a bot's tools (W9).

The cases here are the failure modes that would otherwise be silent: a row
recorded for a tool the engine refused, a removal computed from a drifted
engine listing, an archive member picked by accident, a binary for the wrong
architecture.
"""
from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile

import pytest

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
)
from agentclaw.community.core.bot_config_manifest.cli_tools import (
    CliToolContext,
    CliToolDecl,
    CliToolOp,
    CliToolScope,
    CliToolService,
    CliToolStore,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.delivery_port import (
    CliToolPlacementError,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
    FETCH_CATEGORY,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.teclaw_port import (
    CliToolDriftUnobservableError,
)

from ._fakes import (
    FakeCliToolRepo,
    FakeDelivery,
    FakeEntryFetcher,
    FakeObjectStorage,
    code_of,
)

_BASE = "teclaw/dev/bolt_data"
_LIVE = f"{_BASE}/staff_u1/bot7_cli"
_CTX = CliToolContext(
    bot_id="bot7", owner_id="u1", actor_id="u2", entity_id="u1",
    env="dev", engine_type="openclaw", tenant="teamclaw",
)


def _elf(machine: int = 0x3E, *, payload: bytes = b"\x00" * 64) -> bytes:
    """A minimal little-endian ELF header with ``e_machine`` set."""
    header = bytearray(b"\x7fELF\x02\x01\x01" + b"\x00" * 13)
    header[18:20] = machine.to_bytes(2, "little")
    return bytes(header) + payload


_TOOL = _elf()
_DIGEST = "sha256:" + hashlib.sha256(_TOOL).hexdigest()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _targz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _service(*, content=_TOOL, digest=_DIGEST, fetch_error=None, delivery=None, oss=None):
    oss = oss or FakeObjectStorage()
    repo = FakeCliToolRepo()
    delivery = delivery if delivery is not None else FakeDelivery()
    fetcher = FakeEntryFetcher(content=content, digest=digest, error=fetch_error)
    store = CliToolStore(object_storage=oss, store_base=lambda: _BASE)
    service = CliToolService(
        repo=repo, store=store, delivery=delivery, entry_fetcher=fetcher
    )
    return service, repo, delivery, fetcher, oss


def _decl(**kwargs) -> CliToolDecl:
    base = {"name": "mycli", "source_url": "https://x/mycli", "digest": _DIGEST}
    base.update(kwargs)
    return CliToolDecl(**base)


# ── the happy path, in order ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_install_fetches_stores_delivers_and_records() -> None:
    service, repo, delivery, fetcher, oss = _service()
    outcome = await service.install(_CTX, _decl(version="1.4.2"), installed_by="u2")

    assert outcome.op is CliToolOp.INSTALLED
    assert delivery.installed == [("mycli", _TOOL)]
    assert oss.objects[f"{_LIVE}/mycli"] == _TOOL
    record = repo.get(env="dev", entity_id="u1", bot_id="bot7", name="mycli")
    assert record.md5 == hashlib.md5(_TOOL).hexdigest()
    assert record.size_bytes == len(_TOOL)
    assert (record.digest, record.version, record.oss_key) == (
        _DIGEST, "1.4.2", f"{_LIVE}/mycli",
    )
    assert (record.installed_by, record.modifier) == ("u2", "u2")


@pytest.mark.asyncio
async def test_bytes_are_written_to_oss_before_delivery() -> None:
    """On teclaw the stored object *is* what the artifact references, so a
    delivery that ran first would compose a ref to bytes that are not there."""
    order: list[str] = []
    oss = FakeObjectStorage()
    real_put = oss.put_object
    oss.put_object = lambda key, content: (order.append("store"), real_put(key, content))[1]

    class Ordered(FakeDelivery):
        async def install(self, ctx, *, name, data):
            order.append("deliver")
            await super().install(ctx, name=name, data=data)

    service, *_ = _service(oss=oss, delivery=Ordered())
    await service.install(_CTX, _decl(), installed_by="u2")
    assert order == ["store", "deliver"]


@pytest.mark.asyncio
async def test_the_fetch_goes_through_the_entry_fetcher_under_this_category() -> None:
    """One funnel — a category that fetched its own way would acquire bytes
    the platform's provenance log never saw."""
    service, _, _, fetcher, _ = _service()
    await service.install(_CTX, _decl(auth="tok"), installed_by="u2")
    call = fetcher.calls[0]
    assert call["category"] == FETCH_CATEGORY == "cli_tools"
    assert (call["source_url"], call["digest"], call["auth"]) == (
        "https://x/mycli", _DIGEST, "tok",
    )
    assert call["entry_identity"] == "mycli"


# ── the pin ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_install_enforces_the_declared_sha256() -> None:
    """The one category that distributes an executable checks the content
    address the fetch already computed — the belt costs nothing, and keep_last
    is the road where stored bytes could stand in for what was declared."""
    service, repo, delivery, _, _ = _service(digest="sha256:" + "0" * 64)
    outcome = await service.install(_CTX, _decl(), installed_by="u2")
    assert outcome.op is CliToolOp.FAILED
    assert _DIGEST in outcome.detail
    assert delivery.installed == [] and repo.rows == {}


@pytest.mark.asyncio
async def test_a_fetch_failure_records_nothing() -> None:
    service, repo, delivery, _, oss = _service(fetch_error=EntryFetchError("404"))
    outcome = await service.install(_CTX, _decl(), installed_by="u2")
    assert outcome.op is CliToolOp.FAILED and "404" in outcome.detail
    assert repo.rows == {} and oss.puts == [] and delivery.installed == []


# ── archives ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,pack", [("zip", _zip), ("tar.gz", _targz)])
async def test_archive_selects_only_the_declared_subpath(kind, pack) -> None:
    archive = pack({"README.md": b"not a tool", "bin/mycli": _TOOL})
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    service, _, delivery, _, oss = _service(content=archive, digest=digest)
    outcome = await service.install(
        _CTX, _decl(digest=digest, unpack=kind, subpath="bin/mycli"), installed_by="u2"
    )
    assert outcome.op is CliToolOp.INSTALLED
    assert delivery.installed == [("mycli", _TOOL)]
    assert oss.objects[f"{_LIVE}/mycli"] == _TOOL


@pytest.mark.asyncio
async def test_an_absent_member_fails_and_names_what_the_archive_holds() -> None:
    archive = _zip({"bin/other": _TOOL})
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    service, repo, _, _, _ = _service(content=archive, digest=digest)
    outcome = await service.install(
        _CTX, _decl(digest=digest, unpack="zip", subpath="bin/mycli"), installed_by="u2"
    )
    assert outcome.op is CliToolOp.FAILED
    assert "bin/other" in outcome.detail
    assert repo.rows == {}


@pytest.mark.asyncio
async def test_a_subpath_naming_a_directory_is_refused() -> None:
    """The unpacker's inventory lists files, so a directory is simply not a
    member; ``select_subpath``'s not-a-regular-file refusal is the belt behind
    that, exercised directly in ``test_verify.py``."""
    archive = _zip({"bin/mycli": _TOOL})
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    service, _, _, _, _ = _service(content=archive, digest=digest)
    outcome = await service.install(
        _CTX, _decl(digest=digest, unpack="zip", subpath="bin"), installed_by="u2"
    )
    assert outcome.op is CliToolOp.FAILED


@pytest.mark.asyncio
async def test_unpack_without_a_subpath_fails_rather_than_guessing() -> None:
    """One entry is one command is one file: which file is the caller's to say."""
    archive = _zip({"bin/mycli": _TOOL})
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    service, _, _, _, _ = _service(content=archive, digest=digest)
    outcome = await service.install(
        _CTX, _decl(digest=digest, unpack="zip"), installed_by="u2"
    )
    assert outcome.op is CliToolOp.FAILED and "subpath" in outcome.detail


@pytest.mark.asyncio
async def test_a_subpath_without_unpack_is_refused_not_ignored() -> None:
    """An ignored field is a caller believing they configured something."""
    service, _, _, _, _ = _service()
    outcome = await service.install(
        _CTX, _decl(subpath="bin/mycli"), installed_by="u2"
    )
    assert outcome.op is CliToolOp.FAILED and "unpack" in outcome.detail


# ── architecture ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_non_elf_file_is_refused() -> None:
    script = b"#!/bin/sh\necho hi\n" + b"\x00" * 32
    digest = "sha256:" + hashlib.sha256(script).hexdigest()
    service, repo, delivery, _, _ = _service(content=script, digest=digest)
    outcome = await service.install(_CTX, _decl(digest=digest), installed_by="u2")
    assert outcome.op is CliToolOp.FAILED and "ELF" in outcome.detail
    assert repo.rows == {} and delivery.installed == []


@pytest.mark.asyncio
async def test_a_non_amd64_elf_fails_with_the_architecture_found() -> None:
    """A wrong build has a perfectly valid digest; without this the failure
    would surface as an Exec format error inside a container days later."""
    arm = _elf(0xB7)
    digest = "sha256:" + hashlib.sha256(arm).hexdigest()
    service, repo, _, _, _ = _service(content=arm, digest=digest)
    outcome = await service.install(_CTX, _decl(digest=digest), installed_by="u2")
    assert outcome.op is CliToolOp.FAILED
    assert "aarch64" in outcome.detail
    assert repo.rows == {}


@pytest.mark.asyncio
async def test_a_name_that_is_not_one_key_segment_is_refused_before_any_fetch() -> None:
    service, _, _, fetcher, _ = _service()
    outcome = await service.install(_CTX, _decl(name="../escape"), installed_by="u2")
    assert outcome.op is CliToolOp.FAILED
    assert fetcher.calls == []


# ── placement failure ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nothing_is_recorded_when_placement_fails() -> None:
    """The row is the platform's claim that the bot has the tool. A claim made
    before the engine accepted it is a claim that can be false."""
    delivery = FakeDelivery(install_error=CliToolPlacementError("disk full"))
    service, repo, _, _, oss = _service(delivery=delivery)
    outcome = await service.install(_CTX, _decl(), installed_by="u2")
    assert outcome.op is CliToolOp.FAILED and "disk full" in outcome.detail
    assert repo.rows == {}


@pytest.mark.asyncio
async def test_a_failed_placement_discards_the_object_it_just_stored() -> None:
    """Its key is derived, not recorded, so it is collected here or never."""
    delivery = FakeDelivery(install_error=CliToolPlacementError("nope"))
    service, _, _, _, oss = _service(delivery=delivery)
    await service.install(_CTX, _decl(), installed_by="u2")
    assert oss.deletes == [f"{_LIVE}/mycli"]
    assert f"{_LIVE}/mycli" not in oss.objects


@pytest.mark.asyncio
async def test_a_failed_object_write_never_reaches_the_engine() -> None:
    service, repo, delivery, _, _ = _service(oss=FakeObjectStorage(fail_puts=True))
    outcome = await service.install(_CTX, _decl(), installed_by="u2")
    assert outcome.op is CliToolOp.FAILED
    assert delivery.installed == [] and repo.rows == {}


# ── removal ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_deletes_the_oss_object_with_the_row() -> None:
    service, repo, delivery, _, oss = _service()
    await service.install(_CTX, _decl(), installed_by="u2")
    outcome = await service.remove(_CTX, "mycli")
    assert outcome.op is CliToolOp.REMOVED
    assert delivery.deleted == ["mycli"]
    assert repo.rows == {}
    assert f"{_LIVE}/mycli" not in oss.objects


@pytest.mark.asyncio
async def test_removing_a_tool_the_bot_does_not_have_fails_plainly() -> None:
    service, _, delivery, _, _ = _service()
    outcome = await service.remove(_CTX, "ghost")
    assert outcome.op is CliToolOp.FAILED
    assert delivery.deleted == []


@pytest.mark.asyncio
async def test_a_refused_engine_delete_leaves_the_row_standing() -> None:
    """The row says the bot has the tool, and after a refused delete it does."""
    service, repo, _, _, _ = _service()
    await service.install(_CTX, _decl(), installed_by="u2")
    service._delivery.delete_error = CliToolPlacementError("busy")
    outcome = await service.remove(_CTX, "mycli")
    assert outcome.op is CliToolOp.FAILED
    assert repo.get(env="dev", entity_id="u1", bot_id="bot7", name="mycli") is not None


@pytest.mark.asyncio
async def test_remove_all_collects_the_objects_the_rows_pointed_at() -> None:
    service, repo, _, _, oss = _service()
    await service.install(_CTX, _decl(), installed_by="u2")
    await service.install(_CTX, _decl(name="other"), installed_by="u2")
    assert await service.remove_all(_CTX) == 2
    assert repo.rows == {}
    assert oss.objects == {}


# ── full override ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replace_all_removes_tools_absent_from_the_declaration() -> None:
    service, repo, delivery, _, _ = _service()
    await service.install(_CTX, _decl(name="old"), installed_by="manifest")
    outcomes = await service.replace_all(
        _CTX, [_decl(name="new")], installed_by="manifest"
    )
    assert [(o.name, o.op) for o in outcomes] == [
        ("old", CliToolOp.REMOVED), ("new", CliToolOp.INSTALLED),
    ]
    assert delivery.deleted == ["old"]
    assert set(r.name for r in repo.list(env="dev", entity_id="u1", bot_id="bot7")) == {"new"}


@pytest.mark.asyncio
async def test_replace_all_computes_removals_from_the_table_not_the_engine() -> None:
    """A tool the platform installed is removed even when the engine's view
    has drifted — a short listing would otherwise leave it behind."""
    delivery = FakeDelivery(listing=[])
    service, _, _, _, _ = _service(delivery=delivery)
    await service.install(_CTX, _decl(name="old"), installed_by="manifest")
    await service.replace_all(_CTX, [], installed_by="manifest")
    assert delivery.deleted == ["old"]
    assert delivery.listed == 0


@pytest.mark.asyncio
async def test_a_matching_digest_and_subpath_is_unchanged_and_not_refetched() -> None:
    service, _, delivery, fetcher, _ = _service()
    await service.install(_CTX, _decl(), installed_by="manifest")
    outcomes = await service.replace_all(_CTX, [_decl()], installed_by="manifest")
    assert [o.op for o in outcomes] == [CliToolOp.UNCHANGED]
    assert len(fetcher.calls) == 1
    assert len(delivery.installed) == 1


@pytest.mark.asyncio
async def test_version_alone_never_forces_a_reinstall() -> None:
    """``version`` is a label. Letting it converge would redeliver a 200 MiB
    binary because a comment changed."""
    service, _, _, fetcher, _ = _service()
    await service.install(_CTX, _decl(version="1.0"), installed_by="manifest")
    outcomes = await service.replace_all(
        _CTX, [_decl(version="2.0")], installed_by="manifest"
    )
    assert [o.op for o in outcomes] == [CliToolOp.UNCHANGED]
    assert len(fetcher.calls) == 1


@pytest.mark.asyncio
async def test_a_changed_subpath_reinstalls_even_at_the_same_digest() -> None:
    """One archive can carry two commands, so the digest alone cannot decide."""
    archive = _zip({"bin/a": _TOOL, "bin/b": _elf(payload=b"\x01" * 64)})
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    service, _, delivery, _, _ = _service(content=archive, digest=digest)
    await service.install(
        _CTX, _decl(digest=digest, unpack="zip", subpath="bin/a"), installed_by="manifest"
    )
    outcomes = await service.replace_all(
        _CTX,
        [_decl(digest=digest, unpack="zip", subpath="bin/b")],
        installed_by="manifest",
    )
    assert [o.op for o in outcomes] == [CliToolOp.INSTALLED]
    assert len(delivery.installed) == 2


@pytest.mark.asyncio
async def test_replace_all_reports_per_tool_on_partial_failure() -> None:
    """Three succeed, one fails, and the caller is told which — rather than
    being handed an exception and left to reconcile."""
    service, repo, _, _, _ = _service()
    bad = _decl(name="bad", digest="sha256:" + "0" * 64)
    outcomes = await service.replace_all(
        _CTX, [_decl(name="a"), bad, _decl(name="c")], installed_by="manifest"
    )
    assert [(o.name, o.op) for o in outcomes] == [
        ("a", CliToolOp.INSTALLED), ("bad", CliToolOp.FAILED), ("c", CliToolOp.INSTALLED),
    ]
    assert set(r.name for r in repo.list(env="dev", entity_id="u1", bot_id="bot7")) == {"a", "c"}


# ── drift ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drift_reports_a_table_row_the_engine_does_not_have() -> None:
    delivery = FakeDelivery(listing=["other"])
    service, _, _, _, _ = _service(delivery=delivery)
    await service.install(_CTX, _decl(), installed_by="u2")
    drift = await service.drift(_CTX)
    assert drift.observable and not drift.converged
    assert drift.missing_on_bot == ("mycli",)
    assert drift.unrecorded == ("other",)


@pytest.mark.asyncio
async def test_drift_on_an_unobservable_family_says_so_rather_than_converged() -> None:
    """"No drift" and "I did not look" are different answers, and only one of
    them is a fact."""
    delivery = FakeDelivery(listing=CliToolDriftUnobservableError("composed from the table"))
    service, _, _, _, _ = _service(delivery=delivery)
    await service.install(_CTX, _decl(), installed_by="u2")
    drift = await service.drift(_CTX)
    assert drift.observable is False and drift.converged is False
    assert "composed from the table" in drift.reason
    assert drift.recorded == ("mycli",)


# ── the properties ────────────────────────────────────────────────────────


def test_the_service_branches_on_no_engine_type() -> None:
    """The family difference is which port the strategy bound, and the
    directory is the engine's answer asked inside its own install."""
    from agentclaw.community.core.bot_config_manifest.cli_tools import service as mod

    code = code_of(mod)
    for forbidden in ("openclaw", "teclaw", "aicoding", "hermes", "claude_code", "chmod"):
        assert forbidden not in code, f"the service names {forbidden!r}"


def test_the_service_composes_no_filesystem_path_for_a_bot() -> None:
    from agentclaw.community.core.bot_config_manifest.cli_tools import service as mod

    code = code_of(mod)
    for forbidden in ("/home/", "/workspace", "/identity", "os.path.join"):
        assert forbidden not in code, f"the service names {forbidden!r}"


def test_the_context_is_what_the_fetch_funnel_asks_for() -> None:
    """An HTTP-driven install and a manifest apply fetch through one funnel;
    the seam that makes that true is a declared protocol, not a coincidence."""
    from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
        FetchContext,
    )

    assert isinstance(_CTX, FetchContext)
    assert _CTX.scope == CliToolScope(entity_type="staff", entity_id="u1", bot_id="bot7")


# ── the vocabulary ────────────────────────────────────────────────────────


def test_a_manifest_entry_becomes_a_declaration() -> None:
    """The materialiser and the HTTP route reach the service through one type,
    which is what keeps the two callers from drifting."""
    decl = CliToolDecl.from_entry({
        "name": "mycli", "from": "https://x/t.zip", "digest": _DIGEST,
        "subpath": "bin/mycli", "unpack": "zip", "version": "1.4.2", "auth": "tok",
    })
    assert decl == CliToolDecl(
        name="mycli", source_url="https://x/t.zip", digest=_DIGEST,
        subpath="bin/mycli", unpack="zip", version="1.4.2", auth="tok",
    )
    assert decl.keep_last is True


def test_an_entry_naming_its_source_the_other_way_reads_the_same() -> None:
    assert CliToolDecl.from_entry(
        {"name": "c", "source": "https://x/c", "digest": _DIGEST}
    ).source_url == "https://x/c"


def test_on_fetch_failure_fail_turns_keep_last_off() -> None:
    decl = CliToolDecl.from_entry(
        {"name": "c", "from": "https://x/c", "digest": _DIGEST,
         "on_fetch_failure": "fail"}
    )
    assert decl.keep_last is False


def test_a_failed_outcome_says_it_failed() -> None:
    from agentclaw.community.core.bot_config_manifest.cli_tools import CliToolOutcome

    assert CliToolOutcome("c", CliToolOp.FAILED, "why").failed is True
    assert CliToolOutcome("c", CliToolOp.INSTALLED).failed is False


@pytest.mark.asyncio
async def test_a_reinstall_collects_an_object_left_under_an_earlier_base() -> None:
    """A reinstall normally overwrites the same key. It does not when the row
    was written under an earlier store base — and then the old object is
    unreferenced the moment the row is replaced, with its key held nowhere
    else."""
    service, repo, _, _, oss = _service()
    await service.install(_CTX, _decl(), installed_by="u2")
    stale = "teclaw/OLD/bolt_data/staff_u1/bot7_cli/mycli"
    oss.objects[stale] = _TOOL
    repo.rows[("dev", "u1", "bot7", "mycli")] = repo.rows[
        ("dev", "u1", "bot7", "mycli")
    ].model_copy(update={"oss_key": stale, "digest": "sha256:" + "1" * 64})

    await service.install(_CTX, _decl(), installed_by="u2")
    assert stale in oss.deletes and stale not in oss.objects
    assert oss.objects[f"{_LIVE}/mycli"] == _TOOL


@pytest.mark.asyncio
async def test_an_object_that_will_not_delete_is_logged_not_raised() -> None:
    """The caller's operation already succeeded; turning a leftover object into
    a second failure would misreport what happened, and the object is still
    there for a later purge."""
    service, repo, _, _, oss = _service()
    await service.install(_CTX, _decl(), installed_by="u2")
    oss.fail_deletes = True
    outcome = await service.remove(_CTX, "mycli")
    assert outcome.op is CliToolOp.REMOVED
    assert repo.rows == {}


# ── creation cleanup ──────────────────────────────────────────────────────


def test_the_purger_drops_the_rows_and_the_objects_without_an_engine() -> None:
    """A W13 creation that ended without a bot has no container, so asking an
    engine would be asking about something that never existed. The rows'
    ``oss_key``s are collected by the delete itself: that column lives only on
    those rows, so a caller that deleted first could never enumerate what it
    had just orphaned."""
    from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
        CliToolPurger,
    )

    oss = FakeObjectStorage()
    repo = FakeCliToolRepo()
    store = CliToolStore(object_storage=oss, store_base=lambda: _BASE)
    for name in ("a", "b"):
        repo.upsert(
            env="dev", entity_id="u1", bot_id="bot7", name=name,
            source="https://x", digest=_DIGEST, subpath=None, md5="m",
            size_bytes=1, version=None, oss_key=f"{_LIVE}/{name}",
            installed_by="manifest", modifier="u2",
        )
        oss.objects[f"{_LIVE}/{name}"] = _TOOL

    purger = CliToolPurger(repo=repo, store=store)
    assert purger("u1", "bot7") == 2
    assert repo.rows == {} and oss.objects == {}


def test_the_purger_leaves_no_row_behind_when_an_object_will_not_delete() -> None:
    """The caller is already on a path that is ending; one unreachable object
    must not hide the rest of the cleanup."""
    from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
        CliToolPurger,
    )

    oss = FakeObjectStorage(fail_deletes=True)
    repo = FakeCliToolRepo()
    repo.upsert(
        env="dev", entity_id="u1", bot_id="bot7", name="a", source="https://x",
        digest=_DIGEST, subpath=None, md5="m", size_bytes=1, version=None,
        oss_key=f"{_LIVE}/a", installed_by="manifest", modifier="u2",
    )
    purger = CliToolPurger(
        repo=repo, store=CliToolStore(object_storage=oss, store_base=lambda: _BASE)
    )
    assert purger("u1", "bot7") == 0
    assert repo.rows == {}


@pytest.mark.asyncio
async def test_a_manifest_apply_removes_a_tool_a_person_installed() -> None:
    """Full override does not respect provenance, and ``installed_by`` is what
    lets a report say so rather than the removal being silent."""
    service, repo, delivery, _, _ = _service()
    await service.install(_CTX, _decl(name="by-hand"), installed_by="u2")
    outcomes = await service.replace_all(_CTX, [], installed_by="manifest")

    assert [(o.name, o.op) for o in outcomes] == [("by-hand", CliToolOp.REMOVED)]
    assert outcomes[0].record.installed_by == "u2"
    assert delivery.deleted == ["by-hand"] and repo.rows == {}
