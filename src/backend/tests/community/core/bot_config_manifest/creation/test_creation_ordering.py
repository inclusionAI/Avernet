"""Phase A lands before the bot is created. The ordering, proved (W13, #1696).

This is the item's whole reason for existing. ``BaasService`` composes a bot's
start command by reading its startup-script row, so a script delivered *after*
the container is up has already missed the first boot — the exact window the two
calls this endpoint replaces used to leave open. "The row is written first" is
therefore not an implementation detail; it is the guarantee.

Proved on **recorded call order** rather than on timing, and against the real
apply service, the real seam and the real creation job. A test that slept and
hoped would pass on a machine that happened to be slow enough; a test that stubs
the apply away would prove only that the stub was called.

The second half is just as load-bearing and easier to lose: a manifest layer
failure must **never abort creation**. A phase A that fails still produces a bot
— the failure belongs in the report, not in a refusal to create the thing the
caller asked for.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyStatus,
    EntryOutcome,
)
from agentclaw.community.core.bot_config_manifest.create_job import (
    DEFAULT_CREATE_DEADLINE_SECONDS,
    BotCreateWithManifestHandler,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_PRE_CONTAINER_TRIGGER,
    BotCreationManifestSeam,
)

# Imported for side effect: registers the models on ``Base.metadata``.
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
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from ..apply._fakes import (
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
    real_validator,
)

_ENTITY = "u_owner"
_BOT = "b_ordering"
_SCRIPT = "echo provisioned"
_DOCUMENT = f'schema_version: 1\nscript:\n  body: "{_SCRIPT}"\n'

_PAYLOAD = {
    "bot_id": _BOT,
    "entity_id": _ENTITY,
    "user_id": _ENTITY,
    "tenant": "",
    "env": "dev",
    "document_owner": _ENTITY,
    "spec": {"engine_type": "claude_code", "bot_type": "personal"},
    "iframe_url": "https://auth.example/consent",
    "redirect_url": None,
    "submitted_at": None,
}


class _Db:
    def __init__(self, engine) -> None:
        self._sessions = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._sessions()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class _Manifests:
    """The document half, reduced to what apply asks of it."""

    def get(self, *, entity_id, bot_id):
        return type("R", (), {"document": _DOCUMENT})()

    def validate(self, *, document, active_engine, bot_type):
        import yaml

        return type("V", (), {"parsed": yaml.safe_load(document)})()

    def capabilities_for_bot(self, bot):
        return self.resolve_capabilities(
            active_engine=bot.get("active_engine"), bot_type=bot.get("bot_type")
        )

    def resolve_capabilities(self, *, active_engine, bot_type):
        """The record-free entry point — the one this whole file exercises.

        Phase A runs before the bot exists, so there is no record to read the
        engine off; it comes from the creation's own spec instead.
        """
        from agentclaw.community.core.bot_config_manifest.capabilities import (
            resolve_capabilities,
        )

        return resolve_capabilities(
            active_engine=active_engine,
            bot_type=bot_type,
            is_teclaw=lambda engine: engine == "teclaw",
        )


class _InlineQueue:
    """A worker that claims at once — which is what the real one is told to do.

    The apply task type registers with ``wake_on_enqueue=True`` precisely so a
    due apply runs immediately rather than waiting out an idle poll, so running
    it inline is a faithful stand-in rather than a shortcut. It also keeps the
    recorded order below meaningful: if the apply ran on some other thread, the
    order would be a race.
    """

    def __init__(self) -> None:
        self.service = None

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        self.service.run_apply_task(payload)
        return (None, True)


class _Bots:
    """The bot record, absent until creation writes it — as it really is."""

    def __init__(self) -> None:
        self.record = None

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.record


class _FailingScripts(FakeStartupScriptService):
    """A startup-script store that refuses the write.

    The realistic shape of a failing phase A: the document is valid, the
    materialiser runs, and the write itself does not land.
    """

    def put(self, **_kwargs):
        raise RuntimeError("the startup-script store refused the write")


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return _Db(engine)


def _build(db, *, scripts=None):
    """The real service, the real seam and the real job, wired as in production.

    Only three things are stood in for, and none of them is the ordering: the
    document store, the queue (run inline, see :class:`_InlineQueue`) and
    Passport, which answers ``ISSUED`` because who authorized is not what this
    file is about.
    """
    scripts = scripts if scripts is not None else FakeStartupScriptService()
    queue = _InlineQueue()
    applies = BotConfigManifestApplyService(
        manifest_service=_Manifests(),
        apply_repository=BotConfigManifestApplyRepository(db),
        lock_repository=BotConfigManifestApplyLockRepository(db),
        script_service_provider=lambda: scripts,
        activation_service_provider=lambda: FakeActivationService(),
        mcp_auth_service_provider=lambda: FakeMcpAuth(),
        # W5's materialisers. These suites' documents declare only script, so
        # the fetch-consuming categories are never reached — but they must exist
        # for the registry to register.
        identity_service_provider=lambda: FakeIdentityService(),
        upload_service_provider=lambda: FakeSkillUploadService(),
        capability_reader_provider=lambda: FakeCapabilityReader(),
        package_validator_provider=lambda: real_validator(),
        entry_fetcher_provider=lambda: EntryFetcher(
            FakeGuardedFetcher(), FakeManifestContent(), FakeCredentials()
        ),
        # W6's resources materialiser and W7's git transport: unreached by
        # this suite's document, but the registry registers them and the
        # session is built per apply regardless.
        resource_service_provider=lambda: FakeResourceFileService(),
        cli_tool_service_factory=lambda family: None,
        git_client_provider=lambda: FakeGitClient(),
        task_queue_provider=lambda: queue,
        bot_repository=_Bots(),
    )
    queue.service = applies

    seam = BotCreationManifestSeam(
        manifest_service=_Manifests(),
        apply_service=applies,
        script_service_provider=lambda: scripts,
        start_job=lambda **_kwargs: None,
        find_job=lambda **_kwargs: None,
        authorization_window_seconds=DEFAULT_CREATE_DEADLINE_SECONDS,
        purge_cli_tools=lambda entity_id, bot_id: 0,
    )

    bots = _Bots()
    order: list[str] = []
    seen_at_creation: dict[str, str] = {}

    # ``apply_pre_container`` is the seam's, unwrapped, with one recording line:
    # what is being proved is that the *real* one has already finished when
    # creation is called.
    real_pre_container = seam.apply_pre_container

    def recording_pre_container(**kwargs):
        order.append("pre_container")
        return real_pre_container(**kwargs)

    seam.apply_pre_container = recording_pre_container  # type: ignore[method-assign]

    def create(_payload):
        order.append("create")
        # Read at the moment the bot is created, which is the moment
        # ``_build_create_bot_payload`` composes the start command — the same
        # call, with the same two keys, that ``_resolve_startup_script`` makes.
        seen_at_creation["script"] = scripts.get_body(
            entity_id=_ENTITY, bot_id=_BOT
        )
        bots.record = {"bot_id": _BOT, "entity_id": _ENTITY, "status": "STARTING"}

    handler = BotCreateWithManifestHandler(
        manifest_seam_provider=lambda: seam,
        apply_service_provider=lambda: applies,
        bot_repository_provider=lambda: bots,
        complete_authorization=create,
        passport_plugin_provider=lambda: _IssuedPassport(),
        bot_service_provider=lambda: None,
        auth_relationship_provider=_RecordedRelationship,
    )
    return handler, applies, order, seen_at_creation, scripts


class _RecordedRelationship:
    """The owner relationship, already written — the ordinary case here."""

    def query_relationships(self, *, agent_code, work_no):
        return [{"auth_id": 1}]


class _IssuedPassport:
    def query_auth_status(self, *, bot_id, owner_workno):
        return {"status": "ISSUED"}


def _drive(handler, times: int = 4) -> None:
    """Run the job the way a worker would, until it stops asking."""
    from agentclaw.community.core.task_queue.types import Complete, Fail

    for _ in range(times):
        outcome = handler.handle(dict(_PAYLOAD))
        if isinstance(outcome, (Complete, Fail)):
            return


def test_the_pre_container_phase_finishes_before_creation_is_called(world):
    handler, _applies, order, _seen, _scripts = _build(world)

    _drive(handler)

    assert order[:2] == ["pre_container", "create"], order
    assert order.count("create") == 1, "creation ran more than once"


def test_the_startup_script_row_exists_when_the_start_command_is_composed(world):
    """The guarantee itself: what a first boot would actually carry.

    Asserted on the read ``BaasService._resolve_startup_script`` performs —
    ``get_body(entity_id=…, bot_id=…)`` — rather than on the row, so a change to
    where the script is stored still has to keep this answer true. What BaaS then
    does with a non-empty script is #926's own contract.
    """
    handler, _applies, _order, seen, _scripts = _build(world)

    _drive(handler)

    assert seen["script"] == _SCRIPT, (
        "creation ran with an empty startup script: the first boot would come "
        "up without the script the manifest declared"
    )


def test_a_failed_pre_container_phase_still_creates_the_bot(world):
    """§2.7: nothing in a manifest may abort creation.

    The caller asked for a bot. A materialiser that could not write is a fact
    about the configuration, and refusing to create the bot would turn a partial
    configuration into no bot at all — a strictly worse answer, and one the
    caller cannot retry without starting over.
    """
    handler, applies, order, _seen, _scripts = _build(
        world, scripts=_FailingScripts()
    )

    _drive(handler)

    assert "create" in order, "a failed phase A stopped the bot being created"
    assert order.index("pre_container") < order.index("create")

    # Read inside the job's own tenant scope, because that is where the record
    # was written. Outside it ``get_current_avernet_tenant()`` answers the
    # *default* tenant rather than raising, so an unscoped read here would
    # quietly find nothing and this test would fail for the wrong reason — the
    # same silent failure the job re-establishes the scope to avoid.
    with avernet_tenant_scope(str(_PAYLOAD["tenant"])):
        report = applies.last_apply(entity_id=_ENTITY, bot_id=_BOT)
    assert report is not None
    assert report.trigger == CREATE_PRE_CONTAINER_TRIGGER
    assert report.status is ApplyStatus.FAILED, (
        "the failure has to be in the report; it is the only place a caller "
        "can see it, since creation went ahead regardless"
    )
    assert any(
        entry.construct.value == "script"
        and entry.outcome is EntryOutcome.FAILED
        for entry in report.entries
    ), "the report does not name the entry that failed"
