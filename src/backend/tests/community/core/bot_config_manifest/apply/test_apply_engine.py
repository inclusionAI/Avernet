"""What the apply engine promises, and what it must never do.

Most of these assert the **absence** of a write. Convergence is "re-applying an
unchanged document performs no write"; all-or-nothing is "a category that could
not be materialised wrote nothing". Equal-looking output would prove neither, so
the fakes count their calls and the tests read those counts.
"""
from __future__ import annotations

from datetime import datetime

import pytest
import yaml

from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.apply.order import steps_for
from agentclaw.community.core.bot_config_manifest.apply.orchestrator import (
    ApplyOrchestrator,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyStatus,
    EntryOutcome,
    SourceResolution,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    build_materialisers,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
)

from ._fakes import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeGitClient,
    FakeGuardedFetcher,
    FakeIdentityService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeResourceFileService,
    FakeSkillUploadService,
    FakeStartupScriptService,
    fetched_object,
    make_context,
    real_validator,
)


def _engine(scripts=None, activations=None, auth=None):
    """The W4-shaped engine: mcp + script over their fakes (these tests are
    the engine's contract, not the two fetch-consuming categories' — those
    have their own materialiser files)."""
    return ApplyOrchestrator(
        build_materialisers(
            script_service=scripts or FakeStartupScriptService(),
            activation_service=activations or FakeActivationService(),
            mcp_auth_service=auth or FakeMcpAuth(),
            identity_service=FakeIdentityService(),
            upload_service=FakeSkillUploadService(),
            capability_reader=FakeCapabilityReader(),
            package_validator=real_validator(),
            entry_fetcher=_dummy_entry_fetcher(),
            resource_service=FakeResourceFileService(),
            cli_tool_service=object(),
        ),
        steps=steps_for
    )


def _dummy_entry_fetcher():
    """The engine tests never declare skills/identity sources, so the fetcher
    the registry holds for them can be a never-called placeholder."""
    return EntryFetcher(
        FakeGuardedFetcher(), FakeManifestContent(), FakeCredentials()
    )


async def _apply(engine, document, *, ctx=None, dry_run=False, phases=None):
    return await engine.apply(
        ctx or make_context(),
        yaml.safe_load(document),
        apply_id="a1",
        trigger="explicit",
        started_at=datetime.now(),
        dry_run=dry_run,
        phases=phases,
    )


def _outcomes(report):
    return {entry.identity: entry.outcome for entry in report.entries}


_MCP_AND_SCRIPT = """schema_version: 1
manifest:
  mcp:
    - server_code: gh
script:
  body: "echo hello"
"""


# ── convergence ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reapplying_an_unchanged_document_writes_nothing():
    """The criterion the whole engine is judged on.

    Convergence is observable as the **absence of writes**, not as an
    equal-looking result — so this asserts the services were not called a second
    time, which an implementation that rewrote identical values would fail.
    """
    scripts, activations = FakeStartupScriptService(), FakeActivationService()
    engine = _engine(scripts, activations)

    first = await _apply(engine, _MCP_AND_SCRIPT)
    assert _outcomes(first) == {
        "script": EntryOutcome.CREATED,
        "gh": EntryOutcome.CREATED,
    }
    writes_after_first = (scripts.writes, activations.writes)

    second = await _apply(engine, _MCP_AND_SCRIPT)
    assert _outcomes(second) == {
        "script": EntryOutcome.UNCHANGED,
        "gh": EntryOutcome.UNCHANGED,
    }
    assert (scripts.writes, activations.writes) == writes_after_first
    assert second.status is ApplyStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_convergence_compares_the_substituted_body_not_the_document():
    """A document using a placeholder must still converge.

    Comparing the raw document text would report ``updated`` on every apply of
    any document containing ``${BOT_*}``, because the stored text and the
    written text differ by construction.
    """
    scripts = FakeStartupScriptService()
    engine = _engine(scripts)
    document = 'schema_version: 1\nscript:\n  body: "echo ${BOT_ENV}"\n'

    await _apply(engine, document)
    assert scripts.body == "echo dev", "the placeholder should have been resolved"

    writes = scripts.writes
    again = await _apply(engine, document)
    assert _outcomes(again) == {"script": EntryOutcome.UNCHANGED}
    assert scripts.writes == writes


