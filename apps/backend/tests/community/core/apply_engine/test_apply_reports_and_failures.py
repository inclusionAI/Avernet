"""What the apply engine promises, and what it must never do.

Most of these assert the **absence** of a write. Convergence is "re-applying an
unchanged document performs no write"; all-or-nothing is "a category that could
not be materialised wrote nothing". Equal-looking output would prove neither, so
the fakes count their calls and the tests read those counts.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredScript,
    DesiredState,
)
from agentclaw.community.core.apply_engine.outcomes import (
    ApplyStatus,
    EntryOutcome,
    SourceResolution,
)
from agentclaw.community.core.apply_engine.capabilities import (
    Category,
    Section,
)
from agentclaw.community.core.skill_center.errors import (
    ApplyDesiredStateCommittedError,
)

from ._fakes import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeIdentityService,
    FakeMcpAuth,
    FakeSkillUploadService,
    make_context,
)


from .test_apply_engine import (
    _engine,
    _apply,
    _apply_legacy_mcp,
    _category,
    _outcomes,
)


def test_the_report_payload_names_every_field_it_emits():
    """``as_payload`` copies no dict through, so nothing can ride along.

    A credential cannot reach a response body inside a structure nobody
    inspected, because no structure is passed through — every field is named.
    """
    from agentclaw.community.core.apply_engine.outcomes import (
        ApplyReport,
    )

    payload = ApplyReport(
        apply_id="a",
        bot_id="b",
        trigger="explicit",
        status=ApplyStatus.SUCCEEDED,
        started_at=datetime.now(),
    ).as_payload()

    assert set(payload) == {
        "apply_id",
        "bot_id",
        "trigger",
        "result",
        "started_at",
        "finished_at",
        "state",
        "sources",
        "notes",
        "categories",
        "entries",
    }


def test_a_stored_source_mode_outside_the_enum_reads_as_no_opinion_and_the_rest_decodes():
    """The one lenient field of the codec, pinned.

    The Manifest compiler decodes the last reports on every compile for its
    baselines; one historical row with an unreadable ``mode`` must cost that
    row its baseline opinion (``None``), not stop the bot from ever applying
    again. A later tidy back to ``SourceMode(...)`` would undo exactly this.
    """
    from types import SimpleNamespace

    from agentclaw.community.core.apply_engine.outcomes import SourceMode
    from agentclaw.community.core.apply_engine.run.report_codec import (
        report_from_payload,
    )

    started = datetime.now()
    record = SimpleNamespace(
        apply_id="a",
        bot_id="b",
        trigger="explicit",
        started_at=started,
        finished_at=started,
    )
    payload = {
        "state": {"kind": "manifest", "reference": None},
        "sources": [
            {
                "name": "content",
                "url": "https://git.example/c.git",
                "ref": "v1",
                "mode": "bogus",
                "resolved_sha": "1" * 40,
            },
            {
                "name": "docs",
                "url": "https://git.example/d.git",
                "ref": "main",
                "mode": "strict",
                "resolved_sha": "2" * 40,
            },
        ],
        "entries": [
            {"category": "capabilities", "name": "mcp/gh", "action": "created"}
        ],
        "categories": [
            {
                "category": "capabilities",
                "aborted": False,
                "partially_written": False,
                "removed": [],
            }
        ],
    }
    report = report_from_payload(payload, record=record, status=ApplyStatus.SUCCEEDED)
    assert [source.mode for source in report.sources] == [None, SourceMode.STRICT]
    assert [(entry.identity, entry.outcome.value) for entry in report.entries] == [
        ("mcp/gh", "created")
    ]


# ── platform-owned MCP codes ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_declared_platform_default_refuses_the_whole_apply():
    """A platform default MCP server is supplied, not the declaration's: declaring one refuses the apply, and
    the ordinary server beside it is not written either."""
    activations = FakeActivationService(platform_defaults={"platform-owned"})
    report = await _apply_legacy_mcp(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n"
        "    - server_code: ordinary\n"
        "    - server_code: platform-owned\n",
    )

    assert report.status is ApplyStatus.FAILED
    assert _outcomes(report) == {
        "ordinary": EntryOutcome.SKIPPED,
        "platform-owned": EntryOutcome.FAILED,
    }
    assert activations.writes == 0


@pytest.mark.asyncio
async def test_the_ordinary_server_still_applies_when_no_default_is_declared():
    """The counterpart, so the guard cannot pass by refusing everything."""
    activations = FakeActivationService(platform_defaults={"platform-owned"})
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: ordinary\n",
    )

    assert activations.installed == {"ordinary"}
    assert activations.configured == []
    assert _category(report, Category.CAPABILITIES).aborted is False


@pytest.mark.asyncio
async def test_an_omitted_platform_default_is_left_alone():
    """A captured revision never names the platform's default servers; applying it must not remove them."""
    activations = FakeActivationService(
        installed={"keep", "a-default"},
        platform_defaults={"a-default"},
    )
    report = await _apply_legacy_mcp(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: keep\n",
    )

    assert report.status is ApplyStatus.SUCCEEDED
    assert (
        activations.deactivated == []
        and _category(report, Category.CAPABILITIES).removals == ()
    )
    assert "a-default" in activations.installed


