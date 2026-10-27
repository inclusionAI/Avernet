"""Tests for the ``resources`` materialiser (W6, revised for #2162).

Pins, by the work item's acceptance criteria:
- file entries materialise through ``ResourceFileService`` at a
  workspace-relative ``path`` (physical placement stays the engine's call);
- directory entries expand into their members; the root (``data/``) is owned
  whole: every apply clears it and writes the declaration back, so a path the
  previous apply wrote and this one omits, and a file the bot wrote there
  itself, both go — and ``plan`` reports them as ``removals`` (ownership rule);
- nothing outside the root is ever touched or reported;
- fetch failures and admission refusals abort the whole category before the
  root is cleared (§3.2);
- a clear that fails fails every member with nothing written; the platform's
  resource rows follow the written files, and a rows failure is a confirmed
  partial write;
- convergence writes are file-grained (teclaw per-file forwarding is the same
  transport), and the module never touches ``BotConfigArtifact``;
- archive limits (member count / unpacked size) apply with W1's keys.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.ports.resource_file_port import WorkspaceEntry
from agentclaw.community.core.ports.resource_rows_port import WrittenFile
from agentclaw.community.core.bot_config_manifest.sources.entry_delivery import (
    BlobDelivery,
    EntryFetchError,
    FetchedEntry,
    GitDelivery,
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.fetch.errors import (
    FetchRefusedError,
)
from agentclaw.community.core.apply_engine.materialisers.resources import (
    ROOT_NOT_CLEARED_REASON,
    ROOT_NOT_LISTED_REASON,
    ResourcesRootNotCleared,
    ResourcesRootNotListed,
)
from agentclaw.community.core.apply_engine.delivery import ArcaDelivery
from agentclaw.community.core.apply_engine.orchestrator import (
    ApplyOrchestrator,
)
from agentclaw.community.core.apply_engine.registry import (
    ConfirmedPartialWriteError,
    build_materialisers,
)

from ._fakes import (
    EngineRig,
    FakeActivationService,
    FakeCapabilityReader,
    FakeIdentityService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeResourceFileService,
    FakeResourceRows,
    FakeSkillUploadService,
    FakeStartupScriptService,
    build_skill_tgz,
    compiling_resources,
    declared_session,
    make_context,
    manifest_compiler,
    real_validator,
    resource_rows_for,
)


#: The orchestrator is always handed a delivery strategy's ``steps_for``; these
#: tests exercise the ARCA family, so they hand it ARCA's. The ports callable is
#: never reached — ``steps_for`` only phases and sorts ``APPLY_ORDER``.
_arca_steps = ArcaDelivery(lambda: None).steps_for



def _run(coro):
    return asyncio.run(coro)


def _materialiser(svc, resolver, *, source_session=None, resource_rows=None):
    """The resources materialiser fed through the real entry compiler.

    Raw entries go through ``compile_resources`` over ``resolver`` and a
    fresh content store, and the typed result is what the materialiser
    reads back — the road a real apply takes. ``source_session`` is what a
    ``from`` name resolves against; the compiler's session carries it.
    ``resource_rows`` is the rows factory the write stage replaces the
    bot's rows through; a test that does not read the rows gets a recorder
    nobody looks at.
    """
    return compiling_resources(
        svc, resolver, FakeManifestContent(), source_session=source_session, resource_rows=resource_rows
    )


#: The bucket the fetch cases read. A resources entry names a declared
#: source; ``key`` is the object, and the stub addresses it the way the real
#: object road files it — ``oss://<bucket>/<key>``.
_BUCKET = "cdn"


def _src(key: str, **extra) -> dict:
    """The declared ``source`` an entry carries to read one object."""
    return {"protocol": "oss", "bucket": _BUCKET, "key": key, "auth": "oss-cred", **extra}


class _RecordingStore:
    """The content store a :class:`GitDelivery` files through, recorded.

    The git road's delivery now writes its own receipt, so the double that
    used to record ``file_bytes`` records the store call instead — in the
    store's own vocabulary, which is what the real one receives.
    """

    def __init__(self, filed: list[dict[str, Any]]) -> None:
        self._filed = filed

    def store(
        self,
        fetched,
        *,
        scope,
        source_url,
        credential_name=None,
        modifier="",
        apply_id=None,
        category=None,
        entry_identity=None,
    ):
        self._filed.append(
            {
                "content": fetched.bytes,
                "source_url": source_url,
                "category": category,
                "entry_identity": entry_identity,
                "credential_name": credential_name,
            }
        )
        return SimpleNamespace(digest=fetched.sha256)


class _StubDeclaredSourceResolver:
    """``DeclaredSourceResolver``'s test double, every call recorded.

    Addresses ending in ``gone`` stand in for unreachable sources. With
    ``fixed_body``, every address serves the same bytes (an archive); without
    it, the address's own ``bytes-of-<address>``. It answers in the pipeline's own
    currency — a real :class:`FetchedEntry` — because the materialiser
    consumes ``.content``; a stub answering in the transport's
    ``FetchedObject`` shape would pass collection and fail on the first real
    resolve.
    """

    def __init__(
        self,
        fixed_body: bytes | None = None,
        *,
        git_trees: dict[str, dict[str, bytes]] | None = None,
        moved_from: str | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.filed: list[dict[str, Any]] = []
        self._fixed_body = fixed_body
        #: repository url → the whole tree it delivers, path → bytes.
        self.git_trees = git_trees or {}
        self.moved_from = moved_from
        #: When set, ``resolve`` answers with it verbatim — the way to
        #: stand in for what the git road's ``_keep_last`` hands back.
        self.declared_override = None
        #: The store a git delivery files through, recording into ``filed``.
        self.store = _RecordingStore(self.filed)

    def fetch(
        self,
        ctx: Any,
        *,
        source_url: str,
        digest: str | None = None,
        auth: str | None = None,
        category: str = "resources_file",
        keep_last: bool = False,
        entry_identity: str | None = None,
    ) -> FetchedEntry:
        self.calls.append(
            {
                "source_url": source_url,
                "digest": digest,
                "auth": auth,
                "category": category,
                "keep_last": keep_last,
                "entry_identity": entry_identity,
            }
        )
        if source_url.endswith("gone"):
            raise EntryFetchError("source unreachable")
        body = (
            self._fixed_body
            if self._fixed_body is not None
            else b"bytes-of-" + source_url.encode()
        )
        return FetchedEntry(content=body, digest="sha256:stub", from_store=False)

    def resolve(
        self,
        ctx: Any,
        *,
        entry: dict[str, Any],
        category: str = "resources_file",
        entry_identity: str | None = None,
    ):
        """The declared-source front door, in the double.

        Mirrors the real dispatch closely enough to be worth trusting: a
        ``from`` name or an inline ``source`` object resolves through
        ``sources`` on the apply context, and a git protocol answers with a
        tree rather than bytes — which is the branch the materialiser has to
        get right. A ``source`` that is not a declaration object is refused
        here exactly as the real door refuses it, so the double cannot serve
        a spelling production no longer accepts.
        """
        if getattr(self, "declared_override", None) is not None:
            return BlobDelivery(self.declared_override)
        decl: dict[str, Any] | None = None
        if isinstance(entry.get("from"), str):
            sources = getattr(ctx, "source_session", None)
            declared = getattr(sources, "sources", {}) if sources else {}
            decl = dict(declared.get(entry["from"], {}))
            if not decl:
                raise EntryFetchError(
                    f"'from' names source {entry['from']!r}, which is not "
                    "declared under 'sources'"
                )
        elif isinstance(entry.get("source"), dict):
            decl = dict(entry["source"])
        else:
            raise EntryFetchError(
                "an entry must name one of 'from', 'source' or 'content', "
                "and 'source' is a declaration object carrying a 'protocol'"
            )

        if decl is not None and decl.get("protocol") == "git":
            key = decl["url"]
            tree = self.git_trees.get(key)
            if tree is None:
                raise EntryFetchError("git fetch failed")
            self.calls.append(
                {
                    "source_url": key,
                    "category": category,
                    "entry_identity": entry_identity,
                    "subpath": _compose(decl.get("subpath"), entry.get("subpath")),
                    "auth": decl.get("auth"),
                }
            )
            return GitDelivery(
                _stub_git_source(
                    tree,
                    subpath=_compose(decl.get("subpath"), entry.get("subpath")),
                    auth=decl.get("auth"),
                    url=key,
                    moved=self.moved_from,
                ),
                self.store,
            )

        url = decl.get("url") or f"oss://{decl.get('bucket')}/{decl.get('key')}"
        return BlobDelivery(
            self.fetch(
                ctx,
                source_url=url,
                digest=entry.get("digest"),
                auth=(decl or {}).get("auth", entry.get("auth")),
                category=category,
                keep_last=(
                    entry.get("on_fetch_failure", "keep_last") == "keep_last"
                ),
                entry_identity=entry_identity,
            )
        )




def _compose(source_subpath: str | None, entry_subpath: str | None) -> str | None:
    """The composition ``DeclaredSourceResolver`` performs, so the double addresses the
    same tree the real fetcher would."""
    if entry_subpath is None:
        return source_subpath
    if not source_subpath:
        return entry_subpath
    return source_subpath.rstrip("/") + "/" + entry_subpath.lstrip("/")


class _StubCheckout:
    """A ``GitCheckout``'s shape where a consumer reaches it.

    ``files``/``read_file`` take the subpath and the per-entry byte cap the way
    the guarded readers do, and refuse in the transport's own currency
    (``FetchRefusedError``) so ``GitEntrySource`` performs the translation it
    performs in production rather than the double doing it for it.
    """

    def __init__(self, members: dict[str, bytes], sha: str = "b" * 40) -> None:
        self._members = members
        self.sha = sha
        self.root = None
        self.tree_bytes = sum(len(v) for v in members.values())

    def files(self, subpath=None, *, file_limit=None):
        if not subpath:
            return sorted(self._members.items())
        prefix = subpath.rstrip("/") + "/"
        found = sorted(
            (name[len(prefix):], data)
            for name, data in self._members.items()
            if name.startswith(prefix)
        )
        if not found:
            raise FetchRefusedError(f"subpath {subpath!r} selects nothing")
        return found

    def read_file(self, subpath=None, *, file_limit=None):
        try:
            return self._members[subpath]
        except KeyError:
            raise FetchRefusedError(
                f"subpath {subpath!r} is not a file in the tree"
            ) from None


def _stub_git_source(members, *, subpath, auth, url, moved=None):
    """A **real** :class:`GitEntrySource` over a fake checkout.

    Not a look-alike: the materialiser dispatches on the type, exactly as the
    identity and skills materialisers do, and a duck-typed double would let a
    change to that dispatch pass its own tests while failing in production.
    """
    return GitEntrySource(
        checkout=_StubCheckout(members),
        source_url=url,
        subpath=subpath,
        moved_from=moved,
        auth=auth,
    )


def _tgz(member: dict[str, bytes]) -> bytes:
    """A real tar.gz carrying ``member`` — true bytes, not synthetic objects."""
    return build_skill_tgz(list(member.items()))


def test_fake_resource_service_records_uploads_listings_and_deletes():
    """The fake's contract, pinned where the suite leans on it: ``list_entries`` shows the workspace as a
    device listing would, protected entries included (the policy is the materialiser's), one level or
    recursive; ``delete_dir`` empties a subtree (so a later listing is honest); ``fail_clear`` is the
    transports' silent ``False`` with nothing removed."""
    svc = FakeResourceFileService()
    svc.record_present("data/old.bin", "SOUL.md", "skills/tidy/SKILL.md")
    ctx = make_context(engine_type="claude_code")
    coords = dict(entity_id=ctx.entity_id, bot_id=ctx.bot_id, engine_type=ctx.engine_type)
    _run(svc.upload_file(**coords, target_dir="data", filename="a.bin", data=b"hello"))
    assert svc.writes == {("data", "a.bin"): b"hello"}
    assert _run(svc.list_entries(**coords)) == (
        WorkspaceEntry("SOUL.md", False),
        WorkspaceEntry("data", True),
        WorkspaceEntry("skills", True),
    )
    assert _run(svc.list_entries(**coords, recursive=True)) == (
        WorkspaceEntry("SOUL.md", False),
        WorkspaceEntry("data", True),
        WorkspaceEntry("data/a.bin", False),
        WorkspaceEntry("data/old.bin", False),
        WorkspaceEntry("skills", True),
        WorkspaceEntry("skills/tidy", True),
        WorkspaceEntry("skills/tidy/SKILL.md", False),
    )
    assert _run(svc.delete_dir(**coords, path="data")) is True
    assert svc.deleted == ["data"]
    assert svc.present == {"SOUL.md", "skills/tidy/SKILL.md"}
    svc.record_present("data/again.bin")
    svc.fail_clear = True
    assert _run(svc.delete_dir(**coords, path="data")) is False
    assert svc.present == {"SOUL.md", "skills/tidy/SKILL.md", "data/again.bin"}


# --- file entries: a declared fetch and inline content (Task 2) ---


def test_file_entry_from_a_declared_source_resolves_to_intent_bytes():
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver()
    m = _materialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "data/a.md", "source": _src("a.bin")}])
    )
    assert resolved.ok
    assert [i.identity for i in resolved.intents] == ["data/a.md"]
    assert resolved.intents[0].value == b"bytes-of-oss://cdn/a.bin"
    # The funnel saw the entry's own identity under the file form's own
    # category — the W11 apply/entry linkage — with keep_last as the
    # declared default.
    assert stub.calls == [
        {
            "source_url": "oss://cdn/a.bin",
            "digest": None,
            "auth": "oss-cred",
            "category": "resources_file",
            "keep_last": True,
            "entry_identity": "data/a.md",
        }
    ]


