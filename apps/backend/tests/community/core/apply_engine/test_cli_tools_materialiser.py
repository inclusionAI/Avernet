"""``cli_tools`` as a manifest category (W9).

The property this file exists for: the manifest is a *second caller* of
``CliToolService``, not a second implementation. So what is pinned here is
delegation, convergence, and that the API arm and the apply arm refuse the same
declaration for the same reason.

The road a real apply takes: a raw entry goes through the Manifest compiler
(``compile_cli_tools``), which fetches the bytes through the declared-source
funnel and files them with the content store; the materialiser turns the
compiled ``DesiredCliTool`` into the service's ``CliToolDecl``; the service
reads the stored bytes. ``compiling_cli_tools`` is that road over fakes.
"""
from __future__ import annotations

from types import SimpleNamespace

import hashlib
import inspect

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    DesiredCliTool,
    ContentRef,
    UnpackKind,
)
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.materialisers.cli_tools import (
    CliToolsMaterialiser,
    context_for,
)
from agentclaw.community.core.apply_engine.outcomes import EntryOutcome
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
    DeclaredSourceResolver,
)
from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.apply_engine.cli_tools import (
    CliToolDecl,
    CliToolStatus,
    CliToolOutcome,
    CliToolService,
    CliToolStore,
    INSTALLED_BY_MANIFEST,
)

from .cli_tools._fakes import (
    elf,
    FakeCliToolRepo,
    FakeDelivery,
    FakeObjectStorage,
    FakeToolContent,
)
from ._fakes import (
    FakeObjectCredentials,
    OSS_BUCKET,
    compiling_cli_tools,
    declared_session,
    make_context,
    oss_source,
    seeded_object_store,
)

_BASE = "teclaw/dev/bolt_data"


def _elf() -> bytes:
    return elf()


_TOOL = _elf()
_DIGEST = "sha256:" + hashlib.sha256(_TOOL).hexdigest()

#: The key a plain ``_entry()`` reads out of the seeded bucket.
_KEY = "mycli"


def _ctx(**kwargs) -> ApplyContext:
    base = dict(
        bot_id="bot7", owner_id="u1", actor_id="u2", entity_id="u1",
        engine_type="openclaw", bot_type="personal", apply_id="ap1",
    )
    base.update(kwargs)
    return make_context(**base)


class _Rig:
    """The materialiser over its compiler, with every collaborator in reach.

    ``objects`` is the object store the declared sources read; ``content`` is
    the content store the compiler files into and the service reads from —
    one store, so a compile's filing is what the install finds.
    """

    def __init__(self, *, objects=None, delivery=None, oss=None, source_session=None, git=None) -> None:
        self.oss = oss or FakeObjectStorage()
        self.repo = FakeCliToolRepo()
        self.delivery = delivery if delivery is not None else FakeDelivery()
        self.content = FakeToolContent()
        self.objects = seeded_object_store({_KEY: _TOOL, **(objects or {})})
        self.service = CliToolService(
            repo=self.repo,
            store=CliToolStore(object_storage=self.oss, store_base=lambda: _BASE),
            delivery=self.delivery,
            content=self.content,
        )
        resolver = DeclaredSourceResolver(self.content, FakeObjectCredentials(), self.objects)
        self.session = source_session or declared_session(git=git)
        self.mat = compiling_cli_tools(self.service, resolver, self.content, source_session=self.session)


def _entry(**kwargs) -> dict:
    """One tool declared over an object-store source.

    A declaration object, because that is what a ``source`` is: the bare URL
    string this suite used to write is refused at ``PUT`` and no longer has a
    road at apply either.
    """
    base = {"name": "mycli", "source": oss_source(_KEY), "digest": _DIGEST}
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

    rig = _Rig()
    rig.service.__class__ = Recording
    _, _, results = await _apply(rig.mat, _ctx(), [_entry(), _entry(name="other")])

    assert calls == [(("mycli", "other"), INSTALLED_BY_MANIFEST)]
    assert [r.outcome for r in results] == [EntryOutcome.UPDATED] * 2


@pytest.mark.asyncio
async def test_the_materialiser_adds_no_fetch_of_its_own() -> None:
    """A fetch here would be a second implementation reached only by the
    manifest — the arm where a divergence is hardest to notice. And no read
    either: the service is the one reader of a tool's stored bytes."""
    source = inspect.getsource(
        inspect.getmodule(CliToolsMaterialiser)
    )
    for forbidden in (
        "entry_fetcher", "DeclaredSourceResolver", "unpack_archive", "verify_amd64", ".read(",
    ):
        assert forbidden not in source, f"the materialiser names {forbidden!r}"