@pytest.mark.asyncio
async def test_an_unanswerable_set_supply_lookup_refuses_the_apply_and_writes_nothing():
    """Fail-closed: a check that cannot say what the skill sets supply cannot let a claim through."""

    class UnreachableReader(FakeCapabilityReader):
        def active_skill_assets(self, **_kwargs):
            raise RuntimeError("policy lookup unavailable")

    activations = FakeActivationService()
    report = await _apply(
        _engine(activations=activations, reader=UnreachableReader()),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: ordinary\n",
    )

    assert activations.writes == 0
    assert (
        report.status is ApplyStatus.FAILED
        and _category(report, Category.CAPABILITIES).aborted is True
    )


# ── an aborted category must never read as success ──────────────────────────


@pytest.mark.asyncio
async def test_a_declared_empty_category_that_fails_is_not_reported_successful():
    """Review finding: an aborted category with no entries read as SUCCEEDED.

    ``derive_status`` worked entirely from entry outcomes, and a *declared-empty*
    category produces none — there is nothing declared to report on. So
    ``mcp: []`` whose removal raised aborted the category and still summarised
    the apply as ``SUCCEEDED``, telling a poller its bot had converged on an
    empty set that was in fact untouched.
    """
    activations = FakeActivationService(installed={"stuck"}, platform_defaults=set())

    async def _explode(**_kwargs):
        raise RuntimeError("the activation service is down")

    activations.remove_apply_mcp = _explode

    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp: []\n",
    )

    category = _category(report, Category.CAPABILITIES)
    assert category.aborted is True
    assert category.entries == (), "a declared-empty category declares no entries"
    assert report.status is ApplyStatus.FAILED, (
        "an aborted category with no entries left the summary at SUCCEEDED"
    )


@pytest.mark.asyncio
async def test_one_aborted_empty_category_downgrades_an_otherwise_good_apply():
    """The mixed case, which a "no entries at all" special case would miss.

    A successful `script` alongside a failed `mcp: []` gives a non-empty entry
    list, so the summary has to count the silent failure directly rather than
    fall back to "no entries means nothing was asked".
    """
    activations = FakeActivationService(installed={"stuck"})

    async def _explode(**_kwargs):
        raise RuntimeError("the activation service is down")

    activations.remove_apply_mcp = _explode

    report = await _apply(
        _engine(activations=activations),
        'schema_version: 1\nscript:\n  body: "echo hi"\nmanifest:\n  mcp: []\n',
    )

    assert report.status is ApplyStatus.PARTIAL


@pytest.mark.asyncio
async def test_an_abort_during_the_write_says_the_area_may_have_changed():
    """``aborted`` alone overclaimed; the write case needs its own signal.

    Aborting from ``resolve`` leaves the area untouched, and the API documents
    that. Aborting from ``write`` does not, and the two are indistinguishable
    from entry outcomes — so a caller told only "aborted" would read "left
    exactly as it was" and never re-apply.
    """
    activations = FakeActivationService()

    async def _explode(**_kwargs):
        raise RuntimeError("the activation service is down")

    activations.claim_apply_mcp = _explode

    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n",
    )

    category = _category(report, Category.CAPABILITIES)
    assert category.aborted is True
    assert category.partially_written is True
    assert category.as_dict()["partially_written"] is True
    assert report.status is ApplyStatus.FAILED


@pytest.mark.asyncio
async def test_a_later_mcp_failure_after_a_committed_claim_reports_partial():
    activations = FakeActivationService()
    original_claim = activations.claim_apply_mcp

    async def _fail_second(**kwargs):
        if kwargs["server_code"] == "second":
            raise RuntimeError("second MCP failed")
        return await original_claim(**kwargs)

    activations.claim_apply_mcp = _fail_second
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n"
        "    - server_code: first\n"
        "    - server_code: second\n",
    )

    assert report.status is ApplyStatus.PARTIAL
    assert activations.installed == {"first"}
    assert _category(report, Category.CAPABILITIES).partially_written is True


@pytest.mark.asyncio
async def test_first_mcp_post_commit_failure_reports_partial():
    activations = FakeActivationService()

    async def _commit_then_fail(**kwargs):
        activations.installed.add(kwargs["server_code"])
        raise ApplyDesiredStateCommittedError()

    activations.claim_apply_mcp = _commit_then_fail
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: committed\n",
    )

    assert report.status is ApplyStatus.PARTIAL
    assert activations.installed == {"committed"}