# ── all-or-nothing (§3.2) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_entry_leaves_the_rest_of_its_category_untouched():
    """The test that matters: a transient failure must never delete a working
    entity.

    Declaring ``{ok, nope}`` where ``nope`` is not permitted must write
    **nothing** — not ``ok``, and not the removal of what was already there.
    Under overwrite a partial set is destructive: writing ``{ok}`` when the
    declaration was ``{ok, nope}`` would delete everything else.
    """
    activations = FakeActivationService(installed={"already-here"})
    engine = _engine(activations=activations, auth=FakeMcpAuth(denied={"nope"}))

    report = await _apply(
        engine,
        """schema_version: 1
manifest:
  mcp:
    - server_code: ok
    - server_code: nope
""",
    )

    assert _outcomes(report) == {
        "ok": EntryOutcome.SKIPPED,
        "nope": EntryOutcome.FAILED,
    }
    assert activations.writes == 0
    assert activations.installed == {"already-here"}
    assert report.categories[0].aborted is True
    assert report.categories[0].removals == ()
    assert report.status is ApplyStatus.FAILED


@pytest.mark.asyncio
async def test_one_categorys_failure_does_not_touch_another():
    """Categories are independent; only the failing one is held back."""
    scripts = FakeStartupScriptService()
    engine = _engine(scripts, auth=FakeMcpAuth(denied={"nope"}))

    report = await _apply(
        engine,
        """schema_version: 1
manifest:
  mcp:
    - server_code: nope
script:
  body: "echo hi"
""",
    )

    assert _outcomes(report)["script"] is EntryOutcome.CREATED
    assert _outcomes(report)["nope"] is EntryOutcome.FAILED
    assert scripts.body == "echo hi"
    assert report.status is ApplyStatus.PARTIAL


@pytest.mark.asyncio
async def test_a_permission_outage_reads_as_denied_not_as_permitted():
    """The catalogue endpoint is advisory and fail-open during an outage.

    A desired-state write must not act on that: an empty ``access_level`` is the
    documented outage sentinel, so apply reads it as "no" exactly as
    ``DirectActivationService`` does.
    """
    activations = FakeActivationService()
    engine = _engine(activations=activations, auth=FakeMcpAuth(outage={"gh"}))

    report = await _apply(
        engine, "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n"
    )

    assert _outcomes(report) == {"gh": EntryOutcome.FAILED}
    assert activations.writes == 0


# ── overwrite: [] empties, absence does not (§3.2) ──────────────────────────


@pytest.mark.asyncio
async def test_an_empty_category_empties_its_area_and_absence_does_not():
    """Two behaviours that look opposite and are one rule, pinned together.

    ``mcp: []`` is a declaration that the set is empty, so the area is emptied.
    A document that does not mention ``mcp`` declares nothing about it, so it is
    untouched — which is also why deleting a manifest deletes nothing: a bot
    with no document declares no category at all.
    """
    # Declared empty ⇒ emptied.
    emptying = FakeActivationService(installed={"gh", "other"})
    report = await _apply(
        _engine(activations=emptying), "schema_version: 1\nmanifest:\n  mcp: []\n"
    )
    assert emptying.installed == set()
    assert sorted(report.categories[0].removals) == ["gh", "other"]
    assert report.entries == ()

    # Not declared ⇒ untouched, and not even reported.
    untouched = FakeActivationService(installed={"gh", "other"})
    report = await _apply(
        _engine(activations=untouched),
        'schema_version: 1\nscript:\n  body: "echo hi"\n',
    )
    assert untouched.installed == {"gh", "other"}
    assert untouched.writes == 0
    assert [c.construct for c in report.categories] == [ManifestSection.SCRIPT]