@pytest.mark.asyncio
async def test_resolve_makes_no_service_call_at_all() -> None:
    """The compiler fetches and files at resolve; the service is not reached
    until ``write``, so nothing is read back out of the store and the family
    hears nothing."""
    rig = _Rig()
    await rig.mat.resolve(_ctx(), [_entry()])
    assert rig.content.reads == [] and rig.delivery.installed == [] and rig.delivery.replaced == []
    assert rig.repo.rows == {}


@pytest.mark.asyncio
async def test_a_desired_tool_becomes_the_services_declaration() -> None:
    """The materialiser and the HTTP route reach the service through one type,
    which is what keeps the two callers from drifting. What the desired tool
    does *not* become is an address: the declaration names the stored bytes,
    and the address the document wrote rides along only to be echoed."""
    rig = _Rig()
    desired = DesiredCliTool(
        "mycli", ContentRef(_DIGEST), pin=_DIGEST, subpath="bin/mycli",
        unpack=UnpackKind.ZIP, version="1.4.2", note="moved", declared_address="https://x/t.git",
    )
    resolved = await rig.mat.materialiser.resolve(_ctx(), [desired])
    (intent,) = resolved.intents
    assert intent.identity == "mycli" and intent.note == "moved"
    assert intent.value == CliToolDecl(
        name="mycli", digest=_DIGEST, subpath="bin/mycli", unpack="zip", version="1.4.2",
        content=ContentRef(_DIGEST), declared_address="https://x/t.git",
    )


def test_the_apply_context_is_carried_whole_into_the_service() -> None:
    """The audit fields must not be lost in translation: ``actor_id`` stays the
    person applying, while ``installed_by`` answers the different question of
    what put the tool there. Nothing fetch-shaped rides along: the service has
    no budget to charge and no session to resolve against."""
    ctx = _ctx()
    tool_ctx = context_for(ctx)
    assert (tool_ctx.actor_id, tool_ctx.owner_id, tool_ctx.entity_id) == ("u2", "u1", "u1")
    assert (tool_ctx.apply_id, tool_ctx.tenant, tool_ctx.env) == ("ap1", "teamclaw", "dev")
    assert tool_ctx.engine_type == "openclaw"
    assert not hasattr(tool_ctx, "budget") and not hasattr(tool_ctx, "source_session")


@pytest.mark.asyncio
async def test_a_placeholder_in_the_declared_source_is_the_funnels_to_substitute() -> None:
    """``${BOT_*}`` in a declared source is substituted on the road that
    knows which of a declaration's fields are addresses — the fetch funnel the
    compiler hands the raw entry to. The object store is asked for the
    substituted key, while the row echoes the address the document wrote,
    placeholder and all: a substituted copy on the row would be something a
    later reader could acquire from instead of the entry."""
    rig = _Rig(objects={"openclaw/mycli": _TOOL})
    declared = oss_source("${BOT_ENGINE_TYPE}/mycli")
    _, _, results = await _apply(rig.mat, _ctx(), [_entry(source=declared)])
    assert [r.outcome for r in results] == [EntryOutcome.UPDATED]
    assert [key for _, key, _ in rig.objects.calls] == ["openclaw/mycli"]
    row = rig.service.get(context_for(_ctx()), "mycli")
    assert row.source == f"{OSS_BUCKET}/${{BOT_ENGINE_TYPE}}/mycli"


# ── convergence ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unchanged_digest_and_subpath_plans_unchanged() -> None:
    rig = _Rig()
    ctx = _ctx()
    await _apply(rig.mat, ctx, [_entry()])
    _, plan, results = await _apply(rig.mat, ctx, [_entry()])

    assert plan.is_noop
    assert [r.outcome for r in results] == [EntryOutcome.UNCHANGED]
    # Not re-fetched by the compiler (the pin is already reachable), not
    # re-read by the service, and the family is not called at all: an apply
    # where every declaration converged has nothing to tell it (spec D-13).
    assert len(rig.objects.calls) == 1
    assert len(rig.content.reads) == 1
    assert len(rig.delivery.replaced) == 1


@pytest.mark.asyncio
async def test_version_alone_is_not_a_change() -> None:
    """``version`` is a label; converging on it would redeliver a binary
    because a caller edited a string."""
    rig = _Rig()
    ctx = _ctx()
    await _apply(rig.mat, ctx, [_entry(version="1.0")])
    _, plan, _ = await _apply(rig.mat, ctx, [_entry(version="2.0")])
    assert plan.is_noop


