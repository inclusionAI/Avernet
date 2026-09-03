"""The tenant survives the handoff to a worker — or nothing says it did not.

This is the one property in W13 whose failure is *completely silent*.
``get_current_avernet_tenant()`` is a total function: outside a request it
returns the **default** tenant rather than raising. So a handler that forgot to
re-establish the scope would not error, would not log, and would not fail any
test that happened to run under the default. It would create the bot and read
the manifest under the wrong tenant, and the first sign would be a customer
finding somebody else's configuration.

Nothing structural prevents it either. The queue has no tenant column, and no
request context survives to handler time — the tenant rides in the payload
precisely because there is nowhere else for it to ride.

**Every test here uses a tenant that is not the default**, which is what makes
them able to fail. Under the default they would pass whether the scope was
established or not, and would be worse than no test at all: a green assertion
standing where a guarantee was supposed to be.
"""
from __future__ import annotations

from typing import Optional

import pytest

from agentclaw.community.core.bot_config_manifest.apply.apply_task import (
    ApplyTaskHandler,
)
from agentclaw.community.core.bot_config_manifest.create_job import (
    BotCreateWithManifestHandler,
)
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    avernet_tenant_scope,
    get_current_avernet_tenant,
)

#: Deliberately not :data:`DEFAULT_AVERNET_TENANT`. See the module docstring —
#: a test written under the default cannot distinguish "the scope was
#: re-established" from "the scope was dropped and the default answered".
_TENANT = "another-tenant"


def test_the_chosen_tenant_is_not_the_one_a_dropped_scope_would_answer():
    """The premise the rest of this file rests on, asserted rather than assumed.

    If someone ever changed ``DEFAULT_AVERNET_TENANT`` to this value, every test
    below would keep passing while proving nothing. This is the guard.
    """
    assert _TENANT != DEFAULT_AVERNET_TENANT


class _TenantWatchingManifests:
    """The apply's first read of tenant-scoped storage, made observable.

    Observed at the *document* read rather than at the handler's entry, because
    that is where getting it wrong actually costs something: the document, the
    apply record and the lock are all tenant-scoped rows, and a wrong tenant
    reads an empty manifest — which apply is required to treat as "nothing
    declared" rather than as an error. The apply would then report, truthfully,
    that it applied nothing.
    """

    def __init__(self) -> None:
        self.observed: Optional[str] = None

    def get(self, *, entity_id, bot_id):
        self._observe()
        return None

    def resolve_capabilities(self, *, active_engine, bot_type):
        # Recorded here too: this is the record-free entry point the
        # pre-container phase uses, and it is reached before the document read.
        self._observe()
        from agentclaw.community.core.bot_config_manifest.capabilities import (
            resolve_capabilities,
        )

        return resolve_capabilities(
            active_engine=active_engine,
            bot_type=bot_type,
            is_teclaw=lambda engine: engine == "teclaw",
        )

    def capabilities_for_bot(self, bot):
        return self.resolve_capabilities(
            active_engine=bot.get("active_engine"), bot_type=bot.get("bot_type")
        )

    def validate(self, *, document, active_engine, bot_type):
        import yaml

        return type("V", (), {"parsed": yaml.safe_load(document) or {}})()

    def _observe(self) -> None:
        if self.observed is None:
            self.observed = get_current_avernet_tenant()


def _real_apply_service(manifests):
    """The service, with everything but the tenant boundary stubbed out.

    The scope is established inside ``run_apply_task`` rather than in the
    handler, so the handler alone proves nothing — this drives the real body.
    """
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from agentclaw.community.core.base import Base
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

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    class _Db:
        def __init__(self) -> None:
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

    db = _Db()
    return BotConfigManifestApplyService(
        manifest_service=manifests,
        apply_repository=BotConfigManifestApplyRepository(db),
        lock_repository=BotConfigManifestApplyLockRepository(db),
        script_service_provider=lambda: FakeStartupScriptService(),
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
        task_queue_provider=lambda: None,
        bot_repository=_NoBot(),
    )


class _NoBot:
    def get_by_id_and_entity(self, bot_id, entity_id):
        return None


