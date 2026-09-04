"""``cli_tools`` as a manifest category (W9).

The property this file exists for: the manifest is a *second caller* of
``CliToolService``, not a second implementation. So what is pinned here is
delegation, convergence, and that the API arm and the apply arm refuse the same
declaration for the same reason.
"""
from __future__ import annotations

import hashlib
import inspect

import pytest

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.materialisers.cli_tools import (
    CliToolsMaterialiser,
    context_for,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import EntryOutcome
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    resolve_capabilities,
)
from agentclaw.community.core.bot_config_manifest.cli_tools import (
    CliToolDecl,
    CliToolStatus,
    CliToolOutcome,
    CliToolService,
    CliToolStore,
    INSTALLED_BY_MANIFEST,
)

from ..cli_tools._fakes import (
    elf,
    FakeCliToolRepo,
    FakeDelivery,
    FakeEntryFetcher,
    FakeObjectStorage,
)

_BASE = "teclaw/dev/bolt_data"


def _elf() -> bytes:
    return elf()


_TOOL = _elf()
_DIGEST = "sha256:" + hashlib.sha256(_TOOL).hexdigest()


def _ctx(**kwargs) -> ApplyContext:
    base = dict(
        bot_id="bot7", owner_id="u1", actor_id="u2", entity_id="u1", env="dev",
        tenant="teamclaw", engine_type="openclaw", bot_type="personal", bot={},
        capabilities=resolve_capabilities(
            active_engine="openclaw", bot_type="personal",
            is_teclaw=lambda e: (e or "") == "teclaw",
        ),
        apply_id="ap1",
    )
    base.update(kwargs)
    return ApplyContext(**base)


def _service(*, content=_TOOL, digest=_DIGEST, delivery=None):
    oss = FakeObjectStorage()
    repo = FakeCliToolRepo()
    delivery = delivery if delivery is not None else FakeDelivery()
    fetcher = FakeEntryFetcher(content=content, digest=digest)
    service = CliToolService(
        repo=repo,
        store=CliToolStore(object_storage=oss, store_base=lambda: _BASE),
        delivery=delivery,
        entry_fetcher=fetcher,
    )
    return service, repo, delivery, fetcher


def _entry(**kwargs) -> dict:
    base = {"name": "mycli", "from": "https://x/mycli", "digest": _DIGEST}
    base.update(kwargs)
    return base


async def _apply(mat, ctx, entries):
    resolved = await mat.resolve(ctx, entries)
    plan = await mat.plan(ctx, resolved.intents)
    results = await mat.write(ctx, plan)
    return resolved, plan, results


# ── delegation ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_is_one_replace_all_call() -> None:
    calls: list[tuple] = []

    class Recording(CliToolService):
        async def replace_all(self, ctx, decls, *, installed_by):
            calls.append((tuple(d.name for d in decls), installed_by))
            return [CliToolOutcome(d.name, CliToolStatus.INSTALLED) for d in decls]

    service, *_ = _service()
    service.__class__ = Recording
    mat = CliToolsMaterialiser(service)
    ctx = _ctx()
    _, _, results = await _apply(mat, ctx, [_entry(), _entry(name="other")])

    assert calls == [(("mycli", "other"), INSTALLED_BY_MANIFEST)]
    assert [r.outcome for r in results] == [EntryOutcome.UPDATED] * 2


@pytest.mark.asyncio
async def test_the_materialiser_adds_no_fetch_of_its_own() -> None:
    """A fetch here would be a second implementation reached only by the
    manifest — the arm where a divergence is hardest to notice."""
    source = inspect.getsource(
        inspect.getmodule(CliToolsMaterialiser)
    )
    for forbidden in ("entry_fetcher", "EntryFetcher", "unpack_archive", "verify_amd64"):
        assert forbidden not in source, f"the materialiser names {forbidden!r}"


@pytest.mark.asyncio
async def test_resolve_makes_no_service_call_at_all() -> None:
    service, _, delivery, fetcher = _service()
    mat = CliToolsMaterialiser(service)
    await mat.resolve(_ctx(), [_entry()])
    assert fetcher.calls == [] and delivery.installed == []


def test_the_apply_context_is_carried_whole_into_the_service() -> None:
    """The audit fields must not be lost in translation: ``actor_id`` stays the
    person applying, while ``installed_by`` answers the different question of
    what put the tool there."""
    ctx = _ctx()
    tool_ctx = context_for(ctx)
    assert (tool_ctx.actor_id, tool_ctx.owner_id) == ("u2", "u1")
    assert (tool_ctx.apply_id, tool_ctx.tenant) == ("ap1", "teamclaw")
    assert tool_ctx.budget is ctx.budget


@pytest.mark.asyncio
async def test_a_placeholder_in_a_source_is_substituted_before_the_fetch() -> None:
    service, _, _, fetcher = _service()
    mat = CliToolsMaterialiser(service)
    await _apply(
        mat, _ctx(), [_entry(**{"from": "https://x/${BOT_ENGINE_TYPE}/mycli"})]
    )
    assert fetcher.calls[0]["source_url"] == "https://x/openclaw/mycli"


# ── convergence ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unchanged_digest_and_subpath_plans_unchanged() -> None:
    service, _, delivery, fetcher = _service()
    mat = CliToolsMaterialiser(service)
    ctx = _ctx()
    await _apply(mat, ctx, [_entry()])
    _, plan, results = await _apply(mat, ctx, [_entry()])

    assert plan.is_noop
    assert [r.outcome for r in results] == [EntryOutcome.UNCHANGED]
    assert len(fetcher.calls) == 1 and len(delivery.installed) == 1