@pytest.mark.asyncio
async def test_deleting_the_manifest_deletes_nothing():
    """A cleared manifest is an empty document, and empties nothing.

    This is the same rule as the test above, reached from the other side: an
    absent document declares no category, so no area is overwritten.
    """
    activations = FakeActivationService(installed={"gh"})
    scripts = FakeStartupScriptService(body="echo hi")

    report = await _apply(_engine(scripts, activations), "schema_version: 1\n")

    assert activations.installed == {"gh"}
    assert scripts.body == "echo hi"
    assert (scripts.writes, activations.writes) == (0, 0)
    assert report.categories == ()
    assert report.status is ApplyStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_a_declared_category_removes_what_it_does_not_declare():
    """Overwrite, including the half that deletes."""
    activations = FakeActivationService(installed={"keep", "drop"})
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: keep\n",
    )

    assert activations.installed == {"keep"}
    assert activations.deactivated == ["drop"]
    assert report.categories[0].removals == ("drop",)
    assert _outcomes(report) == {"keep": EntryOutcome.UNCHANGED}


# ── per-category area ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_applying_one_category_leaves_the_others_alone():
    """The area is scoped per category, never globally."""
    scripts = FakeStartupScriptService(body="do not touch me")
    activations = FakeActivationService()
    engine = _engine(scripts, activations)

    await _apply(
        engine, "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n"
    )

    assert scripts.body == "do not touch me"
    assert scripts.writes == 0


# ── no materialiser yet ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_category_with_no_materialiser_fails_and_writes_nothing():
    """A category nothing can apply is an expected state, not a crash.

    Every entry fails with a readable reason, the category is aborted so
    nothing is destroyed, and the categories that *do* have materialisers still
    apply.

    Declared with ``engine_config``, the sparse construct after W9: the test
    was written against ``skills`` in W4's era and has moved forward with each
    wave that materialises its previous subject — W5 took skills, W6 resources,
    W9 cli_tools. X2/T3 will move it forward again.
    """
    scripts = FakeStartupScriptService()
    report = await _apply(
        _engine(scripts),
        """schema_version: 1
manifest:
  engine_config:
    - name: jq
      source: https://cdn.example.com/jq
      digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
script:
  body: "echo hi"
""",
    )

    assert _outcomes(report)["jq"] is EntryOutcome.FAILED
    assert _outcomes(report)["script"] is EntryOutcome.CREATED
    assert report.status is ApplyStatus.PARTIAL

    engine_config = next(
        c for c in report.categories if c.construct is ManifestCategory.ENGINE_CONFIG
    )
    assert engine_config.aborted is True
    assert engine_config.removals == ()
    reason = engine_config.entries[0].reason or ""
    assert "materializer" in reason


# ── reserved identity files ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_has_no_path_that_writes_a_reserved_identity_file():
    """``MEMORY.md`` / ``IDENTITY.md`` are unreachable from apply.

    Refused at ``PUT`` by the schema, and asserted here as well: the guarantee
    should not rest on one layer. There is no identity materialiser in this
    wave, so a declaration cannot be written at all — which this pins, so that
    W5 adding one has to keep it true.
    """
    engine = _engine()
    report = await _apply(
        engine,
        """schema_version: 1
manifest:
  identity:
    - type: MEMORY.md
      content: "should never be written"
""",
    )

    identity = next(
        c for c in report.categories if c.construct is ManifestCategory.IDENTITY
    )
    assert identity.aborted is True
    assert all(e.outcome is EntryOutcome.FAILED for e in identity.entries)


# ── dry run ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_returns_the_plan_and_writes_nothing():
    """A preview that cannot write, because the write call is not made."""
    scripts, activations = FakeStartupScriptService(), FakeActivationService(
        installed={"drop"}
    )
    engine = _engine(scripts, activations)

    report = await _apply(engine, _MCP_AND_SCRIPT, dry_run=True)

    assert _outcomes(report) == {
        "script": EntryOutcome.CREATED,
        "gh": EntryOutcome.CREATED,
    }
    # The plan predicts the removal without performing it.
    mcp = next(c for c in report.categories if c.construct is ManifestCategory.MCP)
    assert mcp.removals == ("drop",)
    assert (scripts.writes, activations.writes) == (0, 0)
    assert activations.installed == {"drop"}