def test_file_entry_inline_content_never_fetches():
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver()
    m = _materialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/notes/r.md", "content": "# rules"}]))
    assert resolved.ok
    assert [i.identity for i in resolved.intents] == ["data/notes/r.md"]
    assert resolved.intents[0].value == b"# rules"
    assert stub.calls == []  # inline content: no fetch, ever


def test_a_protected_workspace_entry_is_refused_at_resolve():
    """The workspace root is the resources root, minus the protected entries: an
    identity file is the compiler's grammar refusal, keyed to the entry, and
    never spends a fetch or a write."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver()
    m = _materialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "SOUL.md", "content": "# rules"}]))
    assert not resolved.ok
    assert resolved.failures[0].identity == "SOUL.md"
    assert "identity file" in resolved.failures[0].reason
    assert stub.calls == [] and svc.writes == {}


def test_fetch_failure_aborts_whole_category():
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.md", "source": _src("a.bin")},
                {"path": "data/gone.md", "source": _src("gone")},
            ],
        )
    )
    assert not resolved.ok
    assert [f.identity for f in resolved.failures] == ["data/gone.md"]


def test_bad_path_entry_is_a_resolve_failure():
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": 123}]))
    assert not resolved.ok


# --- directory entries: platform-side unpack (Task 3) ---


def test_dir_entry_expands_members_under_path():
    archive = _tgz({"top/a.txt": b"AAA", "top/sub/b.txt": b"BBB"})
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "data/wrap/",
                    "unpack": "tar.gz",
                    "strip_components": 1,
                    "source": _src("tree.tgz"),
                }
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    got = {i.identity: i.value for i in resolved.intents}
    # Members only: the directory entry is the declaration's *form*, not an
    # intent of its own. The root's replacement is the write stage's clear,
    # not a marker riding in the intents, so nothing here ends in a slash.
    assert got == {
        "data/wrap/a.txt": b"AAA",
        "data/wrap/sub/b.txt": b"BBB",
    }
    assert all(not i.identity.endswith("/") for i in resolved.intents)
    assert all(i.note is None for i in resolved.intents)


def test_bad_archive_is_a_resolve_failure_and_nothing_reaches_the_bot():
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(b"not-an-archive"))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("tree.tgz")}],
        )
    )
    assert not resolved.ok
    assert resolved.failures[0].identity == "data/wrap/"
    assert svc.writes == {} and svc.deleted == [] and svc.clear_calls == []


def test_dir_unpack_missing_is_rejected_at_resolve():
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(_tgz({"a.txt": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "data/wrap/", "source": _src("tree.tgz")}])
    )
    assert not resolved.ok
    assert "unpack" in resolved.failures[0].reason


def test_nested_paths_abort_category():
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(_tgz({"a.txt": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "data/wrap/",
                    "unpack": "tar.gz",
                    "source": _src("t.tgz"),
                },
                {"path": "data/wrap/inner.txt", "source": _src("i.txt")},
            ],
        )
    )
    assert not resolved.ok
    assert any("nest" in f.reason for f in resolved.failures)


# --- plan: classification for the report (Task 4) ---


def test_plan_classifies_present_as_updated_and_new_as_created():
    svc = FakeResourceFileService(exists_paths={"data/a.md"})
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.md", "source": _src("a.bin")},
                {"path": "data/new.md", "source": _src("new.bin")},
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    by_id = {p.intent.identity: p.outcome for p in plan.entries}
    # Never "unchanged": v1 replaces on every apply by design (the work
    # item's recommended option), and a plan that claimed otherwise would
    # be a promise the write stage does not honour.
    assert by_id == {"data/a.md": "updated", "data/new.md": "created"}
    assert plan.removals == ()
    assert not plan.is_noop


def test_plan_addresses_the_bot_owner_not_the_manifest_storage_key():
    """``entity_id`` must be the owner (the router's address), not ``ctx.entity_id``.

    The two vocabularies share a name: ``ApplyContext.entity_id`` is the
    manifest's *storage key*, while ``ResourceFileService`` addresses a
    workspace by its *owner* — the address the resources router's
    ``_resolve_params`` resolves and ``resource_coords_from_record``
    derives. A test that left them equal would never catch the swap.
    """
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(
        bot_id="b_1",
        owner_id="u_owner",
        entity_id="man-storage-key",
        engine_type="claude_code",
    )
    resolved = _run(
        m.resolve(ctx, [{"path": "data/a.md", "source": _src("a.bin")}])
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    _run(m.plan(ctx, resolved.intents))
    assert svc.listing_calls == [
        {
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
            "engine_type": "claude_code",
        }
    ]
    assert svc.exists_probes == []


def test_plan_makes_one_root_listing_and_no_presence_probes():
    """One listing classifies every member and yields the removals; per-member
    ``exists`` probes are the N round trips the listing replaces."""
    archive = _tgz({"a.txt": b"AAA", "sub/b.txt": b"BBB"})
    svc = FakeResourceFileService()
    svc.record_present("data/wrap/a.txt", "data/stale.md")
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t.tgz")},
                {"path": "data/notes/r.md", "content": "# rules"},
                {"path": "data/x.md", "content": "x"},
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    assert len(svc.listing_calls) == 1
    assert svc.exists_probes == []
    assert len(plan.entries) == 4
    assert plan.removals == ("data/stale.md",)


def test_plan_removals_are_what_the_root_holds_and_the_declaration_does_not():
    """``removals`` is ``listed − declared``: the audit row for the root's
    ownership rule, sorted, every unprotected file in the workspace the
    declaration does not put back — whoever wrote it — and never a protected
    entry.
    """
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService()
    svc.record_present(
        "data/established/a.txt",
        "data/established/stale.txt",
        "data/scratch.log",
        "skills/tidy/SKILL.md",
    )
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "data/established/",
                    "unpack": "tar.gz",
                    "source": _src("old.tgz"),
                },
                {
                    "path": "data/fresh/",
                    "unpack": "tar.gz",
                    "source": _src("new.tgz"),
                },
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    by_id = {p.intent.identity: p.outcome for p in plan.entries}
    # Members only, classified against the listing: a member the root already
    # holds is updated, a fresh one classifies as created, as a first apply would.
    assert by_id == {
        "data/established/a.txt": "updated",
        "data/fresh/a.txt": "created",
    }
    assert plan.removals == ("data/established/stale.txt", "data/scratch.log")
    assert "skills/tidy/SKILL.md" not in plan.removals
    assert not plan.is_noop


# --- write: clear the root, then deliver through the one write chain (Task 5) ---


def _write_through(m, ctx, entries):
    resolved = _run(m.resolve(ctx, entries))
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    return plan, _run(m.write(ctx, plan))


def test_write_clears_the_root_then_uploads_every_member():
    svc = FakeResourceFileService()
    svc.record_present("data/wrap/old.txt")
    archive = _tgz({"a.txt": b"AAA", "sub/b.txt": b"BBB"})
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m,
        ctx,
        [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t.tgz")}],
    )
    # One clear of the root, first: it removes everything under "data/" —
    # including files the new archive no longer ships and hand-added ones
    # (the ownership rule). The uploads land *after* it, or they would not
    # be present now.
    assert len(svc.clear_calls) == 1
    assert svc.deleted == ["data"]  # the root entry, whole
    assert svc.writes == {
        ("data/wrap", "a.txt"): b"AAA",
        ("data/wrap/sub", "b.txt"): b"BBB",
    }
    assert svc.present == {"data/wrap/a.txt", "data/wrap/sub/b.txt"}
    # The clear is an ownership action, not an entry: only the member files
    # report; what the clear took and the declaration did not put back is
    # the plan's audit row.
    assert [r.identity for r in results] == ["data/wrap/a.txt", "data/wrap/sub/b.txt"]
    assert all(r.outcome.value == "created" for r in results)
    assert plan.removals == ("data/wrap/old.txt",)


def test_a_path_the_previous_apply_wrote_and_this_one_omits_is_removed():
    """#2162: the root converges to the declaration, not to its union with
    history. A file apply #1 declared and apply #2 does not is gone after #2
    and named in its ``removals``."""
    svc = FakeResourceFileService()
    factory, rows = resource_rows_for()
    ctx = make_context(engine_type="claude_code")
    first = _materialiser(svc, _StubDeclaredSourceResolver(), resource_rows=factory)
    _write_through(
        first, ctx, [{"path": "data/a.md", "content": "A"}, {"path": "data/b.md", "content": "B"}]
    )
    assert svc.present == {"data/a.md", "data/b.md"}

    second = _materialiser(svc, _StubDeclaredSourceResolver(), resource_rows=factory)
    plan, results = _write_through(second, ctx, [{"path": "data/a.md", "content": "A"}])
    assert plan.removals == ("data/b.md",)
    assert svc.present == {"data/a.md"}
    assert svc.deleted == ["data"]  # the root entry, whole; the plan named data/b.md as the removal
    assert [(r.identity, r.outcome.value) for r in results] == [("data/a.md", "updated")]
    assert rows.rows == [WrittenFile("data/a.md", 1)]


def test_a_file_the_bot_wrote_under_the_root_is_removed_and_reported():
    """The engine keeps no "who wrote this" ledger and needs none: the root
    is the ownership boundary. A file the bot wrote there itself goes with
    the clear and the report says so."""
    svc = FakeResourceFileService()
    svc.record_present("data/scratch/notes.log")
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(m, ctx, [{"path": "data/a.md", "content": "A"}])
    assert plan.removals == ("data/scratch/notes.log",)
    assert svc.deleted == ["data"]  # the root entry, whole
    assert svc.present == {"data/a.md"}
    assert [(r.identity, r.outcome.value) for r in results] == [("data/a.md", "created")]


def test_a_protected_entry_is_never_touched_or_reported():
    """No protected entry (an identity file, the skills tree, a dotfile) is the
    materialiser's to see: not listed, not cleared, not in ``removals``."""
    svc = FakeResourceFileService()
    svc.record_present("SOUL.md", "skills/tidy/SKILL.md", ".git/HEAD")
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    plan, _results = _write_through(m, ctx, [{"path": "data/a.md", "content": "A"}])
    assert plan.removals == ()
    assert svc.deleted == []
    assert svc.present == {"SOUL.md", "skills/tidy/SKILL.md", ".git/HEAD", "data/a.md"}


def test_the_rows_follow_the_written_files():
    """The platform's resource rows are replaced last, from exactly the
    members that landed — one ``(path, size)`` per written file, in
    declaration order, created by the apply's actor."""
    svc = FakeResourceFileService()
    factory, rows = resource_rows_for()
    archive = _tgz({"a.txt": b"AAA", "sub/b.txt": b"BB"})
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive), resource_rows=factory)
    ctx = make_context(engine_type="claude_code", actor_id="u_actor")
    _write_through(
        m,
        ctx,
        [
            {"path": "data/notes/r.md", "content": "# rules"},
            {"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t.tgz")},
        ],
    )
    assert len(rows.replacements) == 1
    assert rows.replacements[-1] == {
        "files": [
            WrittenFile("data/notes/r.md", 7),
            WrittenFile("data/wrap/a.txt", 3),
            WrittenFile("data/wrap/sub/b.txt", 2),
        ],
        "owner_id": ctx.owner_id,
        "created_by": "u_actor",
    }
    # Owned by the bot's owner (whose listing these rows are), stamped with the actor who applied.
    assert rows.replacements[-1]["created_by"] == ctx.actor_id != ctx.owner_id
    assert {row.path for row in rows.rows} == set(svc.present)


