"""The provisioner's first artifact carries the manifest (W8 Task 16).

End to end, against the real pieces: a stored manifest declaring an identity
file, a resource and a skill; the platform-managed pre-container phase run by
the real apply service over the store-backed ports; then the real
``TeclawProvisionService`` producing its artifact through the real
``TeclawComposeProducer``, the real ``ConfigComposer`` and the real collector —
whose DB categories are stood in for and whose managed-files reader is the
real one over the index — and handing it to ``create_teclaw_bot``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.bot_config_manifest.apply.delivery import MaterialiserPorts
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import EntryFetcher
from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.apply.activation_delegates import (
    PlatformActivation,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_config_manifest.managed_files import (
    ManagedFilesComposeReader,
    ManagedFilesStore,
)
from agentclaw.community.core.bot_config_manifest.managed_files.ports import (
    StoreIdentityPort,
    StoreResourcePort,
    PlatformSkillPackageUpload,
)
from agentclaw.community.core.bot_config_manifest.repository.apply_models import (  # noqa: F401
    BotConfigManifestApplyLockModel,
    BotConfigManifestApplyModel,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_apply_service import (
    BotConfigManifestApplyService,
)
from agentclaw.community.core.bot_management.services.teclaw_provision_service import (
    TeclawProvisionService,
)
from tests.community.core.bot_config_manifest.cli_tools._fakes import (
    FakeCliToolRepo,
)
from agentclaw.community.core.config_compose.services.collector import (
    ConfigComposerInputCollector,
)
from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.config_compose.services.mcporter_composer import McporterComposer
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)
from agentclaw.community.core.service_bot.services.deploy.teclaw_compose_producer import (
    TeclawComposeProducer,
)
from agentclaw.community.kernel.bot_config import StoreRef

from ..apply._fakes import (
    FakeActivationService,
    FakeCredentials,
    FakeGitClient,
    FakeGuardedFetcher,
    FakeManifestContent,
    FakeMcpAuth,
    FakeStartupScriptService,
    build_skill_zip,
    fetched_object,
    real_validator,
)
from ..managed_files._fakes import FakeObjectStorage
from ..managed_files.test_skill_port import FakeSkillRepository, LiveCapabilityReader
from tests.community.core.config_compose.test_collector import _reader_over, _registry_over

_OWNER = "u_owner"
_BOT = "b_first"
_ENTITY = _OWNER
_QC_URL = "https://example.test/skills/quality-check.zip"
_QZ = build_skill_zip("quality-check", extra=[("scripts/run.sh", b"echo ok\n")])
_DOCUMENT = f"""schema_version: 1
manifest:
  identity:
    - type: RULES.md
      content: '# be kind'
  resources:
    - path: kb/faq.md
      content: 'Q: ?'
  skills:
    - name: quality-check
      source: {_QC_URL}
