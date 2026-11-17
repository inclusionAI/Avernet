"""Resources replacement contracts at the compiler/materialiser seam."""

from __future__ import annotations


from datetime import datetime


import pytest


from agentclaw.community.core.ports.resource_rows_port import WrittenFile

from agentclaw.community.core.bot_config_manifest.sources.entry_delivery import (
    FetchedEntry,
)


from agentclaw.community.core.apply_engine.materialisers.resources import (
    ROOT_NOT_CLEARED_REASON,
    ResourcesRootNotCleared,
)


from agentclaw.community.core.apply_engine.registry import (
    ConfirmedPartialWriteError,
)

from ._empty_cli_tools import EmptyCliTools

from ._fakes import (
    FakeResourceFileService,
    FakeResourceRows,
    make_context,
    resource_rows_for,
)


from ._capability_facts import build_test_materialisers as build_materialisers
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
    assert isinstance(caught.value.__cause__, RuntimeError) and "token" in str(
        caught.value.__cause__
    )
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
    assert len(svc.clear_calls) == 2
    assert all(
        call
        == {
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
            "engine_type": "claude_code",
        }
        for call in svc.clear_calls
    )
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
        m = _materialiser(
            svc, _StubDeclaredSourceResolver(archive), resource_rows=factory
        )
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
    assert len(svc.clear_calls) == 4
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
    assert [r.reason for r in results if r.outcome.value == "failed"] == [
        "resource delivery failed"
    ]
    assert svc.writes == {("data/wrap", "a.txt"): b"A"}
    # The rows describe what landed, not what was declared.
    assert rows.rows == [WrittenFile("data/wrap/a.txt", 1)]


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


def test_build_materialisers_registers_five():
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
        cli_tool_service=EmptyCliTools(),
        shared_skills=object(),
    )
    assert Category.RESOURCES in registry
    assert len(registry) == 5


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
    resolved = _run(m.resolve(ctx, [{"path": "data/a.md", "source": _src("a.bin")}]))
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
    assert svc.listing_calls[1:] == [] and svc.present == {"data/tools/old.md"}


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
    resolved = _run(m.resolve(ctx, [{"path": "data/big.md", "content": "x" * 32}]))
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
    engine = _resources_engine(
        svc, _StubDeclaredSourceResolver(archive), resource_rows=factory
    )
    ctx = make_context(engine_type="claude_code")

    dry = _run(
        engine.apply(
            ctx,
            _DOCUMENT,
            apply_id="a1",
            trigger="explicit",
            started_at=datetime.now(),
            dry_run=True,
        )
    )
    # The preview wrote nothing: the root still holds the stale file.
    assert svc.writes == {} and svc.deleted == [] and len(svc.clear_calls) == 1
    assert svc.present == {"data/stale.md"}
    assert rows.replacements == []

    real = _run(
        engine.apply(
            ctx,
            _DOCUMENT,
            apply_id="a2",
            trigger="explicit",
            started_at=datetime.now(),
            dry_run=False,
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
    assert svc.writes == {
        ("data/notes", "r.md"): b"# rules",
        ("data/wrap", "a.txt"): b"AAA",
    }
    assert svc.deleted == ["data"]  # the root entry, whole
    assert svc.present == {"data/notes/r.md", "data/wrap/a.txt"}
    assert rows.rows == [
        WrittenFile("data/wrap/a.txt", 3),
        WrittenFile("data/notes/r.md", 7),
    ]


def test_a_declared_empty_category_clears_the_root_and_reports_every_removal():
    """``resources: []`` is a declaration, not an omission: the root converges
    to nothing. Every listed file is in ``removals`` (both shapes), the
    clear happens for real, nothing is delivered, the rows become ``[]`` —
    and a protected entry stays.
    """
    svc = FakeResourceFileService()
    svc.record_present("data/gone/old.md", "data/x.csv", "SOUL.md")
    factory, rows = resource_rows_for()
    engine = _resources_engine(
        svc, _StubDeclaredSourceResolver(), resource_rows=factory
    )
    ctx = make_context(engine_type="claude_code")
    document = {"schema_version": 1, "manifest": {"resources": []}}

    dry = _run(
        engine.apply(
            ctx,
            document,
            apply_id="a1",
            trigger="explicit",
            started_at=datetime.now(),
            dry_run=True,
        )
    )
    assert svc.deleted == [] and len(svc.clear_calls) == 1 and rows.replacements == []
    real = _run(
        engine.apply(
            ctx,
            document,
            apply_id="a2",
            trigger="explicit",
            started_at=datetime.now(),
            dry_run=False,
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
    assert rows.replacements == [
        {"files": [], "owner_id": ctx.owner_id, "created_by": ctx.actor_id}
    ]


def test_a_rows_failure_reports_a_confirmed_partial_write():
    """The orchestrator's half of the rows contract: a ``ConfirmedPartialWriteError``
    out of ``write`` aborts the category as *partially written* and
    *confirmed* — the files are on the bot, the rows are not — never as
    converged, and never as untouched."""
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService()
    factory, _rows = resource_rows_for(FakeResourceRows(fail=True))
    engine = _resources_engine(
        svc, _StubDeclaredSourceResolver(archive), resource_rows=factory
    )
    ctx = make_context(engine_type="claude_code")

    report = _run(
        engine.apply(
            ctx,
            _DOCUMENT,
            apply_id="a1",
            trigger="explicit",
            started_at=datetime.now(),
            dry_run=False,
        )
    )

    resources = _resources_category(report)
    assert (
        resources.aborted
        and resources.partially_written
        and resources.confirmed_partial
    )
    assert {e.identity for e in report.entries} == {"data/wrap/", "data/notes/r.md"}
    assert all(e.outcome.value == "failed" for e in report.entries)
    assert all((e.reason or "").startswith("write failed") for e in report.entries)
    # The files did land; it is the rows that did not.
    assert svc.writes == {
        ("data/notes", "r.md"): b"# rules",
        ("data/wrap", "a.txt"): b"AAA",
    }


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
            apply_id="a1",
            trigger="explicit",
            started_at=datetime.now(),
            dry_run=False,
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
    assert svc.clear_calls[1]["engine_type"] == "aicoding"
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
        assert svc.clear_calls[1]["engine_type"] == "claude_code"

    svc = FakeResourceFileService()
    m = _materialiser(svc, _StubDeclaredSourceResolver(archive))
    ctx = make_context(engine_type="openclaw")
    _write_through(
        m, ctx, [{"path": "data/wrap/", "unpack": "tar.gz", "source": _src("t")}]
    )
    assert svc.clear_calls[1]["engine_type"] == "openclaw"


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
    assert svc.clear_calls[1]["engine_type"] == "openclaw"


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
