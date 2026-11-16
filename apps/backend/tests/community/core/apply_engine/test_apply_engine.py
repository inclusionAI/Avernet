"""What the apply engine promises, and what it must never do.

Most of these assert the **absence** of a write. Convergence is "re-applying an
unchanged document performs no write"; all-or-nothing is "a category that could
not be materialised wrote nothing". Equal-looking output would prove neither, so
the fakes count their calls and the tests read those counts.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from agentclaw.community.core.apply_engine.desired_state import (
    DesiredState,
)
from agentclaw.community.core.apply_engine.order import ApplyPhase
from agentclaw.community.core.apply_engine.delivery import ArcaDelivery
from agentclaw.community.core.apply_engine.orchestrator import (
    ApplyOrchestrator,
)
from agentclaw.community.core.apply_engine.outcomes import (
    ApplyStatus,
    EntryOutcome,
)
from tests.community.core.apply_engine._capability_facts import build_test_materialisers as build_materialisers
from agentclaw.community.core.apply_engine.capabilities import (
    Category,
    Section,
)
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
    DeclaredSourceResolver,
)

from ._fakes import (
    resource_rows_for,
    EngineRig,
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeIdentityService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeObjectStore,
    FakeResourceFileService,
    FakeSkillUploadService,
    FakeStartupScriptService,
    make_context,
    manifest_compiler,
    real_validator,
)


#: The orchestrator is always handed a delivery strategy's ``steps_for``; these
#: tests exercise the ARCA family, so they hand it ARCA's. The ports callable is
#: never reached — ``steps_for`` only phases and sorts ``APPLY_ORDER``.
_arca_steps = ArcaDelivery(lambda: None).steps_for


def _engine(
    scripts=None, activations=None, auth=None, mcp_config=None,
    uploads=None, reader=None, identity=None, objects=None, credentials=None,
):
    """The engine the run service drives: a compiler over the document, the
    orchestrator over the state it compiles, one content store between them.

    mcp + script over their fakes (these tests are the engine's contract, not
    the fetch-consuming categories' — those have their own materialiser
    files). ``objects`` / ``credentials`` seed the declared-source road for the
    few tests that fetch.
    """
    if mcp_config is None:
        mcp_config = MagicMock()
        mcp_config.validate_bot_override.return_value = {
            "valid": True,
            "error": None,
        }
    content = FakeManifestContent()
    orchestrator = ApplyOrchestrator(
        build_materialisers(
            script_service=scripts or FakeStartupScriptService(),
            activation_service=activations or FakeActivationService(),
            mcp_auth_service=auth or FakeMcpAuth(),
            mcp_config_service=mcp_config,
            identity_service=identity or FakeIdentityService(),
            upload_service=uploads or FakeSkillUploadService(),
            capability_reader=reader or FakeCapabilityReader(),
            package_validator=real_validator(),
            content=content,
            resource_service=FakeResourceFileService(),
            resource_rows=resource_rows_for()[0],
            cli_tool_service=SimpleNamespace(list=lambda _ctx: [], replace_all=AsyncMock(return_value=[])),
            shared_skills=object(),
        ),
        steps=_arca_steps
    )
    resolver = DeclaredSourceResolver(
        content, credentials or FakeCredentials(), objects or FakeObjectStore()
    )
    return EngineRig(orchestrator, manifest_compiler(content=content, resolver=resolver))


async def _apply(engine, document, *, ctx=None, dry_run=False, phase=None):
    return await engine.apply(
        ctx or make_context(),
        yaml.safe_load(document),
        apply_id="a1",
        trigger="explicit",
        started_at=datetime.now(),
        dry_run=dry_run,
        phase=phase,
    )



async def _apply_legacy_mcp(engine, document):
    """The pre-existing Genome producer covers Direct MCP, with other scopes absent."""
    ctx = make_context()
    compiled = await engine.compiler.compile_document(ctx, yaml.safe_load(document))
    mcp = compiled.state.capabilities.entries[0].mcp
    return await engine.orchestrator.apply(
        ctx, DesiredState(mcp=mcp), apply_id='legacy', trigger='explicit', started_at=datetime.now(),
    )


def _category(report, construct):
    return next(category for category in report.categories if category.construct == construct)


def _outcomes(report):
    return {entry.identity.split("/", 1)[-1]: entry.outcome for entry in report.entries}


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
    assert _category(report, Category.CAPABILITIES).aborted is True
    assert _category(report, Category.CAPABILITIES).removals == ()
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


# ── overwrite ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("document", ["schema_version: 1\n", "schema_version: 1\nmanifest:\n  mcp: []\n"])
async def test_empty_and_omitted_mcp_clear_the_same_area(document):
    activations = FakeActivationService(installed={"gh", "other"})
    report = await _apply(_engine(activations=activations), document)
    assert activations.installed == set()
    assert _category(report, Category.CAPABILITIES).removals == ("mcp/gh", "mcp/other")
    assert report.entries == ()
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
    assert _category(report, Category.CAPABILITIES).removals == ("mcp/drop",)
    assert _outcomes(report) == {"keep": EntryOutcome.UNCHANGED}


@pytest.mark.asyncio
async def test_mcp_config_change_is_updated_and_reapplying_is_unchanged():
    activations = FakeActivationService(installed={"gh"})
    activations.mcp_overrides["gh"] = {"headers": {"X-Project": "old"}}
    document = """schema_version: 1