"""
_BASE = "teclaw/dev/bolt_data"
_REF_ROOT = f"staff_{_OWNER}/{_BOT}_manifest/teclaw"
_STORES = {
    "skill-repo": StoreRef(type="oss", bucket="b", base="bolt_shared/skills-repo"),
    "bot-data": StoreRef(type="oss", bucket="b", base=_BASE),
    "skill-center": StoreRef(type="oss", bucket="b", base="bolt_shared/skills-center"),
}
_RECORD = {
    "bot_id": _BOT, "entity_id": _ENTITY, "entity_type": "staff", "owner_id": _OWNER,
    "bot_name": "First", "bot_desc": "d", "status": "PENDING", "binding_id": None,
    "active_engine": "teclaw", "bot_type": "personal", "env": "dev",
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
    def get(self, *, entity_id, bot_id):
        return type("R", (), {"document": _DOCUMENT})()

    def validate(self, *, document, active_engine, bot_type):
        import yaml

        return type("V", (), {"parsed": yaml.safe_load(document)})()

    def capabilities_for_bot(self, bot):
        return self.resolve_capabilities(active_engine=bot.get("active_engine"), bot_type=bot.get("bot_type"))

    def resolve_capabilities(self, *, active_engine, bot_type):
        from agentclaw.community.core.bot_config_manifest.capabilities import resolve_capabilities

        return resolve_capabilities(active_engine=active_engine, bot_type=bot_type, is_teclaw=lambda e: e == "teclaw")


class _InlineQueue:
    def __init__(self) -> None:
        self.service = None

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        self.service.run_apply_task(payload)
        return (None, True)


class _Bots:
    def get_by_id_and_entity(self, bot_id, entity_id):
        return dict(_RECORD)

    def get_by_id_and_owner(self, bot_id, owner_id):
        return dict(_RECORD)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("agentclaw.community.utils.env_utils.get_current_env", lambda: "dev")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return _Db(engine)


def _build(db):
    store = ManagedFilesStore(object_storage=FakeObjectStorage(), store_base=lambda: _BASE)
    skills = FakeSkillRepository()
    activation = FakeActivationService()
    reader_of_active = LiveCapabilityReader(skills, activation)
    scripts = FakeStartupScriptService()
    validator = real_validator()

    def fetcher():
        return EntryFetcher(
            FakeGuardedFetcher(responses={_QC_URL: fetched_object(_QZ, url=_QC_URL, content_type="application/zip")}),
            FakeManifestContent(),
            FakeCredentials(),
        )

    def platform_ports() -> MaterialiserPorts:
        return MaterialiserPorts(
            script_service=scripts,
            activation_service=PlatformActivation(activation),
            mcp_auth_service=FakeMcpAuth(),
            identity_service=StoreIdentityPort(store),
            upload_service=PlatformSkillPackageUpload(store, validator=validator, skill_repository=skills),
            capability_reader=reader_of_active,
            package_validator=validator,
            entry_fetcher=fetcher(),
            resource_service=StoreResourcePort(store),
            cli_tool_service=object(),
        )

    queue = _InlineQueue()
    applies = BotConfigManifestApplyService(
        manifest_service=_Manifests(),
        apply_repository=BotConfigManifestApplyRepository(db),
        lock_repository=BotConfigManifestApplyLockRepository(db),
        script_service_provider=lambda: scripts,
        activation_service_provider=lambda: activation,
        mcp_auth_service_provider=lambda: FakeMcpAuth(),
        identity_service_provider=lambda: None,
        upload_service_provider=lambda: None,
        capability_reader_provider=lambda: reader_of_active,
        package_validator_provider=lambda: validator,
        entry_fetcher_provider=fetcher,
        resource_service_provider=lambda: None,
        cli_tool_service_factory=lambda family: None,
        git_client_provider=lambda: FakeGitClient(),
        task_queue_provider=lambda: queue,
        bot_repository=_Bots(),
        is_teclaw=lambda engine: engine == "teclaw",
        teclaw_platform_managed=True,
        teclaw_platform_ports_provider=platform_ports,
    )
    queue.service = applies

    # ── the compose side: real reader over the same index ────────────────
    reader = ManagedFilesComposeReader(
        store=store, manifest_service_provider=_Manifests, platform_managed=lambda: True
    )
    skill_set_service = MagicMock()
    skill_set_service.get_active_skills.side_effect = lambda **_kw: [
        {"id": row["id"], "name": row["name"], "git_path": row["git_path"]}
        for row in skills.rows.values()
        if row["id"] in activation.installed_skills
    ]
    skill_set_service.collect_bot_active_mcps.return_value = []
    factory = MagicMock()
    factory.create.return_value = skill_set_service
    center_store = MagicMock()
    center_store.verify_version.return_value = True
    collector = ConfigComposerInputCollector(
        skill_set_service_factory=factory,
        mcp_config_service=MagicMock(),
        resource_repository=MagicMock(),
        bot_repo=MagicMock(),
        path_factory=MagicMock(),
        identity_service=MagicMock(),
        overrides_reader=_reader_over(None),
        center_store=center_store,
        cli_tool_repository=FakeCliToolRepo(),
        local_mcp_registry=_registry_over({}),
        managed_files_reader=reader,
    )
    composer = ConfigComposer(mcporter_composer=McporterComposer(), collector=collector, stores=_STORES)
    engine_ext = MagicMock()
    engine_ext.fetch.return_value = {}
    router = MagicMock()
    router.resolve.return_value = TeclawComposeProducer(composer, engine_ext)
    baas = MagicMock()
    baas.create_teclaw_bot.return_value = {"bot_uuid": "BOT-x", "publish_id": 9}
    binding_repo = MagicMock()
    binding_repo.insert_binding.return_value = 77
    provisioner = TeclawProvisionService(
        baas_service=baas,
        deploy_artifact_producer_router=router,
        device_binding_repo=binding_repo,
        task_queue_service=MagicMock(),
        bot_repository=MagicMock(),
        teclaw_template_uuid="teclaw-tpl",
    )
    return applies, provisioner, baas, store


def test_the_first_artifact_carries_the_manifest(world):
    applies, provisioner, baas, store = _build(world)

    # The deferred creation's single phase, against the record.
    accepted = applies.start_apply(
        entity_id=_ENTITY, bot_id=_BOT, bot=dict(_RECORD), owner_id=_OWNER, actor_id=_OWNER,
        trigger=CREATE_PRE_CONTAINER_TRIGGER, phases=frozenset({ApplyPhase.PRE_CONTAINER}),
    )
    report = applies.last_apply(entity_id=_ENTITY, bot_id=_BOT)
    assert report is not None and report.apply_id == accepted.apply_id
    assert report.status is ApplyStatus.SUCCEEDED, report
    assert sorted(c.construct.value for c in report.categories) == ["identity", "resources", "skills"]

    # Then provisioning: the artifact the container is created with.
    result = provisioner.provision(bot=dict(_RECORD), owner_id=_OWNER)
    assert result.binding_id == 77
    artifact: dict[str, Any] = baas.create_teclaw_bot.call_args.kwargs["config_artifact"]

    # The first artifact of a bot that carries a manifest is the platform's
    # for every category (ownership follows the operation, contract §9.2).
    assert artifact["ownership"] == {
        "mcp": "platform", "skills": "platform", "resources": "platform",
        "identity_files": "platform", "cli_tools": "platform",
    }
    assert artifact["identity_files"] == [
        {"name": "RULES.md", "store": "bot-data", "path": f"{_REF_ROOT}/identity/RULES.md"}
    ]
    assert [r["path"] for r in artifact["resources"]] == [
        f"{_REF_ROOT}/workspace/kb/faq.md",
        f"{_REF_ROOT}/workspace/skills-local/quality-check/SKILL.md",
        f"{_REF_ROOT}/workspace/skills-local/quality-check/scripts/run.sh",
    ]
    assert artifact["skills"] == [
        {"name": "quality-check", "scope": "user", "store": "bot-data",
         "path": f"{_REF_ROOT}/workspace/skills-local/quality-check"}
    ]
    # Every ref resolves against the bot-data store the artifact embeds.
    assert artifact["stores"]["bot-data"]["base"] == _BASE
    for ref in [*artifact["identity_files"], *artifact["resources"]]:
        assert store._oss.get_object(f"{_BASE}/{ref['path']}") is not None  # noqa: SLF001
    assert artifact["engine_type"] == "teclaw"