# ── the two phases (W13's call pattern, before W13) ─────────────────────────


@pytest.mark.asyncio
async def test_phase_a_applies_only_the_script():
    """What W13 calls before ``_build_create_bot_payload`` composes the command."""
    scripts, activations = FakeStartupScriptService(), FakeActivationService()
    report = await _apply(
        _engine(scripts, activations),
        _MCP_AND_SCRIPT,
        phases=frozenset({ApplyPhase.PRE_CONTAINER}),
    )

    assert _outcomes(report) == {"script": EntryOutcome.CREATED}
    assert activations.writes == 0
    assert [c.construct for c in report.categories] == [ManifestSection.SCRIPT]


@pytest.mark.asyncio
async def test_phase_b_applies_everything_but_the_script():
    """What W13 calls once the container is up."""
    scripts, activations = FakeStartupScriptService(), FakeActivationService()
    report = await _apply(
        _engine(scripts, activations),
        _MCP_AND_SCRIPT,
        phases=frozenset({ApplyPhase.ON_CONTAINER}),
    )

    assert _outcomes(report) == {"gh": EntryOutcome.CREATED}
    assert scripts.writes == 0


@pytest.mark.asyncio
async def test_phase_a_reaches_no_device_and_needs_no_container():
    """The property that makes phase A callable before provisioning.

    Pinned rather than assumed: the script materialiser must reach only the
    startup-script service. If a later change gave it a device dependency, the
    creation path would break in a way no other test would catch — the same
    discipline W10 used for its uncalled ``from_spec``.
    """
    scripts = FakeStartupScriptService()

    # An activation service that raises if touched stands in for "no container".
    class ExplodingActivation:
        def list_installed_mcps(self, **_):
            raise AssertionError("phase A must not reach the device/container path")

        async def activate_mcp(self, **_):
            raise AssertionError("phase A must not reach the device/container path")

        async def deactivate_mcp(self, **_):
            raise AssertionError("phase A must not reach the device/container path")

    report = await _apply(
        _engine(scripts, ExplodingActivation()),
        _MCP_AND_SCRIPT,
        phases=frozenset({ApplyPhase.PRE_CONTAINER}),
    )
    assert _outcomes(report) == {"script": EntryOutcome.CREATED}


@pytest.mark.asyncio
async def test_both_phases_together_preserve_the_declared_order():
    """On a running bot the split is invisible, and the order still holds.

    ``script`` first — which reverses design §3.4, per work-items §2.12.
    """
    report = await _apply(_engine(), _MCP_AND_SCRIPT)
    assert [c.construct for c in report.categories] == [
        ManifestSection.SCRIPT,
        ManifestCategory.MCP,
    ]


# ── the record never over-claims ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_raising_materialiser_is_reported_as_written_nothing():
    """A write that raised may have partially landed.

    It is reported as a failure of every entry rather than claiming any of them:
    the record must never say something was materialised when it may not have
    been.
    """

    class ExplodingActivation(FakeActivationService):
        async def activate_mcp(self, **kwargs):
            raise RuntimeError("device unreachable")

    activations = ExplodingActivation()
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n",
    )

    assert _outcomes(report) == {"gh": EntryOutcome.FAILED}
    assert report.categories[0].aborted is True
    assert report.status is ApplyStatus.FAILED


@pytest.mark.asyncio
async def test_a_bot_with_no_manifest_applies_nothing_without_erroring():
    """Not an error — the rule that makes an absent manifest an empty document."""
    report = await _apply(_engine(), "schema_version: 1\n")
    assert report.entries == ()
    assert report.status is ApplyStatus.SUCCEEDED


# ── the report carries no secret ────────────────────────────────────────────


def test_the_report_payload_names_every_field_it_emits():
    """``as_payload`` copies no dict through, so nothing can ride along.

    A credential cannot reach a response body inside a structure nobody
    inspected, because no structure is passed through — every field is named.
    """
    from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
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
        "sources",
        "notes",
        "categories",
        "entries",
    }