@pytest.mark.asyncio
async def test_failed_skill_write_stops_later_mcp_and_preserves_configuration():
    from ._fakes import (
        FakeObjectCredentials,
        OSS_AUTH,
        OSS_BUCKET,
        build_skill_zip,
        seeded_object_store,
        skill_asset,
    )

    key = "skills/new-skill.zip"
    objects = seeded_object_store({key: build_skill_zip("new-skill")})
    reader = FakeCapabilityReader(
        assets=[
            skill_asset(
                7,
                "old-skill",
                mcp_dependencies=({"code": "mcp.actual"},),
            )
        ]
    )
    activations = FakeActivationService(installed={"mcp.actual"})
    activations.mcp_overrides["mcp.actual"] = {"headers": {"X-Stale": "value"}}

    async def _fail_claim(**_kwargs):
        raise RuntimeError("Direct claim failed after package upload")

    activations.claim_apply_skill = _fail_claim
    report = await _apply(
        _engine(
            activations=activations,
            reader=reader,
            objects=objects,
            credentials=FakeObjectCredentials(),
        ),
        "schema_version: 1\nmanifest:\n"
        "  skills:\n"
        "    - name: new-skill\n"
        "      source:\n"
        "        protocol: oss\n"
        f"        bucket: {OSS_BUCKET}\n"
        f"        key: {key}\n"
        f"        auth: {OSS_AUTH}\n"
        "  mcp: []\n",
    )

    by_construct = {category.construct: category for category in report.categories}
    skills = by_construct[Category.CAPABILITIES]
    mcp = by_construct[Category.CAPABILITIES]
    assert skills.aborted is True
    assert skills.partially_written is True
    assert mcp.removals == ()
    assert activations.configured == [] and activations.deactivated == []
    assert activations.mcp_overrides["mcp.actual"] == {"headers": {"X-Stale": "value"}}
    assert report.status is ApplyStatus.PARTIAL


@pytest.mark.asyncio
async def test_failed_local_asset_cleanup_reports_partial_and_no_removal():
    uploads = FakeSkillUploadService()
    reader = FakeCapabilityReader(
        assets=[
            SimpleNamespace(
                skill_id=71,
                name="stale-local",
                git_path="local://stale-local",
                mcp_dependencies=(),
            )
        ]
    )

    async def _fail_delete(**_kwargs):
        raise RuntimeError("storage unavailable")

    uploads.delete_local_skill = _fail_delete
    report = await _apply(
        _engine(uploads=uploads, reader=reader),
        "schema_version: 1\nmanifest:\n  skills: []\n",
    )

    category = _category(report, Category.CAPABILITIES)
    assert report.status is ApplyStatus.PARTIAL
    assert category.partially_written is True
    assert category.removals == ()


@pytest.mark.asyncio
async def test_an_abort_before_the_write_says_nothing_changed():
    """The counterpart, so the flag means something rather than being always on."""
    report = await _apply(
        _engine(auth=FakeMcpAuth(denied={"nope"})),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: nope\n",
    )

    category = _category(report, Category.CAPABILITIES)
    assert category.aborted is True
    assert category.partially_written is False


