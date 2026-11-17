"""Resources replacement contracts at the compiler/materialiser seam."""

from __future__ import annotations





import pytest

from agentclaw.community.core.ports.resource_file_port import WorkspaceEntry

from agentclaw.community.core.ports.resource_rows_port import WrittenFile



from agentclaw.community.core.apply_engine.materialisers.resources import (
    ROOT_NOT_CLEARED_REASON,
    ROOT_NOT_LISTED_REASON,
    ResourcesRootNotCleared,
    ResourcesRootNotListed,
)





from ._fakes import (
    FakeResourceFileService,
    make_context,
    resource_rows_for,
)


from ._resources_test_support import (
    _arca_steps as _arca_steps,
    _BUCKET as _BUCKET,
    _DOCUMENT as _DOCUMENT,
    _REPO as _REPO,
    _TREE as _TREE,

    _run as _run,
    _materialiser as _materialiser,
    _src as _src,
    _RecordingStore as _RecordingStore,
    _StubDeclaredSourceResolver as _StubDeclaredSourceResolver,
    _compose as _compose,
    _StubCheckout as _StubCheckout,
    _stub_git_source as _stub_git_source,
    _tgz as _tgz,
    _write_through as _write_through,
    _resources_engine as _resources_engine,
    _resources_category as _resources_category,
    _git_session as _git_session,
    _git_rig as _git_rig,
    _ctx as _ctx,
)

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
    assert svc.clear_calls == [
        {
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
            "engine_type": "claude_code",
        }
    ]
    assert svc.exists_probes == []


def test_plan_lists_the_root_and_unprotected_subtree_without_presence_probes():
    """The root and its unprotected subtree provide the snapshot without per-file probes."""
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
    assert [(call["prefix"], call["recursive"]) for call in svc.list_calls] == [("", False), ("data", True)]
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
    assert len(svc.listing_calls) == 2
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
    # The write-stage listing confirms what was present when clear ran.
    assert len(svc.listing_calls) == 2


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
    assert len(svc.clear_calls) == 2
    assert svc.listing_calls == []
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
    assert svc.writes == {} and len(svc.listing_calls) == 2


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
    assert svc.listing_calls[1:] == [] and svc.writes == {}