# ── platform-owned MCP codes ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_platform_default_is_refused_before_anything_is_activated():
    """The review finding, as its regression test.

    ``activate_mcp`` refuses a code the bot's engine/template policy owns, from
    a guard that runs *before* the permission check. Only asking about
    permission meant a declaration of ``{ordinary, platform-default}`` passed
    resolve, activated the ordinary one for real, and then raised — leaving the
    category half-written and reported aborted.

    The assertion that matters is ``writes == 0``: not "the apply failed", which
    it did before the fix too, but that it failed *having written nothing*.
    """
    activations = FakeActivationService(platform_defaults={"platform-owned"})
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n"
        "    - server_code: ordinary\n"
        "    - server_code: platform-owned\n",
    )

    assert activations.writes == 0, (
        "the category was half-written: the ordinary server was activated for "
        "real before the platform default was refused"
    )
    assert activations.installed == set()
    assert report.categories[0].aborted is True
    reasons = {entry.identity: entry.reason for entry in report.categories[0].entries}
    assert "platform default" in (reasons["platform-owned"] or "")


@pytest.mark.asyncio
async def test_the_ordinary_server_still_applies_when_no_default_is_declared():
    """The counterpart, so the guard cannot pass by refusing everything."""
    activations = FakeActivationService(platform_defaults={"platform-owned"})
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: ordinary\n",
    )

    assert activations.activated == ["ordinary"]
    assert report.categories[0].aborted is False


@pytest.mark.asyncio
async def test_a_platform_default_is_never_removed_by_omission():
    """The removal half of the same guard.

    Overwrite reads an absent entry as "remove it", but a platform default is
    one the manifest may not declare in the first place, so its absence cannot
    be a request. Reachable because ``server_codes_for`` is keyed off
    ``active_engine``/``template_type``: an ordinary installed server becomes a
    default the moment a bot's engine changes. Left in the removal set, every
    later apply would call ``deactivate_mcp`` on it and abort the category for
    something no author could fix from the document.
    """
    activations = FakeActivationService(
        installed={"keep", "became-a-default"},
        platform_defaults={"became-a-default"},
    )
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: keep\n",
    )

    assert report.categories[0].aborted is False
    assert activations.deactivated == []
    assert report.categories[0].removals == ()
    assert "became-a-default" in activations.installed


@pytest.mark.asyncio
async def test_an_unanswerable_platform_default_lookup_writes_nothing():
    """Fail-closed, in the direction that keeps the manifest's reach narrow.

    Returning an empty set when the policy cannot be reached would restore the
    original bug exactly — every code would look un-owned and the write would
    discover the truth mid-loop.
    """

    class Unreachable(FakeActivationService):
        def platform_default_mcp_codes(self, **_kwargs):
            raise RuntimeError("policy lookup unavailable")

    activations = Unreachable()
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: ordinary\n",
    )

    assert activations.writes == 0
    assert report.categories[0].aborted is True


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
    activations = FakeActivationService(
        installed={"stuck"}, platform_defaults=set()
    )

    async def _explode(**_kwargs):
        raise RuntimeError("the activation service is down")

    activations.deactivate_mcp = _explode

    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp: []\n",
    )

    category = report.categories[0]
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

    activations.deactivate_mcp = _explode

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

    activations.activate_mcp = _explode

    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n",
    )

    category = report.categories[0]
    assert category.aborted is True
    assert category.partially_written is True
    assert category.as_dict()["partially_written"] is True