def test_a_silently_failed_clear_fails_every_member_and_writes_nothing():
    """The transports refuse by returning ``False``, not by raising — all
    three device filesystems catch their own errors — so a write stage that
    only caught exceptions would see a refused rmtree as success and upload
    the new members over a root that was never cleared. A ``False`` clear
    on a root the plan listed files under aborts the category — raised, so
    the orchestrator claims neither the members nor the removals — with
    nothing written, and the rows are left alone: they still describe what
    is on the bot.
    """
    svc = FakeResourceFileService()
    svc.record_present("data/tools/old.md")
    svc.fail_clear = True
    factory, rows = resource_rows_for()
    archive = _tgz({"a.md": b"A"})
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive), resource_rows=factory)
    ctx = make_context(engine_type="claude_code")
    entries = [
        {"path": "data/tools/", "unpack": "tar.gz", "source": _src("t.tgz")},
        {"path": "data/notes/r.md", "content": "# rules"},
    ]
    with pytest.raises(ResourcesRootNotCleared, match=ROOT_NOT_CLEARED_REASON):
        _write_through(m, ctx, entries)
    assert svc.upload_calls == [] and svc.writes == {}
    assert svc.present == {"data/tools/old.md"}
    assert rows.replacements == []
    # No second listing: the plan's own listing is what tells a refusal from an absent root.
    assert len(svc.listing_calls) == 1


