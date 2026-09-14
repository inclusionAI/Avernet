"""The apply service runs every apply through the family's delivery strategy (W8).

Real repositories on SQLite, an inline queue, fakes for the ports — the same
shape as the lifecycle suite — so what is asserted is the service's own
behaviour: which phase table it walks, which ports it hands the registry, and
that the strategy's closing step runs once and lands on the report.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    ArcaDelivery,
    MaterialiserPorts,
    TeclawDelivery,
)
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.bot_config_manifest.apply import triggers
from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.repository.apply_models import (  # noqa: F401
    BotConfigManifestApplyLockModel,
    BotConfigManifestApplyModel,
)
from agentclaw.community.core.bot_config_manifest.repository.models import (  # noqa: F401
    BotConfigManifestModel,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_apply_service import (
    BotConfigManifestApplyService,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)

from ._fakes import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeGitClient,
    FakeIdentityService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeResourceFileService,
    FakeSkillUploadService,
    FakeStartupScriptService,
    real_validator,
)
from tests.community.core.bot_config_manifest.apply._fakes import FakeObjectStore

_ENTITY = "u_owner"
_BOT = "b_teclaw"
#: Declares one container-bound category and nothing else, so which phase it
#: lands in is the whole observation.
_DOCUMENT = "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: github\n"
_TECLAW_BOT = {
    "bot_id": _BOT,
    "owner_id": _ENTITY,
    "entity_id": _ENTITY,
    "active_engine": "teclaw",
    "bot_type": "personal",
    "status": "ACTIVE",
    "binding_id": 7,
}
_ARCA_BOT = {**_TECLAW_BOT, "bot_id": "b_arca", "active_engine": "claude_code"}


class _Db:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class _Manifests:
    def get(self, *, entity_id, bot_id):
        return type("R", (), {"document": _DOCUMENT})()

    def validate(self, *, document, active_engine, bot_type):
        import yaml

        return type("V", (), {"parsed": yaml.safe_load(document)})()

    def capabilities_for_bot(self, bot):
        from agentclaw.community.core.bot_config_manifest.capabilities import (
            resolve_capabilities,
        )

        return resolve_capabilities(
            active_engine=bot.get("active_engine"),
            bot_type=bot.get("bot_type"),
            is_teclaw=lambda engine: engine == "teclaw",
        )


class _Bots:
    def __init__(self, record):
        self._record = record

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self._record


class _InlineQueue:
    def __init__(self):
        self.service = None

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        self.service.run_apply_task(payload)
        return (None, True)


def _world(*, bot, platform_managed, platform_activation=None, redeliver=None):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    db = _Db(engine)
    queue = _InlineQueue()
    device_activation = FakeActivationService()
    platform_activation = platform_activation or FakeActivationService()

    def platform_ports():
        return MaterialiserPorts(
            script_service=FakeStartupScriptService(),
            activation_service=platform_activation,
            mcp_auth_service=FakeMcpAuth(),
            identity_service=FakeIdentityService(),
            upload_service=FakeSkillUploadService(),
            capability_reader=FakeCapabilityReader(),
            package_validator=real_validator(),
            entry_fetcher=DeclaredSourceResolver(
                FakeManifestContent(), FakeCredentials(), FakeObjectStore()
            ),
            resource_service=FakeResourceFileService(),
            cli_tool_service=object(),
        )

    service = BotConfigManifestApplyService(
        manifest_service=_Manifests(),
        apply_repository=BotConfigManifestApplyRepository(db),
        lock_repository=BotConfigManifestApplyLockRepository(db),
        script_service_provider=lambda: FakeStartupScriptService(),
        activation_service_provider=lambda: device_activation,
        mcp_auth_service_provider=lambda: FakeMcpAuth(),
        identity_service_provider=lambda: FakeIdentityService(),
        upload_service_provider=lambda: FakeSkillUploadService(),
        capability_reader_provider=lambda: FakeCapabilityReader(),
        package_validator_provider=lambda: real_validator(),
        entry_fetcher_provider=lambda: DeclaredSourceResolver(
            FakeManifestContent(), FakeCredentials(), FakeObjectStore()
        ),
        resource_service_provider=lambda: FakeResourceFileService(),
        cli_tool_service_factory=lambda family: None,
        git_client_provider=lambda: FakeGitClient(),
        task_queue_provider=lambda: queue,
        bot_repository=_Bots(bot),
        is_teclaw=lambda engine: engine == "teclaw",
        teclaw_platform_managed=platform_managed,
        teclaw_platform_ports_provider=platform_ports,
        redeliver=redeliver,
    )
    queue.service = service
    return service, device_activation, platform_activation


def _apply(service, bot, phase):
    # A phase is a creation half, so it rides the creation trigger that names
    # it: ``start_apply`` refuses the two stated apart.
    service.start_apply(
        entity_id=_ENTITY,
        bot_id=bot["bot_id"],
        bot=bot,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        trigger=(
            triggers.CREATE_PRE_CONTAINER
            if phase is ApplyPhase.PRE_CONTAINER
            else triggers.CREATE_ON_CONTAINER
        ),
        phase=phase,
    )
    return service.last_apply(entity_id=_ENTITY, bot_id=bot["bot_id"])


def test_the_service_selects_the_strategy_by_engine_and_switch() -> None:
    service, _, _ = _world(bot=_TECLAW_BOT, platform_managed=True)
    assert isinstance(service.delivery_for_bot(_ARCA_BOT), ArcaDelivery)
    teclaw = service.delivery_for_bot(_TECLAW_BOT)
    assert isinstance(teclaw, TeclawDelivery) and teclaw.platform_managed
    service_off, _, _ = _world(bot=_TECLAW_BOT, platform_managed=False)
    assert not service_off.delivery_for_engine("teclaw").platform_managed


def test_teclaw_on_applies_container_bound_categories_in_the_pre_container_phase() -> None:
    notes: list[object] = []

    async def redeliver(ctx):
        notes.append(ctx.bot_id)
        return None

    service, device_activation, platform_activation = _world(
        bot=_TECLAW_BOT, platform_managed=True, redeliver=redeliver
    )
    report = _apply(service, _TECLAW_BOT, ApplyPhase.PRE_CONTAINER)
    assert report.status is ApplyStatus.SUCCEEDED
    assert [c.construct.value for c in report.categories] == ["mcp"]
    # The platform ports were used, the device ports were not.
    assert platform_activation.activated == ["github"]
    assert device_activation.activated == []
    # One closing redeliver, after the categories.
    assert notes == [_BOT]
    assert report.notes == ()


def test_teclaw_on_records_a_failed_closing_step_as_a_note() -> None:
    async def redeliver(ctx):
        return "redeliver failed: container unreachable"

    service, _, _ = _world(bot=_TECLAW_BOT, platform_managed=True, redeliver=redeliver)
    report = _apply(service, _TECLAW_BOT, ApplyPhase.PRE_CONTAINER)
    assert report.status is ApplyStatus.SUCCEEDED
    assert report.notes == ("redeliver failed: container unreachable",)
    # And it survives the round trip through the stored record.
    again = service.get_apply(entity_id=_ENTITY, bot_id=_BOT, apply_id=report.apply_id)
    assert again.notes == report.notes


def test_teclaw_off_is_the_pre_w8_shape() -> None:
    calls: list[object] = []

    async def redeliver(ctx):
        calls.append(ctx)
        return "never"

    service, device_activation, platform_activation = _world(
        bot=_TECLAW_BOT, platform_managed=False, redeliver=redeliver
    )
    pre = _apply(service, _TECLAW_BOT, ApplyPhase.PRE_CONTAINER)
    assert pre.categories == ()  # mcp is not pre-container with the switch off
    on = _apply(service, _TECLAW_BOT, ApplyPhase.ON_CONTAINER)
    assert [c.construct.value for c in on.categories] == ["mcp"]
    assert device_activation.activated == ["github"]
    assert platform_activation.activated == []
    assert calls == []
    assert on.notes == ()


def test_arca_never_sees_the_switch() -> None:
    calls: list[object] = []

    async def redeliver(ctx):
        calls.append(ctx)
        return "never"

    service, device_activation, platform_activation = _world(
        bot=_ARCA_BOT, platform_managed=True, redeliver=redeliver
    )
    pre = _apply(service, _ARCA_BOT, ApplyPhase.PRE_CONTAINER)
    assert pre.categories == ()
    on = _apply(service, _ARCA_BOT, ApplyPhase.ON_CONTAINER)
    assert [c.construct.value for c in on.categories] == ["mcp"]
    assert device_activation.activated == ["github"]
    assert platform_activation.activated == []
    assert calls == []


@pytest.mark.asyncio
async def test_dry_run_runs_through_the_strategy_and_writes_nothing() -> None:
    """A dry run walks both phases, so re-phasing is invisible to it; what it
    proves is that the strategy's registry is the one consulted (the platform
    activation is *read* for the plan) and that no port is written."""
    calls: list[object] = []

    async def redeliver(ctx):
        calls.append(ctx)
        return "never"

    service, device_activation, platform_activation = _world(
        bot=_TECLAW_BOT, platform_managed=True, redeliver=redeliver
    )
    report = await service.dry_run(
        entity_id=_ENTITY, bot_id=_BOT, bot=_TECLAW_BOT, owner_id=_ENTITY, actor_id=_ENTITY
    )
    assert [c.construct.value for c in report.categories] == ["mcp"]
    assert platform_activation.activated == []
    assert device_activation.activated == []
    assert calls == []  # the closing step is a real-apply thing
    assert report.notes == ()


def test_a_raising_closing_step_is_a_note_not_a_failure() -> None:
    async def redeliver(ctx):
        raise ConnectionError("container unreachable")

    service, _, platform_activation = _world(
        bot=_TECLAW_BOT, platform_managed=True, redeliver=redeliver
    )
    report = _apply(service, _TECLAW_BOT, ApplyPhase.PRE_CONTAINER)
    assert report.status is ApplyStatus.SUCCEEDED
    assert platform_activation.activated == ["github"]
    assert report.notes == ("delivery could not be closed: ConnectionError",)


def test_notes_survive_the_carry_forward_merge() -> None:
    from datetime import datetime

    from agentclaw.community.core.bot_config_manifest.apply.carry_forward import (
        carry_forward,
    )
    from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyReport

    earlier = ApplyReport(
        apply_id="a", bot_id=_BOT, trigger="create:pre_container",
        status=ApplyStatus.SUCCEEDED, started_at=datetime.now(), notes=("phase A note",),
    )
    later = ApplyReport(
        apply_id="b", bot_id=_BOT, trigger="create:on_container",
        status=ApplyStatus.SUCCEEDED, started_at=datetime.now(), notes=("phase B note",),
    )

    class _Applies:
        def get(self, **_kw):
            return object()

    merged = carry_forward(
        later,
        ctx=type("C", (), {"env": "dev", "entity_id": _ENTITY, "bot_id": _BOT})(),
        carry_from_apply_id="a",
        applies=_Applies(),
        to_report=lambda record, **_kw: earlier,
    )
    assert merged.notes == ("phase A note", "phase B note")


def test_the_api_payload_carries_the_notes() -> None:
    from datetime import datetime

    from agentclaw.community.adapters.http.openapi_v1.bots.config_manifest_support import (
        apply_payload,
    )
    from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyReport

    report = ApplyReport(
        apply_id="a", bot_id=_BOT, trigger="put", status=ApplyStatus.SUCCEEDED,
        started_at=datetime.now(), notes=("redeliver failed",),
    )
    assert apply_payload(report).notes == ["redeliver failed"]


def _folded(earlier, later):
    """``carry_forward`` over two hand-built reports, with the read stubbed out."""
    from agentclaw.community.core.bot_config_manifest.apply.carry_forward import (
        carry_forward,
    )

    class _Applies:
        def get(self, **_kw):
            return object()

    return carry_forward(
        later,
        ctx=type("C", (), {"env": "dev", "entity_id": _ENTITY, "bot_id": _BOT})(),
        carry_from_apply_id="a",
        applies=_Applies(),
        to_report=lambda record, **_kw: earlier,
    )


def _phase_report(apply_id, trigger, *, sources=(), categories=()):
    from datetime import datetime

    from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyReport

    return ApplyReport(
        apply_id=apply_id,
        bot_id=_BOT,
        trigger=trigger,
        status=ApplyStatus.SUCCEEDED,
        started_at=datetime.now(),
        sources=tuple(sources),
        categories=tuple(categories),
    )


def test_one_source_used_by_both_phases_is_one_row() -> None:
    """The invariant the fold used to break: one declaration, one row.

    A creation's two applies carry two ``SourceSession``s, so a source serving
    categories on both sides of container provisioning is adopted twice — once
    per session, each idempotent only within itself. Concatenating the two
    reports used to publish it twice, against what the schema and
    ``SourceResolution`` both promise a reader.
    """
    from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
        SourceResolution,
    )

    content = SourceResolution(
        name="content",
        url="https://git.corp/manifest-testing.git",
        ref="main",
        mode="non_strict",
        resolved_sha="7" * 40,
        auth="gh-readonly",
    )
    merged = _folded(
        _phase_report("a", "create:pre_container", sources=[content]),
        _phase_report("b", "create:on_container", sources=[content]),
    )
    assert merged.sources == (content,)


def test_two_resolutions_of_one_moving_ref_stay_two_rows() -> None:
    """Dedup is on the whole row, so a ref that moved between phases keeps both.

    ``(url, ref, mode)`` would collapse these, and collapsing them would report
    a delivery that did not happen: phase A served its categories from one
    commit and phase B served its own from another. Both are true, and the
    carried row goes first because that is the order they were resolved in.
    """
    from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
        SourceResolution,
    )

    url = "https://git.corp/manifest-testing.git"
    before = SourceResolution(name="content", url=url, ref="main",
                              mode="non_strict", resolved_sha="7" * 40)
    after = SourceResolution(name="content", url=url, ref="main",
                             mode="non_strict", resolved_sha="9" * 40)
    merged = _folded(
        _phase_report("a", "create:pre_container", sources=[before]),
        _phase_report("b", "create:on_container", sources=[after]),
    )
    assert merged.sources == (before, after)


def test_the_folded_category_order_is_untouched() -> None:
    """Only ``sources`` is deduped; ``categories`` still concatenates in order.

    The carried categories go first because that is ``APPLY_ORDER``'s own
    order — ``script`` is position 0 — and it is what keeps a pre-container
    ``script`` from reading as though it had vanished.
    """
    from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
        CategoryResult,
    )
    from agentclaw.community.core.bot_config_manifest.capabilities import (
        ManifestCategory,
        ManifestSection,
    )

    script = CategoryResult(construct=ManifestSection.SCRIPT)
    mcp = CategoryResult(construct=ManifestCategory.MCP)
    skills = CategoryResult(construct=ManifestCategory.SKILLS)
    merged = _folded(
        _phase_report("a", "create:pre_container", categories=[script]),
        _phase_report("b", "create:on_container", categories=[mcp, skills]),
    )
    assert [c.construct for c in merged.categories] == [
        ManifestSection.SCRIPT,
        ManifestCategory.MCP,
        ManifestCategory.SKILLS,
    ]