@pytest.mark.asyncio
async def test_an_abort_before_the_write_says_nothing_changed():
    """The counterpart, so the flag means something rather than being always on."""
    report = await _apply(
        _engine(auth=FakeMcpAuth(denied={"nope"})),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: nope\n",
    )

    category = report.categories[0]
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
        SOUL_BODY as _SOUL_BODY,
        SOUL_URL as _SOUL_URL,
        build_skill_zip,
    )

    qc_zip = build_skill_zip("quality-check")
    qc_url = "https://content.example/skills/quality-check.zip"
    rules_url = "https://content.example/identity/rules.md"
    import hashlib

    qc_digest = "sha256:" + hashlib.sha256(qc_zip).hexdigest()
    identity = FakeIdentityService()
    uploads = FakeSkillUploadService()
    activation = FakeActivationService()
    reader = FakeCapabilityReader()
    fetcher = FakeGuardedFetcher(
        responses={
            _SOUL_URL: fetched_object(_SOUL_BODY, url=_SOUL_URL),
            qc_url: fetched_object(qc_zip, url=qc_url, content_type="application/zip"),
            # identity rules fetch: the source is gone — a real outage shape.
        },
        failures={rules_url: FetchFailedError("source answered 404")},
    )
    engine = ApplyOrchestrator(
        build_materialisers(
            script_service=FakeStartupScriptService(),
            activation_service=activation,
            mcp_auth_service=FakeMcpAuth(),
            identity_service=identity,
            upload_service=uploads,
            capability_reader=reader,
            package_validator=real_validator(),
            entry_fetcher=EntryFetcher(
                fetcher, FakeManifestContent(), FakeCredentials()
            ),
            resource_service=FakeResourceFileService(),
            cli_tool_service=object(),
        ),
        steps=steps_for
    )

    report = await _apply(
        engine,
        f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source: "{_SOUL_URL}"
    - type: RULES.md
      source: "{rules_url}"
  skills:
    - name: quality-check
      source: "{qc_url}"
      digest: "{qc_digest}"
script:
  body: "echo hi"
""",
        ctx=make_context(engine_type="openclaw"),
    )

    # identity aborted on the failed RULES fetch — nothing written there;
    # RULES itself failed, its neighbour was skipped, and SOUL.md not written.
    by_construct = {c.construct: c for c in report.categories}
    identity_category = by_construct[ManifestCategory.IDENTITY]
    assert identity_category.aborted is True
    outcomes = _outcomes(report)
    assert outcomes["RULES.md"] is EntryOutcome.FAILED
    assert outcomes["SOUL.md"] is EntryOutcome.SKIPPED
    assert identity.writes == []

    # skills delivered, after identity per APPLY_ORDER position (1 < 3).
    assert [c.construct for c in report.categories] == [
        ManifestSection.SCRIPT,
        ManifestCategory.IDENTITY,
        ManifestCategory.SKILLS,
    ]
    assert outcomes["quality-check"] is EntryOutcome.CREATED
    assert uploads.uploads[0]["name"] == "quality-check"

    # script is a plain row write, unaffected by anything upstream.
    assert outcomes["script"] is EntryOutcome.CREATED
    assert report.status is ApplyStatus.PARTIAL


# ── W7: the report's sources section ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_sessions_resolutions_ride_into_the_report():
    """W7 wiring: the report's ``sources`` is the session's records and
    nothing else.

    The orchestrator holds no per-apply state, so the resolutions cannot
    live on it — they ride the context's session and are read out at report
    time. A session with no resolutions and no session at all both stay
    empty, which is what every pre-W7 document expects.
    """
    engine = _engine()
    resolution = SourceResolution(
        name="charts", ref="main", resolved_sha="f" * 40, auth="ci-token"
    )
    # The test-visible seam for a checkout that a materialiser's resolve
    # would have recorded: the session's own record list, appended directly.
    session = SourceSession(sources={}, baselines={}, git=FakeGitClient())
    session._resolutions.append(resolution)
    empty_session = SourceSession(sources={}, baselines={}, git=FakeGitClient())

    report = await _apply(
        engine,
        'script:\n  body: "echo hi"\n',
        ctx=make_context(source_session=session),
    )
    assert report.as_payload()["sources"] == [
        {
            "name": "charts",
            "ref": "main",
            "resolved_sha": "f" * 40,
            "auth": "ci-token",
        }
    ]

    # No resolutions recorded — the section is empty, not absent.
    plain = await _apply(
        engine,
        'script:\n  body: "echo hi"\n',
        ctx=make_context(source_session=empty_session),
    )
    assert plain.as_payload()["sources"] == []

    # No session at all (pre-W7 callers, hand-driven tests) — same answer.
    bare = await _apply(engine, 'script:\n  body: "echo hi"\n')
    assert bare.as_payload()["sources"] == []