def test_a_clear_that_raises_fails_every_member_in_composed_words():
    """M2: the clear is part of delivery — a raise there must not escape as
    raw exception text (the orchestrator quotes what ``write`` raises); the
    category aborts in the stage's own words, and nothing is uploaded onto a
    root in an unknown state.
    """

    class _BuggyClear(FakeResourceFileService):
        async def delete_dir(self, **kwargs):
            raise RuntimeError("rmtree quoted a header with a token")

    archive = _tgz({"a.md": b"A"})
    svc = _BuggyClear()
    svc.record_present("data/old.md")  # something to delete, so the clear reaches the port
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="claude_code")
    entries = [
        {"path": "data/tools/", "unpack": "tar.gz", "source": _src("t.tgz")},
        {"path": "data/notes/r.md", "content": "# rules"},
    ]
    with pytest.raises(ResourcesRootNotCleared) as caught:
        _write_through(m, ctx, entries)
    # the raw exception text never reaches the report; it is the chained cause, for the log
    assert str(caught.value) == ROOT_NOT_CLEARED_REASON and "token" not in str(caught.value)
    assert "token" in str(caught.value.__cause__)
    assert svc.writes == {}


def test_an_empty_root_is_already_clear():
    """A first apply onto a root with nothing unprotected: the clear's listing finds nothing to delete, so
    nothing is deleted and nothing is refused — the transports' ``False`` is never asked for."""
    svc = FakeResourceFileService()
    svc.fail_clear = True  # every delete would answer False; none is made
    archive = _tgz({"a.md": b"A"})
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m,
        ctx,
        [{"path": "data/tools/", "unpack": "tar.gz", "source": _src("t.tgz")}],
    )
    assert [r.outcome.value for r in results] == ["created"]
    assert svc.writes == {("data/tools", "a.md"): b"A"}
    assert plan.removals == ()
    assert len(svc.listing_calls) == 1 and len(svc.clear_calls) == 1
    assert svc.delete_dir_calls == [] and svc.delete_calls == []


def test_a_refused_clear_over_members_that_were_all_present_is_a_failure():
    """The plan's second signal: no removal at all, every member already
    under the root (``updated``) — the root had something to delete, so a
    ``False`` is a refusal, with no second listing needed."""
    svc = FakeResourceFileService()
    svc.record_present("data/notes/r.md")
    svc.fail_clear = True
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/notes/r.md", "content": "# rules"}]))
    plan = _run(m.plan(ctx, resolved.intents))
    assert plan.removals == () and [e.outcome for e in plan.entries] == ["updated"]
    with pytest.raises(ResourcesRootNotCleared):
        _run(m.write(ctx, plan))
    assert svc.writes == {} and len(svc.listing_calls) == 1


def test_a_file_the_bot_wrote_after_the_plan_listed_an_empty_root_still_fails_a_refused_clear():
    """The window between ``plan`` and ``write``: the root was empty when
    listed, the bot wrote under it since, and the transport refused the
    clear with a ``False``. The plan cannot see the file; the second listing
    does, and the category aborts rather than uploading over it."""
    svc = FakeResourceFileService()
    svc.fail_clear = True
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/notes/r.md", "content": "# rules"}]))
    plan = _run(m.plan(ctx, resolved.intents))
    assert plan.removals == () and [e.outcome for e in plan.entries] == ["created"]
    svc.record_present("data/scratch/notes.txt")
    with pytest.raises(ResourcesRootNotCleared):
        _run(m.write(ctx, plan))
    assert svc.writes == {} and svc.present == {"data/scratch/notes.txt"}


def test_a_plan_whose_listing_fails_aborts_in_the_stages_words_with_the_cause_chained():
    """The plan's listing is the other place a transport error could reach
    the report verbatim (``plan failed: …`` interpolates what ``plan``
    raises); the stage raises its own words and keeps the cause for the
    log, and nothing has been touched."""
    svc = FakeResourceFileService()
    svc.fail_list = True
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/notes/r.md", "content": "# rules"}]))
    with pytest.raises(ResourcesRootNotListed) as caught:
        _run(m.plan(ctx, resolved.intents))
    assert str(caught.value) == ROOT_NOT_LISTED_REASON and "token" not in str(caught.value)
    assert "token" in str(caught.value.__cause__)
    assert svc.clear_calls == [] and svc.writes == {}


def test_a_second_listing_that_fails_aborts_with_the_cause_chained_for_the_log():
    """A transport that could not delete may not list either; the port
    raises for that rather than answer empty, and the stage aborts — the
    report in its own words, the transport's error chained as the cause so
    the server log still says what happened."""
    svc = FakeResourceFileService()
    svc.fail_clear = True
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/notes/r.md", "content": "# rules"}]))
    plan = _run(m.plan(ctx, resolved.intents))
    svc.fail_list = True
    with pytest.raises(ResourcesRootNotCleared) as caught:
        _run(m.write(ctx, plan))
    assert str(caught.value) == ROOT_NOT_CLEARED_REASON
    assert isinstance(caught.value.__cause__, RuntimeError) and "token" in str(caught.value.__cause__)
    assert svc.writes == {}


def test_a_rows_failure_raises_confirmed_partial_write_after_the_files_landed():
    """The files are on the bot and the rows are not: exactly what
    ``ConfirmedPartialWriteError`` tells the orchestrator to report. The
    write is not rolled back — the transport has no transaction — and the
    error never swallows the rows failure."""
    svc = FakeResourceFileService()
    factory, rows = resource_rows_for(FakeResourceRows(fail=True))
    m = _materialiser(svc, _StubDeclaredSourceResolver(), resource_rows=factory)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/a.md", "content": "A"}]))
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    with pytest.raises(ConfirmedPartialWriteError) as raised:
        _run(m.write(ctx, plan))
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert svc.writes == {("data", "a.md"): b"A"}
    assert svc.present == {"data/a.md"}
    assert rows.replacements == []


def test_write_addresses_the_bot_owner():
    """The write chain gets the owner's address, the router's own way — for
    the clear and for every upload."""
    svc = FakeResourceFileService()
    archive = _tgz({"a.txt": b"AAA"})
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(
        bot_id="b_1",
        owner_id="u_owner",
        entity_id="man-storage-key",
        engine_type="claude_code",
    )
    _write_through(
        m, ctx, [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t")}]
    )
    assert svc.clear_calls == [
        {
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
            "engine_type": "claude_code",
        }
    ]
    assert svc.upload_calls == [
        {
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
            "engine_type": "claude_code",
            "target_dir": "data/wrap",
            "filename": "a.txt",
        }
    ]
    assert svc.delete_calls == []


def test_write_is_player_setup_convergent():
    """Applying N times equals applying once: same files, same rows, no growth.

    Both applies run against the SAME service state: the second pass is the
    convergence claim (the root from apply #1 is cleared and rewritten, no
    duplicate rows, no stale entries), not a re-observation of apply #1.
    """
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService()
    factory, rows = resource_rows_for()
    for _ in range(2):
        m = _materialiser(svc, _StubDeclaredSourceResolver(archive), resource_rows=factory)
        ctx = make_context(engine_type="claude_code")
        _write_through(
            m,
            ctx,
            [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t.tgz")}],
        )
    assert svc.writes == {("data/wrap", "a.txt"): b"AAA"}
    assert svc.present == {"data/wrap/a.txt"}
    # The first clear found an empty root; the second removed apply #1's directory whole.
    assert svc.deleted == ["data"]
    assert len(svc.clear_calls) == 2
    assert len(rows.replacements) == 2
    assert rows.rows == [WrittenFile("data/wrap/a.txt", 3)]


def test_write_failure_yields_failed_entry_per_member():
    class _Buggy(FakeResourceFileService):
        async def upload_file(self, **kw):
            if kw["filename"] == "b.txt":
                raise RuntimeError("transport down")
            return await super().upload_file(**kw)

    svc = _Buggy(exists_paths=set())
    factory, rows = resource_rows_for()
    archive = _tgz({"a.txt": b"A", "b.txt": "B".encode()})
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive), resource_rows=factory)
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m,
        ctx,
        [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t.tgz")}],
    )
    outcomes = {(r.identity, r.outcome.value) for r in results}
    assert ("data/wrap/b.txt", "failed") in outcomes
    assert ("data/wrap/a.txt", "failed") not in outcomes
    # The report row states the failure in its own words, never raw
    # exception text that might carry a credential.
    assert all(
        "transport down" not in (r.reason or "")
        for r in results
        if r.outcome.value == "failed"
    )
    assert [r.reason for r in results if r.outcome.value == "failed"] == ["resource delivery failed"]
    assert svc.writes == {("data/wrap", "a.txt"): b"A"}
    # The rows describe what landed, not what was declared.
    assert rows.rows == [WrittenFile("data/wrap/a.txt", 1)]


# --- registration: the fifth materialiser, and the artifact contract (Task 6) ---