@pytest.mark.asyncio
async def test_a_row_the_declaration_no_longer_names_plans_a_removal() -> None:
    rig = _Rig()
    ctx = _ctx()
    await _apply(rig.mat, ctx, [_entry(name="old")])
    _, plan, results = await _apply(rig.mat, ctx, [_entry(name="new")])

    assert plan.removals == ("old",)
    # Removed by omission from the whole-set call, not by a delete of its own.
    assert rig.delivery.deleted == []
    assert [[name for name, _ in call] for call in rig.delivery.replaced][-1] == ["new"]
    assert {r.identity for r in results} == {"new"}
    assert {row.name for row in rig.repo.list(env="dev", entity_id="u1", bot_id="bot7")} == {"new"}


@pytest.mark.asyncio
async def test_the_plan_reads_the_table_not_the_engine() -> None:
    """A tool the platform installed must be planned for removal even when the
    engine's view has drifted — and the table is also what makes a dry run
    possible without a container round trip."""
    rig = _Rig(delivery=FakeDelivery(listing=[]))
    ctx = _ctx()
    await _apply(rig.mat, ctx, [_entry(name="old")])
    resolved = await rig.mat.resolve(ctx, [])
    plan = await rig.mat.plan(ctx, resolved.intents)

    assert plan.removals == ("old",)
    assert rig.delivery.listed == 0


# ── refusals, and the equivalence with the API arm ────────────────────────


@pytest.mark.asyncio
async def test_a_duplicate_name_is_refused_rather_than_deduplicated() -> None:
    """A bot cannot have one command twice, and the table's UNIQUE constraint
    says so. Two entries for one name means the author believes something that
    is not true of the result — and a compile failure aborts the construct, so
    the first spelling does not quietly win either."""
    rig = _Rig()
    resolved = await rig.mat.resolve(_ctx(), [_entry(), _entry()])
    assert [f.identity for f in resolved.failures] == ["mycli"]
    assert "more than once" in resolved.failures[0].reason
    assert resolved.intents == ()


@pytest.mark.asyncio
async def test_an_entry_without_a_digest_is_refused_at_apply_too() -> None:
    """The schema refuses it at PUT. Re-asked here because a stored document
    can predate a rule, and distributing an unpinned executable is the one
    thing this category exists not to do."""
    rig = _Rig()
    entry = _entry()
    del entry["digest"]
    resolved = await rig.mat.resolve(_ctx(), [entry])
    assert resolved.failures and "digest" in resolved.failures[0].reason
    assert rig.objects.calls == []


@pytest.mark.asyncio
async def test_the_api_and_apply_refuse_the_same_hostile_declaration() -> None:
    """The equivalence that makes "one implementation" a fact rather than a
    claim: the API uploads and a manifest declares, but both arms reach the
    same pipeline once the bytes are in hand, so a wrong-architecture binary
    fails identically whichever door it came through."""
    payload = elf(machine=0xB7)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()

    rig = _Rig(objects={"arm": payload})

    direct = await rig.service.install_upload(
        context_for(_ctx()),
        CliToolDecl(name="mycli", digest=digest),
        data=payload,
        installed_by="u2",
    )
    _, _, results = await _apply(
        rig.mat, _ctx(), [_entry(source=oss_source("arm"), digest=digest)]
    )

    assert direct.status is CliToolStatus.FAILED
    assert [r.outcome for r in results] == [EntryOutcome.FAILED]
    assert "aarch64" in direct.detail and "aarch64" in (results[0].reason or "")
    assert rig.repo.rows == {}


@pytest.mark.asyncio
async def test_a_failed_entry_carries_a_reason_and_a_successful_one_a_note() -> None:
    """They answer opposite questions, and a client rendering failures must not
    show a note as an error. The failure is the service's — a binary the gate
    refuses — because a compile failure aborts the construct before any row
    is written."""
    arm = elf(machine=0xB7)
    rig = _Rig(objects={"arm": arm})
    bad = _entry(name="bad", source=oss_source("arm"), digest="sha256:" + hashlib.sha256(arm).hexdigest())
    _, _, results = await _apply(rig.mat, _ctx(), [_entry(name="good"), bad])

    by_name = {r.identity: r for r in results}
    assert by_name["good"].outcome is EntryOutcome.UPDATED
    assert by_name["good"].reason is None
    assert by_name["bad"].outcome is EntryOutcome.FAILED
    assert by_name["bad"].reason and by_name["bad"].note is None


# ── the category is unlocked ──────────────────────────────────────────────


