"""The platform-managed teclaw creation, in order, against the real pieces (W8).

The record, then the single pre-container phase writing platform state, then
provisioning — and the platform state is there when provisioning composes the
first artifact. Proved on recorded call order against the real apply service
(with the store-backed identity port over a fake store), the real seam and the
real job. Only the document store, the queue (inline), Passport (``ISSUED``)
and the bot service are stood in for.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.apply.delivery import MaterialiserPorts
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import EntryFetcher
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.create_job import (
    DEFAULT_CREATE_DEADLINE_SECONDS,
    BotCreateWithManifestHandler,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_PRE_CONTAINER_TRIGGER,
    BotCreationManifestSeam,
)
from agentclaw.community.core.bot_config_manifest.managed_files import (
    CATEGORY_IDENTITY,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.bot_config_manifest.managed_files.ports import (
    StoreIdentityPort,
)
from agentclaw.community.core.bot_config_manifest.repository.apply_models import (  # noqa: F401
    BotConfigManifestApplyLockModel,
    BotConfigManifestApplyModel,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_apply_service import (
    BotConfigManifestApplyService,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

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
from ..managed_files._fakes import FakeObjectStorage
from .test_creation_ordering import _Db, _InlineQueue, _IssuedPassport, _RecordedRelationship

_ENTITY = "u_owner"
_BOT = "b_teclaw"
_DOCUMENT = "schema_version: 1\nmanifest:\n  identity:\n    - type: RULES.md\n      content: '# be kind'\n"
_PAYLOAD = {
    "bot_id": _BOT, "entity_id": _ENTITY, "user_id": _ENTITY, "tenant": "", "env": "dev",
    "document_owner": _ENTITY, "spec": {"engine_type": "teclaw", "bot_type": "personal"},
    "iframe_url": None, "redirect_url": None, "submitted_at": None,
}
_SCOPE = ManagedFileScope(entity_type="staff", entity_id=_ENTITY, bot_id=_BOT)


class _Manifests:
    def get(self, *, entity_id, bot_id):
        return type("R", (), {"document": _DOCUMENT})()

    def delete(self, *, entity_id, bot_id):
        return True

    def validate(self, *, document, active_engine, bot_type):
        import yaml

        return type("V", (), {"parsed": yaml.safe_load(document)})()

    def capabilities_for_bot(self, bot):
        return self.resolve_capabilities(active_engine=bot.get("active_engine"), bot_type=bot.get("bot_type"))

    def resolve_capabilities(self, *, active_engine, bot_type):
        from agentclaw.community.core.bot_config_manifest.capabilities import resolve_capabilities

        return resolve_capabilities(active_engine=active_engine, bot_type=bot_type, is_teclaw=lambda e: e == "teclaw")


class _Bots:
    def __init__(self) -> None:
        self.record = None

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.record

    def get_by_id_and_owner(self, bot_id, owner_id):
        return self.record


@pytest.fixture
def world():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return _Db(engine)


def _build(db):
    scripts = FakeStartupScriptService()
    store = ManagedFilesStore(
        object_storage=FakeObjectStorage(), store_base=lambda: "teclaw/dev/bolt_data"
    )
    queue = _InlineQueue()
    bots = _Bots()
    fetcher = lambda: EntryFetcher(FakeGuardedFetcher(), FakeManifestContent(), FakeCredentials())  # noqa: E731

    def platform_ports() -> MaterialiserPorts:
        return MaterialiserPorts(
            script_service=scripts,
            activation_service=FakeActivationService(),
            mcp_auth_service=FakeMcpAuth(),
            identity_service=StoreIdentityPort(store),
            upload_service=FakeSkillUploadService(),
            capability_reader=FakeCapabilityReader(),
            package_validator=real_validator(),
            entry_fetcher=fetcher(),
            resource_service=FakeResourceFileService(),
            cli_tool_service=object(),
        )

    applies = BotConfigManifestApplyService(
        manifest_service=_Manifests(),
        apply_repository=BotConfigManifestApplyRepository(db),
        lock_repository=BotConfigManifestApplyLockRepository(db),
        script_service_provider=lambda: scripts,
        activation_service_provider=lambda: FakeActivationService(),
        mcp_auth_service_provider=lambda: FakeMcpAuth(),
        identity_service_provider=lambda: FakeIdentityService(),
        upload_service_provider=lambda: FakeSkillUploadService(),
        capability_reader_provider=lambda: FakeCapabilityReader(),
        package_validator_provider=lambda: real_validator(),
        entry_fetcher_provider=fetcher,
        resource_service_provider=lambda: FakeResourceFileService(),
        cli_tool_service_factory=lambda family: None,
        git_client_provider=lambda: FakeGitClient(),
        task_queue_provider=lambda: queue,
        bot_repository=bots,
        is_teclaw=lambda engine: engine == "teclaw",
        teclaw_platform_managed=True,
        teclaw_platform_ports_provider=platform_ports,
    )
    queue.service = applies

    seam = BotCreationManifestSeam(
        manifest_service=_Manifests(),
        apply_service=applies,
        script_service_provider=lambda: scripts,
        start_job=lambda **_kwargs: None,
        find_job=lambda **_kwargs: None,
        authorization_window_seconds=DEFAULT_CREATE_DEADLINE_SECONDS,
        purge_managed_files=store.purge_owner_bot,
    )

    order: list[str] = []
    seen_at_provision: dict[str, list[str]] = {}
    real_pre_container = seam.apply_pre_container

    def recording_pre_container(**kwargs):
        order.append("pre_container")
        return real_pre_container(**kwargs)

    seam.apply_pre_container = recording_pre_container  # type: ignore[method-assign]

    def create(_payload, **kw):
        order.append("create" if kw.get("provision", True) else "record")
        bots.record = {
            "bot_id": _BOT, "entity_id": _ENTITY, "owner_id": _ENTITY, "status": "PENDING",
            "binding_id": None, "active_engine": "teclaw", "bot_type": "personal", "env": "dev",
            "ext": {"passport": {"agent_code": "agent-t", "status": "ISSUED"}},
        }

    class _BotService:
        def provision_bot(self, bot_id, user_id, nick_name, **kw):
            order.append("provision")
            # What the provisioner's compose would read: the platform's copy.
            seen_at_provision["identity"] = [r.rel_path for r in store.list(_SCOPE, category=CATEGORY_IDENTITY)]
            bots.record = {**bots.record, "binding_id": 9, "status": "ACTIVE"}
            return bots.record

    handler = BotCreateWithManifestHandler(
        manifest_seam_provider=lambda: seam,
        apply_service_provider=lambda: applies,
        bot_repository_provider=lambda: bots,
        complete_authorization=create,
        passport_plugin_provider=lambda: _IssuedPassport(),
        bot_service_provider=_BotService,
        auth_relationship_provider=_RecordedRelationship,
        creation_sequence=lambda engine: applies.delivery_for_engine(engine).creation_sequence,
    )
    return handler, applies, seam, order, seen_at_provision, store


def _drive(handler, times: int = 6) -> None:
    from agentclaw.community.core.task_queue.types import Complete, Fail

    for _ in range(times):
        outcome = handler.handle(dict(_PAYLOAD))
        if isinstance(outcome, (Complete, Fail)):
            return


def test_the_record_then_the_phase_then_provisioning_in_that_order(world):
    handler, applies, _seam, order, seen, _store = _build(world)

    _drive(handler)

    assert order == ["record", "pre_container", "provision"], order
    # The platform's copy of the identity file existed when provisioning ran —
    # what the first artifact is composed from.
    assert seen["identity"] == ["identity/RULES.md"]
    with avernet_tenant_scope(""):
        report = applies.last_apply(entity_id=_ENTITY, bot_id=_BOT)
    assert report is not None and report.trigger == CREATE_PRE_CONTAINER_TRIGGER
    assert report.status is ApplyStatus.SUCCEEDED
    # No post-container phase was ever started under this sequence.
    assert applies.delivery_for_engine("teclaw").needs_container() is False


def test_a_creation_that_ends_without_a_bot_purges_the_store(world, monkeypatch):
    monkeypatch.setattr("agentclaw.community.utils.env_utils.get_current_env", lambda: "dev")
    _handler, _applies, seam, _order, _seen, store = _build(world)
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="RULES.md", rel_path="identity/RULES.md", content=b"x")
    assert seam.discard(entity_id=_ENTITY, bot_id=_BOT, owner_id=_ENTITY) is True
    assert store.list(_SCOPE, category=CATEGORY_IDENTITY) == []