manifest:
  mcp:
    - server_code: gh
      config:
        headers:
          X-Project: new
"""
    engine = _engine(activations=activations)

    first = await _apply(engine, document)
    second = await _apply(engine, document)

    assert _outcomes(first) == {"gh": EntryOutcome.UPDATED}
    assert _outcomes(second) == {"gh": EntryOutcome.UNCHANGED}
    assert activations.configured == [("gh", {"headers": {"X-Project": "new"}})]


@pytest.mark.asyncio
async def test_bare_mcp_entry_clears_an_existing_bot_override():
    activations = FakeActivationService(installed={"gh"})
    activations.mcp_overrides["gh"] = {"url": "https://old.example/mcp"}

    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n",
    )

    assert _outcomes(report) == {"gh": EntryOutcome.UPDATED}
    assert activations.mcp_overrides == {}
    assert activations.configured == [("gh", None)]


@pytest.mark.asyncio
async def test_new_bare_mcp_entry_clears_a_saved_override_without_installation():
    activations = FakeActivationService()
    activations.mcp_overrides["gh"] = {"url": "https://orphan.example/mcp"}

    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n",
    )

    assert _outcomes(report) == {"gh": EntryOutcome.CREATED}
    assert activations.installed == {"gh"}
    assert activations.configured == [("gh", None)]
    assert activations.mcp_overrides == {}


@pytest.mark.asyncio
async def test_bare_mcp_entry_validates_inherited_config_before_any_write():
    activations = FakeActivationService()
    config_service = MagicMock()
    config_service.validate_bot_override.return_value = {
        "valid": False,
        "error": "inherited endpoint is unavailable",
    }

    report = await _apply(
        _engine(activations=activations, mcp_config=config_service),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n",
    )

    assert report.status is ApplyStatus.FAILED
    assert activations.writes == 0
    config_service.validate_bot_override.assert_called_once_with(
        user_id="u_owner",
        server_code="gh",
        config=None,
        engine_type="claude_code",
    )


@pytest.mark.asyncio
async def test_header_name_case_only_change_is_unchanged():
    activations = FakeActivationService(installed={"gh"})
    activations.mcp_overrides["gh"] = {"headers": {"X-Project": "same"}}

    report = await _apply(
        _engine(activations=activations),
        """schema_version: 1
manifest:
  mcp:
    - server_code: gh
      config:
        headers:
          x-project: same
""",
    )

    assert _outcomes(report) == {"gh": EntryOutcome.UNCHANGED}
    assert activations.configured == []


@pytest.mark.asyncio
async def test_mcp_center_config_validation_fails_category_before_any_write():
    activations = FakeActivationService()
    config_service = MagicMock()
    config_service.validate_bot_override.return_value = {
        "valid": False,
        "error": "MCP 在 PRE 环境没有可用的 SSE 端点",
    }

    report = await _apply(
        _engine(activations=activations, mcp_config=config_service),
        """schema_version: 1
manifest:
  mcp:
    - server_code: first
      config:
        endpoint_env: PRE
        transport_protocol: SSE
    - server_code: second
""",
    )

    assert report.status is ApplyStatus.FAILED
    assert activations.writes == 0


@pytest.mark.asyncio
async def test_a_declared_set_supplied_mcp_refuses_the_whole_apply_before_any_write():
    """An apply speaks only for Direct servers; declaring one a skill set supplies is a conflict, and the apply is
    refused before anything is written — not converted, as a Manifest apply once did."""
    activations = FakeActivationService(installed={"owned"})
    activations.set_managed = {"owned"}

    report = await _apply(
        _engine(activations=activations),
        """schema_version: 1
manifest:
  mcp:
    - server_code: new-first
    - server_code: owned
