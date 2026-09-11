"""``cli_tools`` as a manifest category (W9).

The property this file exists for: the manifest is a *second caller* of
``CliToolService``, not a second implementation. So what is pinned here is
delegation, convergence, and that the API arm and the apply arm refuse the same
declaration for the same reason.
"""
from __future__ import annotations

from types import SimpleNamespace

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

#: The object source a plain ``_entry()`` names. A cli_tools entry declares a
#: source the way every other category does; the endpoint and the key pair
#: come off the credential the source names, never out of the document.
_OBJECT_SOURCE = {
    "protocol": "oss",
    "bucket": "tool-artifacts",
    "key": "bin/",
    "auth": "oss-cred",
}


def _ctx(**kwargs) -> ApplyContext:
    base = dict(
        bot_id="bot7", owner_id="u1", actor_id="u2", entity_id="u1", env="dev",
        tenant="teamclaw", engine_type="openclaw", bot_type="personal", bot={},
        capabilities=resolve_capabilities(
            active_engine="openclaw", bot_type="personal",
            is_teclaw=lambda e: (e or "") == "teclaw",
        ),
        apply_id="ap1",
        # A declared source resolves against the apply's source session.
        source_session=SimpleNamespace(sources={"tools": _OBJECT_SOURCE}),
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
    """One tool declared over an object-store source.

    A declaration object, because that is what a ``source`` is: the bare URL
    string this suite used to write is refused at ``PUT`` and no longer has a
    road at apply either.
    """
    base = {
        "name": "mycli",
        "source": {
            "protocol": "oss",
            "bucket": "tools",
            "key": "mycli",
            "auth": "oss-prod",
        },
        "digest": _DIGEST,
    }
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
async def test_the_entry_reaches_the_fetch_funnel_unrewritten() -> None:
    """The materialiser hands down the entry it was given, placeholders and
    all.

    ``${BOT_*}`` in a declared source is the funnel's to substitute, on the
    road that knows which of a declaration's fields are addresses — and it is
    the only thing that can substitute into a ``from``-named source, whose
    fields this materialiser never sees. A second substitution here produced a
    *copy* of the address, and a copy is something a later reader can acquire
    from instead of the entry.
    """
    service, _, _, fetcher = _service()
    mat = CliToolsMaterialiser(service)
    declared = {
        "protocol": "oss",
        "bucket": "tools",
        "key": "${BOT_ENGINE_TYPE}/mycli",
        "auth": "oss-prod",
    }
    await _apply(mat, _ctx(), [_entry(source=declared)])
    assert fetcher.calls[0]["declaration"] == declared


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
    # Not re-fetched, and the family is not called at all: an apply where every
    # declaration converged has nothing to tell it (spec D-13).
    assert len(fetcher.calls) == 1
    assert len(delivery.replaced) == 1


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
    # Removed by omission from the whole-set call, not by a delete of its own.
    assert delivery.deleted == []
    assert [[name for name, _ in call] for call in delivery.replaced][-1] == ["new"]
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
    claim: the API uploads and a manifest declares, but both arms reach the
    same pipeline once the bytes are in hand, so a wrong-architecture binary
    fails identically whichever door it came through."""
    payload = elf(machine=0xB7)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()

    service, repo, _, _ = _service(content=payload, digest=digest)

    direct = await service.install_upload(
        context_for(_ctx()),
        CliToolDecl(name="mycli", digest=digest),
        data=payload,
        installed_by="u2",
    )
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


# ── cli_tools over a named git source (defect D5) ──────────────────────────
#
# The one construct that used to pass `PUT` and fail at apply — the "accepted
# means appliable" rule broken by the category that most needed it. Two halves
# had to be wrong together: the schema charged the object-store digest rule to
# a source it classified as `named`, and the materialiser put that `from` NAME
# on the wire as though it were a URL.


_REPO = "https://code.example.com/team/tools.git"


def _git_ctx(**kwargs) -> ApplyContext:
    """An apply context whose session declares one git source named ``tools``."""
    session = SimpleNamespace(
        sources={
            "tools": {
                "protocol": "git",
                "url": _REPO,
                "ref": "v1.0.0",
                "subpath": "bin",
            }
        }
    )
    return _ctx(source_session=session, **kwargs)


@pytest.mark.asyncio
async def test_a_tool_from_a_named_git_source_applies_without_a_digest() -> None:
    """The commit SHA is the pin, so no `digest` is asked for — and the entry
    reaches the wire as the *repository*, not as the string "tools"."""
    service, repo, _, fetcher = _service()
    mat = CliToolsMaterialiser(service)
    resolved, _, results = await _apply(
        mat, _git_ctx(), [{"name": "mycli", "from": "tools", "subpath": "mycli"}]
    )
    assert resolved.ok, resolved.failures
    assert [r.reason for r in results] == [None]
    assert fetcher.calls[0]["source_address"] == _REPO
    assert fetcher.calls[0]["protocol"] == "git"
    # The source's subpath and the entry's composed, so one declared source can
    # serve more than one tool out of one repository.
    assert fetcher.filed[0]["entry_identity"] == "mycli"


@pytest.mark.asyncio
async def test_the_digest_belt_still_refuses_an_unpinned_object_store_tool() -> None:
    """Loosening git must not loosen the object store: the belt behind the
    schema keys on the same axis the schema does, not on a blanket."""
    service, _, _, _ = _service()
    mat = CliToolsMaterialiser(service)
    resolved = await mat.resolve(
        _git_ctx(),
        [{"name": "mycli", "source": {"protocol": "oss", "bucket": "b",
                                      "key": "mycli", "auth": "oss-prod"}}],
    )
    assert not resolved.ok
    assert "requires a 'digest'" in resolved.failures[0].reason


@pytest.mark.asyncio
async def test_a_bare_url_source_fails_the_entry_rather_than_being_fetched() -> None:
    """The spelling the grammar dropped.

    ``declared_protocol`` cannot name a protocol for a string, so the digest
    belt sees an unpinned entry and refuses it there; an entry that *did* carry
    a digest gets no further either — the funnel has no road for a bare URL and
    says which form a source must take. Both refusals are the point: the
    platform never fetches an address a document merely wrote down.
    """
    service, repo, _, fetcher = _service()
    mat = CliToolsMaterialiser(service)

    unpinned = await mat.resolve(
        _git_ctx(), [{"name": "mycli", "source": "https://x/mycli"}]
    )
    assert not unpinned.ok
    assert "requires a 'digest'" in unpinned.failures[0].reason

    _, _, results = await _apply(
        mat,
        _git_ctx(),
        [{"name": "mycli", "source": "https://x/mycli", "digest": _DIGEST}],
    )
    assert [r.outcome for r in results] == [EntryOutcome.FAILED]
    assert "declaration object" in (results[0].reason or "")
    assert repo.rows == {}


@pytest.mark.asyncio
async def test_an_undeclared_from_fails_the_entry_with_the_name() -> None:
    """And when the source really is missing, the failure says so — rather
    than fetching the name as a URL and failing somewhere unrecognisable."""
    service, _, _, _ = _service()
    mat = CliToolsMaterialiser(service)
    _, _, results = await _apply(
        mat, _git_ctx(), [{"name": "mycli", "from": "nowhere", "digest": _DIGEST}]
    )
    assert results[0].outcome is EntryOutcome.FAILED
    assert "nowhere" in results[0].reason


@pytest.mark.asyncio
async def test_a_git_sourced_tool_never_plans_unchanged() -> None:
    """Convergence is ``(digest, subpath)``, and a git source has no digest.

    Without this, every git-sourced tool at one subpath would compare equal to
    every other — so a ref that moved to a new commit would plan ``unchanged``
    and the old binary would survive, which is the single outcome convergence
    exists to prevent. An unpinned declaration re-acquires every apply instead:
    the same conservative answer ``resources`` gives, for the same reason.
    """
    service, _, _, _ = _service()
    mat = CliToolsMaterialiser(service)
    ctx = _git_ctx()
    entries = [{"name": "mycli", "from": "tools", "subpath": "mycli"}]
    await _apply(mat, ctx, entries)
    _, plan, _ = await _apply(mat, ctx, entries)
    assert [p.outcome for p in plan.entries] != [EntryOutcome.UNCHANGED.value]
    assert not plan.is_noop


@pytest.mark.asyncio
async def test_a_pinned_tool_still_plans_unchanged_on_a_repeat_apply() -> None:
    """And the object-store road keeps its fast path: re-applying an unchanged
    pin must not redeliver a binary that can be 200 MiB."""
    service, _, _, _ = _service()
    mat = CliToolsMaterialiser(service)
    ctx = _ctx()
    await _apply(mat, ctx, [_entry()])
    _, plan, _ = await _apply(mat, ctx, [_entry()])
    assert plan.is_noop


@pytest.mark.asyncio
async def test_two_revisions_of_a_git_tool_do_not_share_one_object_key() -> None:
    """The review's P1, closed.

    The store's key embeds a fingerprint of the digest precisely so a new
    version never overwrites the object a surviving row still points at. A
    git-sourced declaration carries no digest — the commit SHA is its pin — and
    an empty digest fingerprints to the constant "0", so every revision of one
    tool landed on one key. A rejected delivery would then roll the row back to
    bytes that had already been overwritten, publishing a binary the engine
    refused. The acquired bytes are hashed instead.
    """
    first, oss, _, _ = _service(content=_TOOL)
    mat = CliToolsMaterialiser(first)
    entries = [{"name": "mycli", "from": "tools", "subpath": "mycli"}]
    await _apply(mat, _git_ctx(), entries)

    other = elf(payload=b"a-different-build".ljust(64, b"\x00"))
    second, _, _, _ = _service(content=other)
    # Same store, so the two revisions compete for the same keys if they can.
    second._store = first._store
    await _apply(CliToolsMaterialiser(second), _git_ctx(), entries)

    keys = [k for k in first._store._oss.puts if "mycli" in k]
    assert len(keys) == 2
    assert keys[0] != keys[1], "two different binaries wrote to one object key"
    assert not any(k.endswith(".0") for k in keys), (
        "an empty digest fingerprinted to the constant key"
    )
    # And both objects survive, so a rollback has real bytes to point back at.
    assert len({first._store._oss.objects[k] for k in keys}) == 2


@pytest.mark.asyncio
async def test_a_git_tool_records_a_real_content_digest() -> None:
    """The row's digest is what convergence, the store key and the audit all
    read. Empty is not a value any of them can use."""
    service, repo, _, _ = _service()
    await _apply(
        CliToolsMaterialiser(service),
        _git_ctx(),
        [{"name": "mycli", "from": "tools", "subpath": "mycli"}],
    )
    row = service.get(context_for(_git_ctx()), "mycli")
    assert row is not None
    assert row.digest.startswith("sha256:")
    assert len(row.digest) == len("sha256:") + 64


@pytest.mark.asyncio
async def test_an_inline_git_source_is_exempt_from_the_digest_belt_too() -> None:
    """The other spelling of the same source.

    `from:` a git source and an inline `source: {protocol: git, ...}` are one
    source declared two ways, so the digest exemption has to reach both — which
    is the whole of D5, and the named road alone would not have proved it.
    """
    service, _, _, fetcher = _service()
    mat = CliToolsMaterialiser(service)
    resolved, _, results = await _apply(
        mat,
        _ctx(source_session=SimpleNamespace(sources={})),
        [{
            "name": "mycli",
            "source": {
                "protocol": "git",
                "url": "https://code.example.com/team/tools.git",
                "ref": "v1.0.0",
            },
            "subpath": "bin/mycli",
        }],
    )
    assert resolved.ok, resolved.failures
    assert [r.reason for r in results] == [None]
    assert fetcher.calls[0]["source_address"] == (
        "https://code.example.com/team/tools.git"
    )
    assert fetcher.calls[0]["protocol"] == "git"