def test_module_never_imports_the_artifact_contract():
    """W6 acceptance: ``BotConfigArtifact`` stays untouched.

    The whole module is AST-walked (not just top-level imports) so a lazy
    import inside a function cannot slip the contract in either.
    """
    import ast
    from pathlib import Path

    import agentclaw.community.core.apply_engine.materialisers.resources as _res

    tree = ast.parse(Path(_res.__file__).read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    for name in names:
        assert "kernel.bot_config" not in name, (
            "the resources materialiser must not reach the artifact contract"
        )


def test_build_materialisers_registers_six():
    from agentclaw.community.core.apply_engine.registry import (
        build_materialisers,
    )
    from agentclaw.community.core.apply_engine.capabilities import (
        Category,
    )

    registry = build_materialisers(
        script_service=object(),
        activation_service=object(),
        mcp_auth_service=object(),
        mcp_config_service=object(),
        identity_service=object(),
        upload_service=object(),
        capability_reader=object(),
        package_validator=object(),
        content=object(),
        resource_service=object(),
        resource_rows=resource_rows_for()[0],
        cli_tool_service=object(),
    )
    assert Category.RESOURCES in registry
    assert len(registry) == 6


# --- fetch categories and the keep_last contract (review findings) ---


def test_each_form_fetches_under_its_own_category():
    """Files fetch as ``resources_file``, archives as ``resources_archive``.

    Schema §5 states the two widths separately (100MB vs 200MB); a shared
    "resources" would leave the fetch funnel's cap lookup to its fallback,
    silently halving what the archive form allows — and the W11 linkage
    column would carry a category the vocabulary never defined.
    """
    archive = _tgz({"a.txt": b"x"})
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(archive)
    m = _materialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.md", "source": _src("a.bin")},
                {
                    "path": "data/tools/",
                    "unpack": "tar.gz",
                    "source": _src("t.tgz"),
                },
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    assert [c["category"] for c in stub.calls] == [
        "resources_file",
        "resources_archive",
    ]
    assert [c["entry_identity"] for c in stub.calls] == ["data/a.md", "data/tools/"]


def test_keep_last_fallback_surfaces_as_the_entries_note():
    """§9.6: a keep_last row states the fallback.

    The fetch pipeline hands the reason over as
    ``FetchedEntry.fallback_reason``; the materialiser must carry it into the
    intent's note — identity and skills already do, and a silent fallback is
    the contract broken quietly.
    """

    class _FallingBack(_StubDeclaredSourceResolver):
        def fetch(self, ctx, **kwargs):
            entry = super().fetch(ctx, **kwargs)
            return FetchedEntry(
                content=entry.content,
                digest=entry.digest,
                from_store=True,
                content_type=entry.content_type,
                fallback_reason=(
                    "delivered from the platform's stored copy (keep_last)"
                ),
            )

    svc = FakeResourceFileService()
    m = _materialiser(svc, _FallingBack())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "data/a.md", "source": _src("a.bin")}])
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    assert (
        resolved.intents[0].note
        == "delivered from the platform's stored copy (keep_last)"
    )


def test_write_carries_the_note_onto_the_report_row():
    """The note reaches the report row — stating a keep_last fallback only in
    the log is §9.6 broken quietly."""

    class _FallingBack(_StubDeclaredSourceResolver):
        def fetch(self, ctx, **kwargs):
            entry = super().fetch(ctx, **kwargs)
            return FetchedEntry(
                content=entry.content,
                digest=entry.digest,
                from_store=True,
                content_type=entry.content_type,
                fallback_reason="fell back (keep_last)",
            )

    svc = FakeResourceFileService()
    m = _materialiser(svc, _FallingBack())
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m, ctx, [{"path": "data/a.md", "source": _src("a.bin")}]
    )
    assert [r.note for r in results] == ["fell back (keep_last)"]


# --- the write chain's admission rules, asked before the root is cleared (H1) ---


def test_an_archive_member_the_chain_would_refuse_fails_at_resolve():
    """``upload_file`` refuses extensions outside its allow-list (no ``.sh``,
    no extensionless files). That refusal must land in ``resolve`` — before
    the write stage clears the root — or the category reaches write with a
    member that deterministically fails *after* the destructive clear, on
    every re-apply. The failure is keyed to the *declared* entry's identity
    with the member named in the reason, so the orchestrator's abort
    mapping can blame the declared row.
    """
    archive = _tgz({"a.md": b"ok", "run.sh": b"#!/bin/sh\n"})
    svc = FakeResourceFileService(exists_paths={"data/tools/old.md"})
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [{"path": "data/tools/", "unpack": "tar.gz", "source": _src("t.tgz")}],
        )
    )
    # The whole category aborts (§3.2): one undeliverable member means the
    # root is NOT cleared for a partial delivery.
    assert not resolved.ok
    assert resolved.failures[0].identity == "data/tools/"
    # The reason names the offending member inside the declared entry.
    assert "data/tools/run.sh" in resolved.failures[0].reason
    assert "file type" in resolved.failures[0].reason.lower()
    assert svc.clear_calls == [] and svc.present == {"data/tools/old.md"}


def test_an_extensionless_inline_file_fails_at_resolve():
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/LICENSE", "content": "MIT"}]))
    assert not resolved.ok
    assert "file type" in resolved.failures[0].reason.lower()


def test_inline_content_over_the_size_cap_fails_at_resolve(
    monkeypatch,
):
    """Inline ``content`` never goes through the fetch funnel's caps, so the
    delivery gate is the only line a 600MB inline entry meets — and it must
    meet it in ``resolve``, not as a write-stage surprise."""
    from agentclaw.community.core.resources.services import file_service as fs

    monkeypatch.setattr(fs, "MAX_FILE_SIZE", 16)
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "data/big.md", "content": "x" * 32}])
    )
    assert not resolved.ok
    # The write chain's own words — the gate delegates to the same
    # predicate ``upload_file`` enforces, so the refusal texts agree.
    assert "too large" in resolved.failures[0].reason.lower()


def test_strip_components_of_the_wrong_type_is_a_resolve_failure():
    """A document from before the schema's type check: no raw ``TypeError``
    may escape ``resolve`` — the orchestrator would abort the category with
    the exception's text in it."""
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(_tgz({"a.md": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "data/tools/",
                    "unpack": "tar.gz",
                    "strip_components": "one",
                    "source": _src("t.tgz"),
                }
            ],
        )
    )
    assert not resolved.ok
    assert "strip_components" in resolved.failures[0].reason


# --- report shape: the removals channel, end to end (review F4) ---


def _resources_engine(svc, fetcher, *, resource_rows=None):
    """An orchestrator over the real registry and the real compiler, resources wired to the rig.

    One content store for both halves: the compiler files every entry's
    bytes there, and the materialiser reads them back by digest.
    """
    content = FakeManifestContent()
    orchestrator = ApplyOrchestrator(
        build_materialisers(
            script_service=FakeStartupScriptService(),
            activation_service=FakeActivationService(),
            mcp_auth_service=FakeMcpAuth(),
            mcp_config_service=object(),
            identity_service=FakeIdentityService(),
            upload_service=FakeSkillUploadService(),
            capability_reader=FakeCapabilityReader(),
            package_validator=real_validator(),
            content=content,
            resource_service=svc,
            resource_rows=resource_rows or resource_rows_for()[0],
            cli_tool_service=object(),
        ),
        steps=_arca_steps
    )
    return EngineRig(orchestrator, manifest_compiler(content=content, resolver=fetcher))


_DOCUMENT = {
    "schema_version": 1,
    "manifest": {
        "resources": [
            {
                "path": "data/wrap/",
                "unpack": "tar.gz",
                "source": _src("t.tgz"),
            },
            {"path": "data/notes/r.md", "content": "# rules"},
        ]
    },
}


def _resources_category(report):
    return next(c for c in report.categories if c.construct.value == "resources")


def test_dry_run_and_apply_report_the_same_resources_shape():
    """F4: preview and execution must answer in one shape.

    Both paths take the same plan, so a dry run reports the same rows and the
    same ``removals`` — the bot-written file that is about to go — while
    writing nothing: no clear, no upload, no rows. The execution delivers.
    """
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService()
    svc.record_present("data/stale.md")
    factory, rows = resource_rows_for()
    engine = _resources_engine(svc, _StubDeclaredSourceResolver(archive), resource_rows=factory)
    ctx = make_context(engine_type="claude_code")

    dry = _run(
        engine.apply(
            ctx, _DOCUMENT, apply_id="a1", trigger="explicit",
            started_at=datetime.now(), dry_run=True,
        )
    )
    # The preview wrote nothing: the root still holds the stale file.
    assert svc.writes == {} and svc.deleted == [] and svc.clear_calls == []
    assert svc.present == {"data/stale.md"}
    assert rows.replacements == []

    real = _run(
        engine.apply(
            ctx, _DOCUMENT, apply_id="a2", trigger="explicit",
            started_at=datetime.now(), dry_run=False,
        )
    )

    for report in (dry, real):
        resources = _resources_category(report)
        assert resources.removals == ("data/stale.md",)
        assert resources.as_dict()["removed"] == ["data/stale.md"]
        assert not resources.aborted
    dry_rows = {e.identity: e.outcome.value for e in dry.entries}
    real_rows = {e.identity: e.outcome.value for e in real.entries}
    assert dry_rows == {"data/notes/r.md": "created", "data/wrap/a.txt": "created"}
    assert real_rows == {"data/notes/r.md": "created", "data/wrap/a.txt": "created"}
    # The execution delivered.
    assert svc.writes == {("data/notes", "r.md"): b"# rules", ("data/wrap", "a.txt"): b"AAA"}
    assert svc.deleted == ["data"]  # the root entry, whole
    assert svc.present == {"data/notes/r.md", "data/wrap/a.txt"}
    assert rows.rows == [WrittenFile("data/wrap/a.txt", 3), WrittenFile("data/notes/r.md", 7)]