@pytest.mark.asyncio
async def test_version_alone_is_not_a_change() -> None:
    """``version`` is a label; converging on it would redeliver a binary
    because a caller edited a string."""
    service, *_ = _service()
    mat = CliToolsMaterialiser(service)
    ctx = _ctx()
    await _apply(mat, ctx, [_entry(version="1.0")])
    _, plan, _ = await _apply(mat, ctx, [_entry(version="2.0")])
    assert plan.is_noop


@pytest.mark.asyncio
async def test_a_row_the_declaration_no_longer_names_plans_a_removal() -> None:
    service, repo, delivery, _ = _service()
    mat = CliToolsMaterialiser(service)
    ctx = _ctx()
    await _apply(mat, ctx, [_entry(name="old")])
    _, plan, results = await _apply(mat, ctx, [_entry(name="new")])

    assert plan.removals == ("old",)
    assert delivery.deleted == ["old"]
    assert {r.identity for r in results} == {"new"}
    assert {row.name for row in repo.list(env="dev", entity_id="u1", bot_id="bot7")} == {"new"}


@pytest.mark.asyncio
async def test_the_plan_reads_the_table_not_the_engine() -> None:
    """A tool the platform installed must be planned for removal even when the
    engine's view has drifted — and the table is also what makes a dry run
    possible without a container round trip."""
    delivery = FakeDelivery(listing=[])
    service, *_ = _service(delivery=delivery)
    mat = CliToolsMaterialiser(service)
    ctx = _ctx()
    await _apply(mat, ctx, [_entry(name="old")])
    resolved = await mat.resolve(ctx, [])
    plan = await mat.plan(ctx, resolved.intents)

    assert plan.removals == ("old",)
    assert delivery.listed == 0


# ── refusals, and the equivalence with the API arm ────────────────────────


@pytest.mark.asyncio
async def test_a_duplicate_name_is_refused_rather_than_deduplicated() -> None:
    """A bot cannot have one command twice, and the table's UNIQUE constraint
    says so. Two entries for one name means the author believes something that
    is not true of the result."""
    service, *_ = _service()
    resolved = await CliToolsMaterialiser(service).resolve(
        _ctx(), [_entry(), _entry()]
    )
    assert [f.identity for f in resolved.failures] == ["mycli"]
    assert len(resolved.intents) == 1


@pytest.mark.asyncio
async def test_an_entry_without_a_digest_is_refused_at_apply_too() -> None:
    """The schema refuses it at PUT. Re-asked here because a stored document
    can predate a rule, and distributing an unpinned executable is the one
    thing this category exists not to do."""
    service, _, _, fetcher = _service()
    entry = _entry()
    del entry["digest"]
    resolved = await CliToolsMaterialiser(service).resolve(_ctx(), [entry])
    assert resolved.failures and "digest" in resolved.failures[0].reason
    assert fetcher.calls == []


@pytest.mark.asyncio
async def test_the_api_and_apply_refuse_the_same_hostile_declaration() -> None:
    """The equivalence that makes "one implementation" a fact rather than a
    claim: both arms reach the same service, so a wrong-architecture binary
    fails identically whichever door it came through."""
    payload = elf(machine=0xB7)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()

    service, repo, _, _ = _service(content=payload, digest=digest)
    decl = CliToolDecl(name="mycli", source_url="https://x/mycli", digest=digest)

    direct = await service.install(context_for(_ctx()), decl, installed_by="u2")
    _, _, results = await _apply(
        CliToolsMaterialiser(service), _ctx(), [_entry(digest=digest)]
    )

    assert direct.status is CliToolStatus.FAILED
    assert [r.outcome for r in results] == [EntryOutcome.FAILED]
    assert "aarch64" in direct.detail and "aarch64" in (results[0].reason or "")
    assert repo.rows == {}


@pytest.mark.asyncio
async def test_a_failed_entry_carries_a_reason_and_a_successful_one_a_note() -> None:
    """They answer opposite questions, and a client rendering failures must not
    show a note as an error."""
    service, *_ = _service()
    mat = CliToolsMaterialiser(service)
    bad = _entry(name="bad", digest="sha256:" + "0" * 64)
    _, _, results = await _apply(mat, _ctx(), [_entry(name="good"), bad])

    by_name = {r.identity: r for r in results}
    assert by_name["good"].outcome is EntryOutcome.UPDATED
    assert by_name["good"].reason is None
    assert by_name["bad"].outcome is EntryOutcome.FAILED
    assert by_name["bad"].reason and by_name["bad"].note is None


# ── the category is unlocked ──────────────────────────────────────────────


def test_the_materialiser_is_registered_under_its_own_construct() -> None:
    from agentclaw.community.core.bot_config_manifest.apply.registry import (
        build_materialisers,
    )

    registry = build_materialisers(
        script_service=object(), activation_service=object(),
        mcp_auth_service=object(), identity_service=object(),
        upload_service=object(), capability_reader=object(),
        package_validator=object(), entry_fetcher=object(),
        resource_service=object(), cli_tool_service=object(),
    )
    assert isinstance(registry[ManifestCategory.CLI_TOOLS], CliToolsMaterialiser)


def test_no_materialiser_names_an_engine() -> None:
    """The existing property, re-asserted for the new module: the family
    difference is which delivery port the service holds."""
    source = inspect.getsource(inspect.getmodule(CliToolsMaterialiser))
    for engine in ("openclaw", "teclaw", "aicoding", "hermes", "claude_code"):
        assert engine not in source, f"the materialiser names {engine!r}"
