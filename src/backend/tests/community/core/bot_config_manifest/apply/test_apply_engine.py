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
from agentclaw.community.core.bot_config_manifest.apply.orchestrator import (
    ApplyOrchestrator,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyStatus,
    EntryOutcome,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    build_materialisers,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)

from ._fakes import (
    FakeActivationService,
    FakeMcpAuth,
    FakeStartupScriptService,
    make_context,
)


def _engine(scripts=None, activations=None, auth=None):
    return ApplyOrchestrator(
        build_materialisers(
            script_service=scripts or FakeStartupScriptService(),
            activation_service=activations or FakeActivationService(),
            mcp_auth_service=auth or FakeMcpAuth(),
        )
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
    """W5/W6's categories are an expected state, not a crash.

    Every entry fails with a readable reason, the category is aborted so nothing
    is destroyed, and the categories that *do* have materialisers still apply.
    """
    scripts = FakeStartupScriptService()
    report = await _apply(
        _engine(scripts),
        """schema_version: 1
manifest:
  skills:
    - name: quality-check
      content: "x"
script:
  body: "echo hi"
""",
    )

    assert _outcomes(report)["quality-check"] is EntryOutcome.FAILED
    assert _outcomes(report)["script"] is EntryOutcome.CREATED
    assert report.status is ApplyStatus.PARTIAL

    skills = next(
        c for c in report.categories if c.construct is ManifestCategory.SKILLS
    )
    assert skills.aborted is True
    assert skills.removals == ()
    reason = skills.entries[0].reason or ""
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
        "categories",
        "entries",
    }
