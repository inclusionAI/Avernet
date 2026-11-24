"""The platform-managed teclaw creation, in order, against the real pieces (W8).

The record, then the single pre-container phase writing platform state, then
provisioning — and the platform state is there when provisioning composes the
first artifact. Proved on recorded call order against the real apply service
(with the store-backed identity port over a fake store), the real seam and the
real job. Only the document store, the queue (inline), Passport (``ISSUED``)
and the bot service are stood in for.
"""
from __future__ import annotations

from tests.community.core.apply_engine._capability_facts import materialiser_ports

from agentclaw.community.core.bot_management.creation_source import validate_creation_source

import pytest

from agentclaw.community.core.apply_engine.delivery import MaterialiserPorts
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.create_job import (
    DEFAULT_CREATE_DEADLINE_SECONDS,
    BotCreateWithManifestHandler,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_PRE_CONTAINER_TRIGGER,
    BotCreationManifestSeam,
)
from agentclaw.community.core.apply_engine.managed_files import (
    CATEGORY_IDENTITY,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.apply_engine.managed_files.ports import (
    PlatformIdentity,
)
from agentclaw.community.core.apply_engine.repository.apply_models import (  # noqa: F401
    BotConfigManifestApplyLockModel,
    BotConfigManifestApplyModel,
)
from agentclaw.community.core.apply_engine.run.service import (
    ApplyRunService,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

from tests.community.core.apply_engine._empty_cli_tools import EmptyCliTools
from tests.community.core.apply_engine._fakes import (
    resource_rows_for,
    manifest_compilers,
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeManifestContent,
    FakeMcpAuth,
    FakeMcpConfigService,
    FakeResourceFileService,
    FakeSkillUploadService,
    FakeStartupScriptService,
    real_validator,
    delivery_strategies,
    no_redeliver,
    teclaw_engine_test,
)
from agentclaw.community.core.apply_engine.delivery import (
    TeclawPlatformDelivery,
)
from tests.community.core.apply_engine.managed_files._fakes import FakeObjectStorage
from .test_creation_ordering import _Db, _InlineQueue, _IssuedPassport, _RecordedRelationship
from tests.community.core.apply_engine._fakes import FakeObjectStore

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
    def __init__(self, document=_DOCUMENT):
        self.document = document

    def get(self, *, entity_id, bot_id):
        return type("R", (), {"document": self.document})()

    def delete(self, *, entity_id, bot_id):
        return True

    def validate(self, *, document, active_engine, bot_type):
        import yaml

        return type("V", (), {"parsed": yaml.safe_load(document)})()

    def capabilities_for_bot(self, bot):
        return self.resolve_capabilities(active_engine=bot.get("active_engine"), bot_type=bot.get("bot_type"))

    def resolve_capabilities(self, *, active_engine, bot_type):
        from agentclaw.community.core.apply_engine.capabilities import resolve_capabilities

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


def _build(db, *, server_permission_check=None, document=_DOCUMENT, activation=None, provision=None):
    activation = activation or FakeActivationService()
    manifests = _Manifests(document)
    scripts = FakeStartupScriptService()
    store = ManagedFilesStore(
        object_storage=FakeObjectStorage(), store_base=lambda: "teclaw/dev/bolt_data"
    )
    queue = _InlineQueue()
    bots = _Bots()
    content = FakeManifestContent()
    fetcher = lambda: DeclaredSourceResolver(content, FakeCredentials(), FakeObjectStore())  # noqa: E731

    def platform_ports() -> MaterialiserPorts:
        return materialiser_ports(
            script_service=scripts,
            activation_service=activation,
            mcp_auth_service=FakeMcpAuth(denied={"denied"}),
            mcp_config_service=FakeMcpConfigService(),
            identity_service=PlatformIdentity(store),
            upload_service=FakeSkillUploadService(),
            capability_reader=FakeCapabilityReader(),
            package_validator=real_validator(),
            content=content,
            resource_service=FakeResourceFileService(),
            resource_rows=resource_rows_for()[0],
            cli_tool_service=EmptyCliTools(),
            shared_skills=object(),
            file_sync=None,
        )

    applies = ApplyRunService(
        server_permission_check=server_permission_check,
        capabilities=manifests,
        apply_repository=BotConfigManifestApplyRepository(db),
        lock_repository=BotConfigManifestApplyLockRepository(db),
        compilers=manifest_compilers(manifests=manifests, content=content, resolver=fetcher()),
        completion_handlers={},
        task_queue_provider=lambda: queue,
        bot_repository=bots,
        # The platform-managed deployment: the store-backed bundle above, and
        # no device-backed row — an ARCA bot in this rig would be a bug.
        delivery_strategies=delivery_strategies(
            teclaw=TeclawPlatformDelivery(
                ports=platform_ports, redeliver=no_redeliver
            ),
            is_teclaw=teclaw_engine_test,
        ),
    )
    queue.service = applies

    seam = BotCreationManifestSeam(
        validate_creation_spec=validate_creation_source,
        manifest_service=manifests,
        apply_service=applies,
        script_service_provider=lambda: scripts,
        start_job=lambda **_kwargs: None,
        find_job=lambda **_kwargs: None,
        authorization_window_seconds=DEFAULT_CREATE_DEADLINE_SECONDS,
        purge_managed_files=store.purge_owner_bot,
        purge_cli_tools=lambda entity_id, bot_id: 0,
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
            if provision is not None:
                provision(bots.record)
            # What the provisioner's compose would read: the platform's copy.
            seen_at_provision["identity"] = [r.rel_path for r in store.list(_SCOPE, category=CATEGORY_IDENTITY)]
            bots.record = {**bots.record, "binding_id": 9, "status": "ACTIVE"}
            return bots.record

    handler = BotCreateWithManifestHandler(
        validate_creation_spec=validate_creation_source,
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


def test_platform_creation_finalizes_before_provision_without_permission_query(world, monkeypatch):
    calls = []
    async def unexpected_observation(*args):
        calls.append(args)
        return args[1]
    monkeypatch.setattr(
        "agentclaw.community.core.apply_engine.run.service.finish_permission_notes", unexpected_observation
    )
    handler, applies, _seam, order, seen, _store = _build(
        world, server_permission_check=lambda *args: calls.append(args) or False
    )
    handler.handle(dict(_PAYLOAD))
    handler.handle(dict(_PAYLOAD))
    with avernet_tenant_scope(""):
        report = applies.last_apply(entity_id=_ENTITY, bot_id=_BOT)
        assert report.status is ApplyStatus.SUCCEEDED
        assert report.notes == ()
    assert order == ["record", "pre_container"]
    _drive(handler)
    with avernet_tenant_scope(""):
        assert applies.get_apply(entity_id=_ENTITY, bot_id=_BOT, apply_id=report.apply_id) == report
    assert seen["identity"] == ["identity/RULES.md"]
    assert calls == []



def test_platform_creation_mixed_mcp_artifact_is_delivered_without_permission_lookup(world):
    from unittest.mock import MagicMock
    from agentclaw.community.core.bot_management.services.teclaw_provision_service import TeclawProvisionService
    from agentclaw.community.core.service_bot.services.deploy.teclaw_compose_producer import TeclawComposeProducer
    from agentclaw.community.core.config_compose.models import McpComposeInput
    from tests.community.core.config_compose.test_config_composer import _FakeCollector, _composer

    activation = FakeActivationService()
    calls = []
    artifacts = []
    document = "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: allowed\n    - server_code: denied\n"
    def provision(bot):
        class Collector(_FakeCollector):
            def mcps(self, req):
                return [McpComposeInput(mcp_data={
                    "server_code": code, "run_mode": "REMOTE",
                    "endpoints": [{"networkType": "INTERNET", "env": "PROD",
                                   "transportProtocol": "STREAMABLE_HTTP", "url": "https://example.test/mcp"}],
                }, endpoint_env="PROD") for code in sorted(activation.installed)]
        producer = TeclawComposeProducer(_composer(Collector()), MagicMock(fetch=lambda bot: {}))
        router, baas, binding, queue, bots = (MagicMock() for _ in range(5))
        router.resolve.return_value = producer
        baas.create_teclaw_bot.return_value = {"bot_uuid": "device", "publish_id": 1}
        binding.insert_binding.return_value = 1
        service = TeclawProvisionService(baas_service=baas, deploy_artifact_producer_router=router,
            device_binding_repo=binding, task_queue_service=queue, bot_repository=bots, teclaw_template_uuid="test")
        service.provision(bot=bot, owner_id=_ENTITY)
        artifacts.append(baas.create_teclaw_bot.call_args.kwargs["config_artifact"])

    handler, applies, _seam, order, _seen, _store = _build(world, document=document, activation=activation,
        provision=provision, server_permission_check=lambda *args: calls.append(args) or args[1] != "denied")
    payload = {**_PAYLOAD, "permission_subject": _ENTITY}
    handler.handle(payload)
    handler.handle(payload)
    with avernet_tenant_scope(""):
        report = applies.last_apply(entity_id=_ENTITY, bot_id=_BOT)
        assert report.status is ApplyStatus.SUCCEEDED and report.notes == ()
    assert order == ["record", "pre_container"]
    handler.handle(payload)
    assert activation.installed == {"allowed", "denied"}
    assert {s["server_code"] for s in artifacts[0]["mcp"]["servers"]} == {"allowed", "denied"}
    assert calls == []
    with avernet_tenant_scope(""):
        assert applies.get_apply(entity_id=_ENTITY, bot_id=_BOT, apply_id=report.apply_id) == report


def test_late_worker_cannot_rewrite_finalized_report(world):
    handler, applies, _seam, _order, _seen, _store = _build(world)
    handler.handle(dict(_PAYLOAD))
    handler.handle(dict(_PAYLOAD))
    with avernet_tenant_scope(""):
        before = applies.last_apply(entity_id=_ENTITY, bot_id=_BOT)
        BotConfigManifestApplyRepository(world).finish(
            env="dev", entity_id=_ENTITY, bot_id=_BOT, apply_id=before.apply_id,
            status="FAILED", report='{"notes": ["late"]}',
        )
        assert applies.get_apply(entity_id=_ENTITY, bot_id=_BOT, apply_id=before.apply_id) == before


def test_a_creation_that_ends_without_a_bot_purges_the_store(world, monkeypatch):
    monkeypatch.setattr("agentclaw.community.utils.env_utils.get_current_env", lambda: "dev")
    _handler, _applies, seam, _order, _seen, store = _build(world)
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="RULES.md", rel_path="identity/RULES.md", content=b"x")
    assert seam.discard(entity_id=_ENTITY, bot_id=_BOT, owner_id=_ENTITY) is True
    assert store.list(_SCOPE, category=CATEGORY_IDENTITY) == []