""",
    )

    assert report.status is ApplyStatus.FAILED
    assert _outcomes(report) == {"new-first": EntryOutcome.SKIPPED, "owned": EntryOutcome.FAILED}
    assert "SkillSet" in next(e.reason for e in _category(report, Category.CAPABILITIES).entries if e.identity == "mcp/owned")
    assert activations.writes == 0 and activations.installed == {"owned"}





@pytest.mark.asyncio
async def test_dependency_retained_mcp_clears_explicit_supply_without_removed_report():
    activations = FakeActivationService(installed={"mcp.dependency"})
    activations.mcp_overrides["mcp.dependency"] = {
        "headers": {"X-Stale": "value"}
    }
    reader = FakeCapabilityReader(assets=[SimpleNamespace(
        skill_id=3, name="dependent", git_path="local://dependent",
        mcp_dependencies=({"code": "mcp.dependency"},),
    )])

    report = await _apply_legacy_mcp(
        _engine(activations=activations, reader=reader),
        "schema_version: 1\nmanifest:\n  mcp: []\n",
    )

    category = _category(report, Category.CAPABILITIES)
    assert category.removals == ()
    assert _outcomes(report) == {"mcp.dependency": EntryOutcome.UPDATED}
    assert "Skill dependency" in (category.entries[0].note or "")
    assert activations.deactivated == ["mcp.dependency"]


@pytest.mark.asyncio
async def test_explicit_dependency_becomes_direct_but_reports_unchanged():
    activations = FakeActivationService()
    reader = FakeCapabilityReader(assets=[SimpleNamespace(
        skill_id=3, name="dependent", git_path="local://dependent",
        mcp_dependencies=({"code": "mcp.dependency"},),
    )])

    report = await _apply_legacy_mcp(
        _engine(activations=activations, reader=reader),
        "schema_version: 1\nmanifest:\n  mcp:\n"
        "    - server_code: mcp.dependency\n",
    )

    assert _outcomes(report) == {"mcp.dependency": EntryOutcome.UNCHANGED}
    assert activations.installed == {"mcp.dependency"}


@pytest.mark.asyncio
async def test_a_set_conflict_aborts_capabilities_while_script_still_applies():
    """The script category has no conflict of its own, but the apply is refused as a whole before any write."""
    scripts = FakeStartupScriptService(body="before")
    activations = FakeActivationService()
    activations.set_managed = {"owned"}

    report = await _apply(
        _engine(scripts, activations),
        """schema_version: 1
manifest:
  mcp:
    - server_code: owned
script:
  body: "echo hi"
""",
    )

    assert report.status is ApplyStatus.PARTIAL
    assert _category(report, Category.CAPABILITIES).aborted
    script_entry = _category(report, Section.SCRIPT).entries[0]
    assert script_entry.outcome is EntryOutcome.UPDATED
    assert scripts.body == "echo hi" and scripts.writes == 1 and activations.writes == 0


@pytest.mark.asyncio
async def test_an_omitted_set_supplied_mcp_is_left_alone():
    activations = FakeActivationService(installed={"keep", "owned"})
    activations.set_managed = {"owned"}

    report = await _apply_legacy_mcp(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: keep\n",
    )

    assert report.status is ApplyStatus.SUCCEEDED
    assert activations.deactivated == [] and _category(report, Category.CAPABILITIES).removals == ()
    assert activations.installed == {"keep", "owned"}


# ── per-category area ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_applying_only_mcp_clears_an_omitted_startup_script():
    scripts = FakeStartupScriptService(body="do not touch me")
    activations = FakeActivationService()
    engine = _engine(scripts, activations)

    await _apply(
        engine, "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n"
    )

    assert not scripts.body
    assert scripts.writes == 1


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
        c for c in report.categories if c.construct is Category.ENGINE_CONFIG
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
        c for c in report.categories if c.construct is Category.IDENTITY
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
    mcp = next(c for c in report.categories if c.construct is Category.CAPABILITIES)
    assert mcp.removals == ("mcp/drop",)
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
        phase=ApplyPhase.PRE_CONTAINER,
    )

    assert _outcomes(report) == {"script": EntryOutcome.CREATED}
    assert activations.writes == 0
    assert [c.construct for c in report.categories] == [Section.SCRIPT]


@pytest.mark.asyncio
async def test_phase_b_applies_everything_but_the_script():
    """What W13 calls once the container is up."""
    scripts, activations = FakeStartupScriptService(), FakeActivationService()
    report = await _apply(
        _engine(scripts, activations),
        _MCP_AND_SCRIPT,
        phase=ApplyPhase.ON_CONTAINER,
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
        phase=ApplyPhase.PRE_CONTAINER,
    )
    assert _outcomes(report) == {"script": EntryOutcome.CREATED}


@pytest.mark.asyncio
async def test_both_phases_together_preserve_the_declared_order():
    """On a running bot the split is invisible, and the order still holds.

    ``script`` first — which reverses design §3.4, per work-items §2.12.
    """
    report = await _apply(_engine(), _MCP_AND_SCRIPT)
    assert [c.construct for c in report.categories] == [
        Section.SCRIPT, Category.IDENTITY, Category.RESOURCES,
        Category.CAPABILITIES, Category.CLI_TOOLS,
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
        async def claim_apply_mcp(self, **kwargs):
            raise RuntimeError("device unreachable")

    activations = ExplodingActivation()
    report = await _apply(
        _engine(activations=activations),
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n",
    )

    assert _outcomes(report) == {"gh": EntryOutcome.FAILED}
    assert _category(report, Category.CAPABILITIES).aborted is True
    assert report.status is ApplyStatus.FAILED


# ── the report carries no secret ────────────────────────────────────────────