_APPLY_PAYLOAD = {
    "apply_id": "a1",
    "entity_id": "u_owner",
    "bot_id": "b_1",
    "owner_id": "u_owner",
    "actor_id": "u_owner",
    "env": "dev",
    "tenant": _TENANT,
    "trigger": "explicit",
    "lock_token": "tok",
    "started_at": "2026-09-01T00:00:00",
    # Explicit, as every payload now is — the builder has no default.
    "phases": ["on_container", "pre_container"],
    "carry_from_apply_id": None,
    "engine_type": "claude_code",
    "bot_type": "personal",
}


def _run_apply():
    manifests = _TenantWatchingManifests()
    handler = ApplyTaskHandler(lambda: _real_apply_service(manifests))
    handler.handle(dict(_APPLY_PAYLOAD))
    return manifests


def test_the_apply_runs_under_the_submitting_requests_tenant():
    assert _run_apply().observed == _TENANT


def test_the_apply_does_not_leak_its_tenant_to_what_follows():
    """A worker runs one task after another on the same thread.

    A scope that outlived its task would hand the next one somebody else's
    tenant, which is the same failure as dropping it — in the other direction,
    and just as quiet.
    """
    _run_apply()
    assert get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT


class _RecordingPassport:
    """The creation job's first read, and where it observes the tenant."""

    def __init__(self) -> None:
        self.observed: Optional[str] = None

    def query_auth_status(self, *, bot_id, owner_workno):
        self.observed = get_current_avernet_tenant()
        return {"status": "PENDING"}


def _job(passport):
    return BotCreateWithManifestHandler(
        manifest_seam_provider=lambda: None,
        apply_service_provider=lambda: None,
        bot_repository_provider=lambda: _RecordingBots(),
        complete_authorization=lambda _payload: None,
        passport_plugin_provider=lambda: passport,
        bot_service_provider=lambda: None,
        auth_relationship_provider=lambda: _RecordedRelationship(),
    )


class _RecordedRelationship:
    def query_relationships(self, *, agent_code, work_no):
        return [{"auth_id": 1}]


class _RecordingBots:
    """The job's very first read — before Passport, before anything."""

    observed: Optional[str] = None

    def get_by_id_and_entity(self, bot_id, entity_id):
        type(self).observed = get_current_avernet_tenant()
        return None


_PAYLOAD = {
    "bot_id": "b_1",
    "entity_id": "u_owner",
    "user_id": "u_owner",
    "tenant": _TENANT,
    "env": "dev",
    "document_owner": "u_owner",
    "spec": {"engine_type": "claude_code", "bot_type": "personal"},
    "iframe_url": None,
    "redirect_url": None,
    "submitted_at": None,
}


def test_the_creation_job_runs_under_the_submitting_requests_tenant():
    """Both reads, because the bot lookup happens before Passport is asked.

    A scope established late enough to cover the Passport call but not the bot
    lookup would read the *default* tenant's bot table — and find nothing, which
    reads exactly like "the bot has not been created yet".
    """
    passport = _RecordingPassport()
    _RecordingBots.observed = None

    _job(passport).handle(dict(_PAYLOAD))

    assert _RecordingBots.observed == _TENANT
    assert passport.observed == _TENANT


def test_the_creation_job_does_not_leak_its_tenant_to_what_follows():
    _job(_RecordingPassport()).handle(dict(_PAYLOAD))
    assert get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT


@pytest.mark.parametrize(
    "handle",
    [
        _run_apply,
        lambda: _job(_RecordingPassport()).handle(dict(_PAYLOAD)),
    ],
    ids=["apply", "creation"],
)
def test_a_handler_replaces_whatever_scope_it_inherits(handle):
    """A worker thread carrying a leftover scope must not change the answer.

    The payload is the authority on which tenant a task belongs to — not
    whatever the thread was last used for. Establishing the scope conditionally,
    or only when none is set, would make a task's behaviour depend on what ran
    before it.
    """
    with avernet_tenant_scope("some-other-tenant-entirely"):
        handle()
        # And the caller's own scope is intact afterwards.
        assert get_current_avernet_tenant() == "some-other-tenant-entirely"