def test_a_declared_empty_category_clears_the_root_and_reports_every_removal():
    """``resources: []`` is a declaration, not an omission: the root converges
    to nothing. Every listed file is in ``removals`` (both shapes), the
    clear happens for real, nothing is delivered, the rows become ``[]`` —
    and a protected entry stays.
    """
    svc = FakeResourceFileService()
    svc.record_present("data/gone/old.md", "data/x.csv", "SOUL.md")
    factory, rows = resource_rows_for()
    engine = _resources_engine(svc, _StubDeclaredSourceResolver(), resource_rows=factory)
    ctx = make_context(engine_type="claude_code")
    document = {"schema_version": 1, "manifest": {"resources": []}}

    dry = _run(
        engine.apply(
            ctx, document, apply_id="a1", trigger="explicit",
            started_at=datetime.now(), dry_run=True,
        )
    )
    assert svc.deleted == [] and svc.clear_calls == [] and rows.replacements == []
    real = _run(
        engine.apply(
            ctx, document, apply_id="a2", trigger="explicit",
            started_at=datetime.now(), dry_run=False,
        )
    )

    for report in (dry, real):
        resources = _resources_category(report)
        assert resources.removals == ("data/gone/old.md", "data/x.csv")
        assert resources.as_dict()["removed"] == ["data/gone/old.md", "data/x.csv"]
        assert not resources.aborted
    assert [e.identity for e in real.entries] == []
    # The root was cleared for real, and nothing was delivered.
    assert svc.deleted == ["data"]  # the root entry, whole
    assert svc.writes == {}
    assert svc.present == {"SOUL.md"}
    assert rows.replacements == [{"files": [], "owner_id": ctx.owner_id, "created_by": ctx.actor_id}]


def test_a_rows_failure_reports_a_confirmed_partial_write():
    """The orchestrator's half of the rows contract: a ``ConfirmedPartialWriteError``
    out of ``write`` aborts the category as *partially written* and
    *confirmed* — the files are on the bot, the rows are not — never as
    converged, and never as untouched."""
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService()
    factory, _rows = resource_rows_for(FakeResourceRows(fail=True))
    engine = _resources_engine(svc, _StubDeclaredSourceResolver(archive), resource_rows=factory)
    ctx = make_context(engine_type="claude_code")

    report = _run(
        engine.apply(
            ctx, _DOCUMENT, apply_id="a1", trigger="explicit",
            started_at=datetime.now(), dry_run=False,
        )
    )

    resources = _resources_category(report)
    assert resources.aborted and resources.partially_written and resources.confirmed_partial
    assert {e.identity for e in report.entries} == {"data/wrap/", "data/notes/r.md"}
    assert all(e.outcome.value == "failed" for e in report.entries)
    assert all((e.reason or "").startswith("write failed") for e in report.entries)
    # The files did land; it is the rows that did not.
    assert svc.writes == {("data/notes", "r.md"): b"# rules", ("data/wrap", "a.txt"): b"AAA"}


def test_a_refused_member_blames_the_declared_entry_in_the_report():
    """F3: the report must say which member was undeliverable and why.

    The orchestrator's abort mapping keys failure reasons onto *declared*
    entry identities — a member-keyed failure matches nothing and both
    declared rows answer a generic "another entry could not be
    materialized", leaving the author to guess which of the archive's
    members violated the write chain's admission and in what way.
    """
    archive = _tgz({"a.md": b"ok", "run.sh": b"#!/bin/sh\n"})
    svc = FakeResourceFileService(exists_paths={"data/tools/old.md"})
    engine = _resources_engine(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="claude_code")

    report = _run(
        engine.apply(
            ctx,
            {
                "schema_version": 1,
                "manifest": {
                    "resources": [
                        {
                            "path": "data/tools/",
                            "unpack": "tar.gz",
                            "source": _src("t.tgz"),
                        },
                        {"path": "data/notes/r.md", "content": "# rules"},
                    ]
                },
            },
            apply_id="a1", trigger="explicit",
            started_at=datetime.now(), dry_run=False,
        )
    )

    rows = {(e.identity, e.outcome.value): e for e in report.entries}
    (identity, outcome), row = next(
        item for item in rows.items() if item[0][0] == "data/tools/"
    )
    assert outcome == "failed"
    assert "run.sh" in (row.reason or "")
    assert "file type" in (row.reason or "").lower()
    # The blameless neighbour keeps the generic skip wording.
    assert rows[("data/notes/r.md", "skipped")].reason is not None
    # Nothing was written and the root still stands.
    assert svc.writes == {}
    assert svc.deleted == [] and svc.clear_calls == []
    assert svc.present == {"data/tools/old.md"}


# --- the write address's engine half: the runtime routing (F2) ---


def test_routed_bots_address_the_runtime_engine_workspace():
    """F2: the workspace address routes the engine the router's way.

    The resources router resolves the engine through the runtime routing
    policy (``claude_code`` + a non-``normalCC`` template ⇒ ``aicoding``)
    before composing ``{bot_dir}/{engine}/workspace``; a raw
    ``active_engine`` would deliver the files into a tree neither the
    resources console nor the bot reads while the report says SUCCEEDED.
    """
    archive = _tgz({"a.txt": b"A"})
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(
        engine_type="claude_code", bot={"template_type": "applicationCoding"}
    )
    _write_through(
        m, ctx, [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t")}]
    )
    assert svc.clear_calls[0]["engine_type"] == "aicoding"
    assert all(c["engine_type"] == "aicoding" for c in svc.upload_calls)
    assert all(c["engine_type"] == "aicoding" for c in svc.listing_calls)
    assert svc.exists_probes == []


def test_unrouted_bots_keep_the_active_engine():
    """The control cases: no template, the ``normalCC`` template, and a
    plain non-claude_code engine all keep the address on the active engine
    — the routing policy must be the only thing that moves the address."""
    archive = _tgz({"a.txt": b"A"})
    cases = [
        {},  # fake default: claude_code, no template_type
        {"template_type": "normalCC"},
    ]
    for overlay in cases:
        svc = FakeResourceFileService()
        m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
        ctx = make_context(engine_type="claude_code", bot=overlay)
        _write_through(
            m, ctx, [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t")}]
        )
        assert svc.clear_calls[0]["engine_type"] == "claude_code"

    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="openclaw")
    _write_through(
        m, ctx, [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t")}]
    )
    assert svc.clear_calls[0]["engine_type"] == "openclaw"


def test_an_engineless_bot_record_falls_back_to_the_context_engine():
    """A record without a readable engine (``active_engine`` None) falls
    back to ``ctx.engine_type`` — never an empty string, which the path
    factory would compose into the bot root instead of an engine dir."""
    archive = _tgz({"a.txt": b"A"})
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="openclaw", bot={"active_engine": None})
    _write_through(
        m, ctx, [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t")}]
    )
    assert svc.clear_calls[0]["engine_type"] == "openclaw"


# --- the apply-time path belt re-asks the schema's own rule (F6) ---


def test_a_tilde_path_is_refused_at_resolve():
    """A document that predates the validator carries the PUT layer's
    refusals into apply: "~" is an absolute-ish path in exactly the way
    "/" is, and the old belt (only "/", ".." segments) let it through to
    the write chain."""
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "~/x.md", "content": "x"}]))
    assert not resolved.ok
    assert resolved.failures[0].identity == "~/x.md"
    assert "~" in resolved.failures[0].reason


def test_a_windows_drive_path_is_refused_at_resolve():
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "C:evil.md", "content": "x"}]))
    assert not resolved.ok
    assert "workspace-relative" in resolved.failures[0].reason


def test_duplicate_declared_paths_abort_the_category():
    """The schema refuses a duplicate resource path at PUT; the belt must
    re-ask it, or two entries at one path produce two intents with one
    identity, two report rows, and a last-write-wins order no rule defines."""
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.md", "content": "first"},
                {"path": "data/a.md", "source": _src("a")},
            ],
        )
    )
    assert not resolved.ok
    # The mcp/siblings' "seen" refusal convention: the duplicate is blamed
    # and the category aborts (§3.2) — the intents resolved before the
    # refusal are never consumed, the orchestrator aborts on ``not ok``
    # without looking at them. Nothing writable survives the apply.
    assert any("more than once" in f.reason for f in resolved.failures)


