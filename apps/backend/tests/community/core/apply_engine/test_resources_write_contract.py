"""Resources replacement contracts at the compiler/materialiser seam."""

from __future__ import annotations


from typing import Any


from agentclaw.community.core.bot_config_manifest.sources.entry_delivery import (
    FetchedEntry,
)


from ._fakes import (
    FakeResourceFileService,
    make_context,
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
    addressing = {
        "entity_type",
        "entity_id",
        "bot_id",
        "engine_type",
        "publish_id",
        "device_uuid",
    }
    for cls in (ResourceFilePort, FakeResourceFileService):
        assert set(_kwparams(cls.list_entries)) == addressing | {
            "prefix",
            "recursive",
        }, cls
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
    resolved = _run(m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}]))
    assert resolved.ok, resolved.failures


def test_a_file_removed_upstream_disappears_on_the_next_apply():
    """The root is owned whole: the declared area is replaced wholesale, not
    merged. Modelled as two applies over a shrinking repository — the file
    the second tree no longer carries is gone from the bot and named in the
    second apply's ``removals``."""
    svc = FakeResourceFileService()
    ctx = _ctx()
    m = _git_rig(
        svc, _StubDeclaredSourceResolver(git_trees={_REPO: _TREE}), subpath="kb"
    )
    _write_through(m, ctx, [{"path": "data/kb/", "from": "content"}])
    assert "data/kb/pricing.csv" in svc.present

    shrunk = {k: v for k, v in _TREE.items() if k != "kb/pricing.csv"}
    m2 = _git_rig(
        svc, _StubDeclaredSourceResolver(git_trees={_REPO: shrunk}), subpath="kb"
    )
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
    m = _git_rig(
        svc, _StubDeclaredSourceResolver(git_trees={_REPO: oversized}), subpath="kb"
    )
    resolved = _run(m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}]))
    assert not resolved.ok
    assert resolved.intents == ()
    assert "bad.exe" in resolved.failures[0].reason
    assert svc.listing_calls[1:] == [] and svc.present == {"data/kb/ok.md"}


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
    _run(m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}]))
    assert len(stub.filed) == 1
    assert stub.filed[0]["entry_identity"] == "data/kb/"
    # The canonical form is deterministic and injective: same tree, same bytes.
    from agentclaw.community.core.content_store import (
        canonical_tree_bytes,
    )

    assert canonical_tree_bytes([("a", b"bc")]) == canonical_tree_bytes([("a", b"bc")])
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
    resolved = _run(m.resolve(_ctx(), [{"path": "data/x.md", "from": "nowhere"}]))
    assert not resolved.ok
    assert "nowhere" in resolved.failures[0].reason


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
    resolved = _run(m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}]))
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
            [
                {
                    "path": "data/kb/",
                    "source": _src("kb.tgz"),
                    "unpack": "tar.gz",
                    "strip_components": 1,
                }
            ],
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
            [
                {
                    "path": "data/kb/",
                    "source": _src("kb.zip"),
                    "unpack": "zip",
                    "strip_components": -1,
                }
            ],
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
    resolved = _run(m.resolve(_ctx(), [{"path": "data/kb/", "from": "content"}]))
    assert resolved.ok, resolved.failures
    assert stub.calls, "the git road did not fetch"