def test_the_materialiser_is_registered_under_its_own_construct() -> None:
    from agentclaw.community.core.apply_engine.registry import (
        build_materialisers,
    )

    registry = build_materialisers(
        script_service=object(), activation_service=object(),
        mcp_auth_service=object(), identity_service=object(),
        mcp_config_service=object(),
        upload_service=object(), capability_reader=object(),
        package_validator=object(), content=object(),
        resource_service=object(), resource_rows=object(), cli_tool_service=object(), shared_skills=object(),
    )
    assert isinstance(registry[Category.CLI_TOOLS], CliToolsMaterialiser)


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


class _StaticGit:
    """A git client serving one checkout with stable bytes.

    The checkout mirrors ``GitCheckout`` where the compiler reaches it: the
    composed subpath names the one file ``read_file`` answers, the declared
    size is what the budget charges, and ``root`` is a path that was never
    made so the session's close has nothing to remove.
    """

    def __init__(self, body: bytes = _TOOL, sha: str = "c" * 40) -> None:
        self.body = body
        self.sha = sha
        self.specs: list = []

    def fetch(self, spec, *, headers=None):
        from pathlib import Path

        self.specs.append(spec)
        return SimpleNamespace(
            root=Path("/nonexistent/acm-git-static"),
            sha=self.sha,
            url=spec.url,
            ref=spec.ref,
            members=(("100644", spec.subpath or "tool", len(self.body)),),
            tree_bytes=len(self.body),
            files=lambda subpath=None, file_limit=None: [(subpath or "tool", self.body)],
            read_file=lambda subpath=None, file_limit=None: self.body,
        )