def test_an_unknown_unpack_kind_is_refused():
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(_tgz({"a.md": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "data/wrap/",
                    "unpack": "tgz",
                    "source": _src("t.tgz"),
                }
            ],
        )
    )
    assert not resolved.ok
    assert "zip|tar.gz" in resolved.failures[0].reason


def test_refusals_quote_the_write_chains_own_words():
    """The resolve gate's verdict is the write chain's own text`` —`

    delegated to the one predicate (``file_service.admission_refusal``)
    rather than restated, the two surfaces cannot drift into disagreeing
    about what they admit.
    """
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/run.sh", "content": "#"}]))
    assert not resolved.ok
    assert resolved.failures[0].reason.startswith("File type not allowed")


# --- the port the wiring keys on (F7) ---


def test_the_port_mirrors_the_apply_side_call_surface():
    """The port is apply's call surface, exactly — and a keyword subset of
    the real service.

    A ``@runtime_checkable`` isinstance checks method *presence* only, so
    nothing else notices a signature drift; pinned here by reflection:
    every parameter the port declares must exist on the real
    ``ResourceFileService`` method with the same declared default (the
    fake mirrors the port in turn). The port is deliberately *narrower*
    than the real service — ``preserve_structure``, ``publish_id`` /
    ``device_uuid`` and ``runtime_context`` are the router's vocabulary,
    not apply's.
    """
    import inspect

    from agentclaw.community.core.ports.resource_file_port import (
        ResourceFilePort,
    )
    from agentclaw.community.core.services.resource_file_service import (
        ResourceFileService,
    )

    def _kwparams(func) -> dict[str, Any]:
        return {
            name: param.default
            for name, param in inspect.signature(func).parameters.items()
            if param.kind == inspect.Parameter.KEYWORD_ONLY
        }

    for method in ("upload_file", "delete", "exists", "list_entries", "delete_dir"):
        port_params = _kwparams(getattr(ResourceFilePort, method))
        real_params = _kwparams(getattr(ResourceFileService, method))
        # Every port parameter exists on the real service...
        assert set(port_params) <= set(real_params), (method, port_params)
        # ...with the same declared default where the port declares one.
        for name, default in port_params.items():
            assert real_params[name] == default, (method, name)
        # ...and every parameter the port declares is one apply passes.
        assert "preserve_structure" not in port_params, method
        assert "runtime_context" not in port_params, method
        # The one divergence the apply side must not reintroduce: ``exists``
        # is addressed like ``delete`` and ``upload_file``, entity and all.
        assert "entity_type" in _kwparams(ResourceFilePort.exists)
    # The listing and the directory delete are general: a prefix (``""`` is the root) and the engine's
    # own recursive flag, a path, and the stage addressing of a service bot — and the fake copies the shape.
    addressing = {"entity_type", "entity_id", "bot_id", "engine_type", "publish_id", "device_uuid"}
    for cls in (ResourceFilePort, FakeResourceFileService):
        assert set(_kwparams(cls.list_entries)) == addressing | {"prefix", "recursive"}, cls
        assert set(_kwparams(cls.delete_dir)) == addressing | {"path"}, cls


def test_an_unhashable_unpack_kind_is_refused_not_raised():
    """The membership check is against a frozenset (``VALID_UNPACK``); an
    unhashable ``unpack`` (a YAML list, from a validator-bypassing document)
    must still get the belt's clean refusal — a raw ``TypeError`` would
    leave the category's failure text to the orchestrator's exception
    path instead of the stage's own words.
    """
    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(_tgz({"a.md": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "data/wrap/",
                    "unpack": ["zip"],
                    "source": _src("t.tgz"),
                }
            ],
        )
    )
    assert not resolved.ok
    assert "zip|tar.gz" in resolved.failures[0].reason


# --- resources over git (defect D1) -----------------------------------------
#
# The category that could not name a source. Every case below is a shape the
# object road already had and ``resources`` could not reach, plus the one
# shape only git gives: a tree that arrives as a tree.

_REPO = "https://code.example.com/team/content.git"

_TREE = {
    "kb/faq.csv": b"q,a\n",
    "kb/pricing.csv": b"sku,price\n",
    "kb/deep/nested/notes.md": b"# nested\n",
    "elsewhere/ignore.md": b"not selected\n",
}


def _git_session(*, subpath=None, auth=None):
    """A source session declaring one git source named ``content``.

    It rides on the compiler's session (not the apply context, which no
    longer carries one), so it is handed to the rig rather than the call.
    """
    decl = {"protocol": "git", "url": _REPO, "ref": "v1.2.0"}
    if subpath is not None:
        decl["subpath"] = subpath
    if auth is not None:
        decl["auth"] = auth
    return declared_session(sources={"content": decl})


def _git_rig(svc, stub, *, subpath=None, auth=None):
    """The materialiser over ``stub``, resolving ``from: content`` through :func:`_git_session`."""
    return _materialiser(svc, stub, source_session=_git_session(subpath=subpath, auth=auth))


def _ctx():
    return make_context(engine_type="claude_code")


def test_a_file_entry_reads_one_file_out_of_a_git_source():
    """D1's simplest half: ``from:`` resolves, and one file lands at ``path``."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(git_trees={_REPO: _TREE})
    m = _git_rig(svc, stub)
    resolved = _run(
        m.resolve(
            _ctx(),
            [{"path": "data/faq.csv", "from": "content", "subpath": "kb/faq.csv"}],
        )
    )
    assert resolved.ok
    assert [(i.identity, i.value) for i in resolved.intents] == [
        ("data/faq.csv", b"q,a\n")
    ]


def test_a_directory_entry_over_git_takes_the_tree_recursively():
    """Every file at every depth, and nothing outside the selected subtree.

    The nesting is the point: ``kb/deep/nested/notes.md`` is three levels below
    the selected root and must arrive, while ``elsewhere/`` — outside the
    source's subpath — must not. A shallow walk would pass a one-level fixture
    and quietly truncate real trees.
    """
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(git_trees={_REPO: _TREE})
    m = _git_rig(svc, stub, subpath="kb")
    resolved = _run(
        m.resolve(
            _ctx(),
            [{"path": "data/kb/", "from": "content"}],
        )
    )
    assert resolved.ok
    # Members only — the tree's replacement is the write stage's clear of the
    # root, not an intent riding ahead of the members.
    assert {i.identity: i.value for i in resolved.intents} == {
        "data/kb/deep/nested/notes.md": b"# nested\n",
        "data/kb/faq.csv": b"q,a\n",
        "data/kb/pricing.csv": b"sku,price\n",
    }


def test_a_directory_entry_over_git_declares_no_unpack():
    """``unpack`` exists because one HTTPS GET fetches one object. Git hands
    over a real tree, so requiring it here would be asking the caller to
    configure a packaging step that does not happen."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(git_trees={_REPO: _TREE})
    m = _git_rig(svc, stub, subpath="kb")
    resolved = _run(
        m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}])
    )
    assert resolved.ok, resolved.failures


def test_a_file_removed_upstream_disappears_on_the_next_apply():
    """The root is owned whole: the declared area is replaced wholesale, not
    merged. Modelled as two applies over a shrinking repository — the file
    the second tree no longer carries is gone from the bot and named in the
    second apply's ``removals``."""
    svc = FakeResourceFileService()
    ctx = _ctx()
    m = _git_rig(svc, _StubDeclaredSourceResolver(git_trees={_REPO: _TREE}), subpath="kb")
    _write_through(m, ctx, [{"path": "data/kb/", "from": "content"}])
    assert "data/kb/pricing.csv" in svc.present

    shrunk = {k: v for k, v in _TREE.items() if k != "kb/pricing.csv"}
    m2 = _git_rig(svc, _StubDeclaredSourceResolver(git_trees={_REPO: shrunk}), subpath="kb")
    plan, _results = _write_through(m2, ctx, [{"path": "data/kb/", "from": "content"}])
    assert plan.removals == ("data/kb/pricing.csv",)
    # The clear is what makes the removal happen on the bot rather than only
    # in this list.
    assert svc.present == {"data/kb/deep/nested/notes.md", "data/kb/faq.csv"}


def test_a_member_refused_by_admission_aborts_with_the_root_still_standing():
    """The gate runs before the clear. One undeliverable member must fail the
    category *without* a clear — otherwise every re-apply deterministically
    empties the root and then fails to refill it."""
    svc = FakeResourceFileService()
    svc.record_present("data/kb/ok.md")
    oversized = {"kb/ok.md": b"fine\n", "kb/bad.exe": b"\x00" * 8}
    m = _git_rig(svc, _StubDeclaredSourceResolver(git_trees={_REPO: oversized}), subpath="kb")
    resolved = _run(
        m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}])
    )
    assert not resolved.ok
    assert resolved.intents == ()
    assert "bad.exe" in resolved.failures[0].reason
    assert svc.clear_calls == [] and svc.present == {"data/kb/ok.md"}