@pytest.mark.asyncio
async def test_a_fetching_document_applies_all_four_categories_in_order():
    """The W5 integration: one document declaring identity, skills, mcp and
    script walks the complete registry, categories land in ``APPLY_ORDER``
    position (identity before skills), a fetch failure in one category
    neither touches the bot nor stops the others, and the status summarises
    the partial delivery.
    """
    from ._fakes import (
        FakeObjectCredentials,
        OSS_AUTH,
        OSS_BUCKET,
        SOUL_BODY as _SOUL_BODY,
        SOUL_KEY as _SOUL_KEY,
        build_skill_zip,
        seeded_object_store,
    )

    qc_zip = build_skill_zip("quality-check")
    qc_key = "skills/quality-check.zip"
    rules_key = "identity/rules.md"
    import hashlib

    qc_digest = "sha256:" + hashlib.sha256(qc_zip).hexdigest()
    identity = FakeIdentityService()
    uploads = FakeSkillUploadService()
    activation = FakeActivationService()
    reader = FakeCapabilityReader()
    objects = seeded_object_store({_SOUL_KEY: _SOUL_BODY, qc_key: qc_zip})
    # identity rules fetch: its bucket is unreachable — a real outage shape.
    objects.make_unavailable("rules-bucket", "source answered 404")
    engine = _engine(
        activations=activation,
        mcp_config=object(),
        uploads=uploads,
        reader=reader,
        identity=identity,
        objects=objects,
        credentials=FakeObjectCredentials(),
    )

    report = await _apply(
        engine,
        f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: oss
        bucket: "{OSS_BUCKET}"
        key: "{_SOUL_KEY}"
        auth: "{OSS_AUTH}"
    - type: RULES.md
      source:
        protocol: oss
        bucket: rules-bucket
        key: "{rules_key}"
        auth: "{OSS_AUTH}"
  skills:
    - name: quality-check
      source:
        protocol: oss
        bucket: "{OSS_BUCKET}"
        key: "{qc_key}"
        auth: "{OSS_AUTH}"
      digest: "{qc_digest}"
script:
  body: "echo hi"
""",
        ctx=make_context(engine_type="openclaw"),
    )

    # identity aborted on the failed RULES fetch — nothing written there;
    # RULES itself failed, its neighbour was skipped, and SOUL.md not written.
    by_construct = {c.construct: c for c in report.categories}
    identity_category = by_construct[Category.IDENTITY]
    assert identity_category.aborted is True
    outcomes = _outcomes(report)
    assert outcomes["RULES.md"] is EntryOutcome.FAILED
    assert outcomes["SOUL.md"] is EntryOutcome.SKIPPED
    assert identity.writes == []

    # skills delivered, after identity per APPLY_ORDER position (1 < 3).
    assert [c.construct for c in report.categories] == [
        Section.SCRIPT,
        Category.IDENTITY,
        Category.RESOURCES,
        Category.CAPABILITIES,
        Category.CLI_TOOLS,
    ]
    assert outcomes["quality-check"] is EntryOutcome.CREATED
    assert uploads.uploads[0]["name"] == "quality-check"

    # script is a plain row write, unaffected by anything upstream.
    assert outcomes["script"] is EntryOutcome.CREATED
    assert report.status is ApplyStatus.PARTIAL


# ── W7: the report's sources section ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_compilers_resolutions_ride_into_the_report():
    """W7 wiring: the report's ``sources`` is what the compiler resolved and
    nothing else.

    The orchestrator holds no per-apply state, so the resolutions cannot
    live on it — the compiler reads them out of its session and hands them
    over as ``sources``, which the orchestrator carries onto the report
    verbatim. A compile that resolved nothing stays empty, not absent, which
    is what every pre-W7 document expects.
    """
    engine = _engine()
    resolution = SourceResolution(
        name="charts",
        url="https://git.corp/charts.git",
        ref="main",
        mode="strict",
        resolved_sha="f" * 40,
        auth="ci-token",
    )
    state = DesiredState(script=Declaration((DesiredScript("echo hi"),)))

    # The seam a compiler hands its records across: the orchestrator's own
    # ``sources`` argument, carried as given.
    report = await engine.orchestrator.apply(
        make_context(),
        state,
        apply_id="a1",
        trigger="explicit",
        started_at=datetime.now(),
        sources=(resolution,),
    )
    assert report.as_payload()["sources"] == [
        {
            "name": "charts",
            "url": "https://git.corp/charts.git",
            "ref": "main",
            "mode": "strict",
            "resolved_sha": "f" * 40,
            "auth": "ci-token",
        }
    ]

    # No resolutions handed over — the section is empty, not absent.
    plain = await engine.orchestrator.apply(
        make_context(),
        state,
        apply_id="a1",
        trigger="explicit",
        started_at=datetime.now(),
    )
    assert plain.as_payload()["sources"] == []

    # Through the compiler, a document naming no git source resolves nothing
    # — same answer.
    bare = await _apply(engine, 'script:\n  body: "echo hi"\n')
    assert bare.as_payload()["sources"] == []


def test_a_removal_that_landed_makes_an_otherwise_failed_apply_partial():
    """Review finding: ``script: null`` deleting the script while the only declared entry failed read as FAILED,
    so a genome promotion left the refs alone although the bot had changed."""
    from agentclaw.community.core.apply_engine.outcomes import (
        CategoryResult,
        EntryResult,
        derive_status,
    )

    cleared = CategoryResult(
        construct=Section.SCRIPT, entries=(), removals=("script",), aborted=False
    )
    failed = CategoryResult(
        construct=Category.CAPABILITIES,
        entries=(
            EntryResult(
                Category.CAPABILITIES,
                "refund-policy",
                EntryOutcome.FAILED,
                reason="not found",
            ),
        ),
        removals=(),
        aborted=True,
    )
    assert derive_status((cleared, failed)) is ApplyStatus.PARTIAL
    assert derive_status((failed,)) is ApplyStatus.FAILED, (
        "nothing landed: still FAILED"
    )
    assert derive_status((cleared,)) is ApplyStatus.SUCCEEDED