def _git_rig(git: _StaticGit | None = None, *, sources=None, **kwargs) -> _Rig:
    """A rig whose session declares one git source named ``tools``."""
    git = git or _StaticGit()
    declared = {"tools": {"protocol": "git", "url": _REPO, "ref": "v1.0.0", "subpath": "bin"}}
    return _Rig(
        source_session=declared_session(sources=declared if sources is None else sources, git=git),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_a_tool_from_a_named_git_source_applies_without_a_digest() -> None:
    """The commit SHA is the pin, so no `digest` is asked for — and the entry
    reaches the wire as the *repository*, not as the string "tools"."""
    git = _StaticGit()
    rig = _git_rig(git)
    resolved, _, results = await _apply(
        rig.mat, _ctx(), [{"name": "mycli", "from": "tools", "subpath": "mycli"}]
    )
    assert resolved.ok, resolved.failures
    assert [r.reason for r in results] == [None]
    assert [spec.url for spec in git.specs] == [_REPO]
    # The source's subpath and the entry's composed, so one declared source can
    # serve more than one tool out of one repository.
    assert git.specs[0].subpath == "bin/mycli"
    filed = rig.content.store_calls[0]
    assert filed["entry_identity"] == "mycli" and filed["category"] == "cli_tools"
    assert filed["source_url"] == f"git+{_REPO}@{'c' * 40}:bin/mycli"
    # The row echoes the name the document used, never the repository.
    assert rig.service.get(context_for(_ctx()), "mycli").source == "tools"


@pytest.mark.asyncio
async def test_the_digest_belt_still_refuses_an_unpinned_object_store_tool() -> None:
    """Loosening git must not loosen the object store: the belt behind the
    schema keys on the same axis the schema does, not on a blanket."""
    rig = _git_rig()
    resolved = await rig.mat.resolve(
        _ctx(), [{"name": "mycli", "source": oss_source(_KEY)}],
    )
    assert not resolved.ok
    assert "requires a 'digest'" in resolved.failures[0].reason
    assert rig.objects.calls == []


@pytest.mark.asyncio
async def test_a_bare_url_source_fails_the_entry_rather_than_being_fetched() -> None:
    """The spelling the grammar dropped.

    ``declared_protocol`` cannot name a protocol for a string, so the digest
    belt sees an unpinned entry and refuses it there; an entry that *did* carry
    a digest gets no further either — the funnel has no road for a bare URL and
    says which form a source must take. Both refusals are the point: the
    platform never fetches an address a document merely wrote down.
    """
    rig = _git_rig()

    unpinned = await rig.mat.resolve(_ctx(), [{"name": "mycli", "source": "https://x/mycli"}])
    assert not unpinned.ok
    assert "requires a 'digest'" in unpinned.failures[0].reason

    pinned = await rig.mat.resolve(
        _ctx(), [{"name": "mycli", "source": "https://x/mycli", "digest": _DIGEST}]
    )
    assert not pinned.ok
    assert "declaration object" in pinned.failures[0].reason
    assert rig.repo.rows == {} and rig.objects.calls == []


@pytest.mark.asyncio
async def test_an_undeclared_from_fails_the_entry_with_the_name() -> None:
    """And when the source really is missing, the failure says so — rather
    than fetching the name as a URL and failing somewhere unrecognisable."""
    rig = _git_rig()
    resolved = await rig.mat.resolve(
        _ctx(), [{"name": "mycli", "from": "nowhere", "digest": _DIGEST}]
    )
    assert not resolved.ok
    assert "nowhere" in resolved.failures[0].reason
    assert rig.repo.rows == {}


@pytest.mark.asyncio
async def test_a_git_sourced_tool_never_plans_unchanged() -> None:
    """Convergence is ``(digest, subpath)``, and a git source has no digest.

    Without this, every git-sourced tool at one subpath would compare equal to
    every other — so a ref that moved to a new commit would plan ``unchanged``
    and the old binary would survive, which is the single outcome convergence
    exists to prevent. An unpinned declaration re-acquires every apply instead:
    the same conservative answer ``resources`` gives, for the same reason.
    """
    git = _StaticGit()
    rig = _git_rig(git)
    ctx = _ctx()
    entries = [{"name": "mycli", "from": "tools", "subpath": "mycli"}]
    await _apply(rig.mat, ctx, entries)
    _, plan, _ = await _apply(rig.mat, ctx, entries)
    assert [p.outcome for p in plan.entries] != [EntryOutcome.UNCHANGED.value]
    assert not plan.is_noop
    # Re-acquired: one checkout per apply, not a reachable pin's fast path.
    assert len(git.specs) == 2


@pytest.mark.asyncio
async def test_a_pinned_tool_still_plans_unchanged_on_a_repeat_apply() -> None:
    """And the object-store road keeps its fast path: re-applying an unchanged
    pin must not redeliver a binary that can be 200 MiB."""
    rig = _Rig()
    ctx = _ctx()
    await _apply(rig.mat, ctx, [_entry()])
    _, plan, _ = await _apply(rig.mat, ctx, [_entry()])
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
    oss = FakeObjectStorage()
    first = _git_rig(_StaticGit(_TOOL, sha="a" * 40), oss=oss)
    entries = [{"name": "mycli", "from": "tools", "subpath": "mycli"}]
    await _apply(first.mat, _ctx(), entries)

    other = elf(payload=b"a-different-build".ljust(64, b"\x00"))
    # Same object store, so the two revisions compete for the same keys if
    # they can; the second rig's rows are its own, as a fresh bot's would be.
    second = _git_rig(_StaticGit(other, sha="b" * 40), oss=oss)
    await _apply(second.mat, _ctx(), entries)

    keys = [k for k in oss.puts if "mycli" in k]
    assert len(keys) == 2
    assert keys[0] != keys[1], "two different binaries wrote to one object key"
    assert not any(k.endswith(".0") for k in keys), (
        "an empty digest fingerprinted to the constant key"
    )
    # And both objects survive, so a rollback has real bytes to point back at.
    assert len({oss.objects[k] for k in keys}) == 2


@pytest.mark.asyncio
async def test_a_git_tool_records_a_real_content_digest() -> None:
    """The row's digest is what convergence, the store key and the audit all
    read. Empty is not a value any of them can use."""
    rig = _git_rig()
    await _apply(rig.mat, _ctx(), [{"name": "mycli", "from": "tools", "subpath": "mycli"}])
    row = rig.service.get(context_for(_ctx()), "mycli")
    assert row is not None
    assert row.digest == _DIGEST
    # The compiler's checkout selected the one file, so the row carries no
    # archive member to select again.
    assert row.subpath is None


@pytest.mark.asyncio
async def test_an_inline_git_source_is_exempt_from_the_digest_belt_too() -> None:
    """The other spelling of the same source.

    `from:` a git source and an inline `source: {protocol: git, ...}` are one
    source declared two ways, so the digest exemption has to reach both — which
    is the whole of D5, and the named road alone would not have proved it.
    """
    git = _StaticGit()
    rig = _git_rig(git, sources={})
    resolved, _, results = await _apply(
        rig.mat,
        _ctx(),
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
    assert [spec.url for spec in git.specs] == ["https://code.example.com/team/tools.git"]
    assert git.specs[0].subpath == "bin/mycli"
    assert rig.service.get(context_for(_ctx()), "mycli").source == "https://code.example.com/team/tools.git"