def test_two_entries_share_one_declared_git_source():
    """The declare-once-reference-many mechanism, which ``resources`` was
    excluded from: one source block, two entries, two different subpaths."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(git_trees={_REPO: _TREE})
    m = _git_rig(svc, stub, subpath="kb")
    resolved = _run(
        m.resolve(
            _ctx(),
            [
                {"path": "data/faq.csv", "from": "content", "subpath": "faq.csv"},
                {
                    "path": "data/pricing.csv",
                    "from": "content",
                    "subpath": "pricing.csv",
                },
            ],
        )
    )
    assert resolved.ok, resolved.failures
    assert [(i.identity, i.value) for i in resolved.intents] == [
        ("data/faq.csv", b"q,a\n"),
        ("data/pricing.csv", b"sku,price\n"),
    ]


def test_a_git_sourced_entry_files_its_bytes_with_the_platform_store():
    """§2.8: audit and ``keep_last`` read one log, whichever road served the
    bytes. The credential **name** rides with it — never a value — so the
    lineage answers "which credential distributed this" on both roads."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(git_trees={_REPO: _TREE})
    m = _git_rig(svc, stub, auth="corp-git")
    _run(
        m.resolve(
            _ctx(),
            [{"path": "data/faq.csv", "from": "content", "subpath": "kb/faq.csv"}],
        )
    )
    assert len(stub.filed) == 1
    filed = stub.filed[0]
    assert filed["content"] == b"q,a\n"
    assert filed["entry_identity"] == "data/faq.csv"
    assert filed["credential_name"] == "corp-git"
    assert filed["source_url"].startswith("git+")


def test_a_git_sourced_tree_files_one_receipt_not_one_per_member():
    """A 5000-file tree is one delivery. One receipt per member would make the
    audit log describe the transport instead of the entry."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(git_trees={_REPO: _TREE})
    m = _git_rig(svc, stub, subpath="kb")
    _run(
        m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}])
    )
    assert len(stub.filed) == 1
    assert stub.filed[0]["entry_identity"] == "data/kb/"
    # The canonical form is deterministic and injective: same tree, same bytes.
    from agentclaw.community.core.content_store import (
        canonical_tree_bytes,
    )

    assert canonical_tree_bytes([("a", b"bc")]) == canonical_tree_bytes(
        [("a", b"bc")]
    )
    # A path/payload pair that a delimiter-joined encoding would confuse.
    assert canonical_tree_bytes([("a", b"b"), ("c", b"d")]) != (
        canonical_tree_bytes([("a", b"b\nc"), ("", b"d")])
    )


def test_a_moved_ref_rides_into_the_entrys_note():
    """§9.6: a non-strict source that moved reports the move on the entry."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(git_trees={_REPO: _TREE}, moved_from="deadbeef")
    m = _git_rig(svc, stub)
    resolved = _run(
        m.resolve(
            _ctx(),
            [{"path": "data/faq.csv", "from": "content", "subpath": "kb/faq.csv"}],
        )
    )
    assert resolved.ok
    assert "deadbeef" in resolved.intents[0].note


def test_an_undeclared_from_fails_the_entry_not_the_process():
    svc = FakeResourceFileService()
    m = _git_rig(svc, _StubDeclaredSourceResolver(git_trees={_REPO: _TREE}))
    resolved = _run(
        m.resolve(_ctx(), [{"path": "data/x.md", "from": "nowhere"}])
    )
    assert not resolved.ok
    assert "nowhere" in resolved.failures[0].reason


# ── keep_last for a git-delivered tree (review finding) ────────────────────


def test_the_canonical_tree_form_round_trips():
    """`keep_last` is the DEFAULT, and it promises the bot keeps running what
    it has. A stored form with no decoder cannot keep that promise, so the
    canonical bytes are reversible and self-describing."""
    from agentclaw.community.core.content_store import (
        canonical_tree_bytes,
        decode_tree_bytes,
    )

    members = [
        ("faq.csv", b"q,a\n"),
        ("deep/nested/notes.md", b"# nested\n"),
        # A payload containing the framing characters, and an empty file: both
        # are why the encoding is length-prefixed rather than delimiter-joined.
        ("odd:name.txt", b"12:not-a-frame\n"),
        ("empty", b""),
    ]
    blob = canonical_tree_bytes(members)
    assert decode_tree_bytes(blob) == sorted(members)


def test_bytes_that_are_not_a_canonical_tree_decode_to_none():
    """`None` is an answer, not a fault: the caller is asking "tree or
    archive?", and a truncated receipt must not half-deliver a tree."""
    from agentclaw.community.core.content_store import (
        canonical_tree_bytes,
        decode_tree_bytes,
    )

    assert decode_tree_bytes(b"PK\x03\x04 a real zip") is None
    assert decode_tree_bytes(b"") is None
    truncated = canonical_tree_bytes([("a.md", b"0123456789")])[:-4]
    assert decode_tree_bytes(truncated) is None


def test_keep_last_delivers_a_stored_git_tree_instead_of_failing():
    """The review's P2, closed.

    A transient git outage used to fail the whole resources category with
    "a directory entry fetched over 'oss' must declare 'unpack'" — a message
    about a field a git source may not even carry. The stored copy now comes
    back as the tree it was.
    """
    from agentclaw.community.core.content_store import (
        canonical_tree_bytes,
    )

    tree = [("faq.csv", b"q,a\n"), ("deep/notes.md", b"# nested\n")]
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver()
    # What `_git_keep_last` hands back: the baseline receipt's bytes, carrying
    # the fallback reason the report must state (§9.6).
    stub.declared_override = FetchedEntry(
        content=canonical_tree_bytes(tree),
        digest="sha256:stub",
        from_store=True,
        source_url="git+https://code.example.com/team/content.git@abc/kb",
        fallback_reason=(
            "delivered from the platform's stored copy (keep_last): "
            "the git fetch failed"
        ),
    )
    m = _git_rig(svc, stub, subpath="kb")
    resolved = _run(
        m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}])
    )
    assert resolved.ok, resolved.failures
    assert {i.identity: i.value for i in resolved.intents} == {
        "data/kb/deep/notes.md": b"# nested\n",
        "data/kb/faq.csv": b"q,a\n",
    }
    # The report must say the source was tried and the stored copy stood in —
    # on every member, since the entry's note is each member's note.
    assert all("keep_last" in i.note for i in resolved.intents)


def test_a_real_archive_still_takes_the_unpack_road():
    """Recognising a stored tree must not swallow the archive case: an oss
    directory entry with a genuine zip goes on unpacking exactly as before."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(_tgz({"kb/faq.csv": b"q,a\n"}))
    m = _materialiser(svc, stub)
    resolved = _run(
        m.resolve(
            make_context(engine_type="claude_code"),
            [{
                "path": "data/kb/",
                "source": _src("kb.tgz"),
                "unpack": "tar.gz",
                "strip_components": 1,
            }],
        )
    )
    assert resolved.ok, resolved.failures
    assert "data/kb/faq.csv" in {i.identity for i in resolved.intents}


def test_an_oss_directory_entry_is_refused_before_the_network_is_touched():
    """`resolve`'s contract is that everything which can fail without touching
    the bot fails first — and a directory fetch can be 200 MiB against this
    apply's byte budget and its lock TTL. An entry a missing `unpack`
    guarantees to reject must not spend one."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver()
    m = _materialiser(svc, stub)
    resolved = _run(
        m.resolve(
            make_context(engine_type="claude_code"),
            [{"path": "data/kb/", "source": _src("kb.zip")}],
        )
    )
    assert not resolved.ok
    assert "must declare 'unpack" in resolved.failures[0].reason
    assert stub.calls == [], "a doomed entry paid for a fetch"


def test_an_invalid_strip_components_is_also_refused_before_the_fetch():
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver()
    m = _materialiser(svc, stub)
    resolved = _run(
        m.resolve(
            make_context(engine_type="claude_code"),
            [{
                "path": "data/kb/",
                "source": _src("kb.zip"),
                "unpack": "zip",
                "strip_components": -1,
            }],
        )
    )
    assert not resolved.ok
    assert "strip_components" in resolved.failures[0].reason
    assert stub.calls == []


def test_a_git_directory_entry_still_fetches_without_declaring_unpack():
    """The pre-fetch gate must not leak onto the git road, where `unpack` is
    refused at PUT and a tree needs no packaging at all."""
    svc = FakeResourceFileService()
    stub = _StubDeclaredSourceResolver(git_trees={_REPO: _TREE})
    m = _git_rig(svc, stub, subpath="kb")
    resolved = _run(
        m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}])
    )
    assert resolved.ok, resolved.failures
    assert stub.calls, "the git road did not fetch"
