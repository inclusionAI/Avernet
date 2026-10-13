"""Rule 25 conformance for the V1 Local Skill security Plugin API."""

import hashlib
from threading import Event

import pytest

from agentclaw.community.plugins.local.local_skill_security_provider import (
    UnavailableLocalSkillSecurityProvider,
)
from agentclaw.community.plugin_api.local_skill_security_provider import (
    CONTRACT_VERSION,
    LocalSkillSecurityProviderCapabilities,
    LocalSkillSecurityProviderProtocol,
    SecurityArtifactAccessRequest,
    SecurityRemoteArtifact,
    SecuritySkillInfo,
)
from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Mode
from agentclaw.community.di.local_skill_security_config import (
    LocalSkillSecurityObservationConfig,
    require_observation_capabilities,
)
from agentclaw.community.core.repository.implementations.skill_center.local_skill_security_observation import (
    LocalSkillSecurityObservationRepository,
)
from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    LocalSkillSecurityObservationRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillRepository as SkillRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.local_skill_security_tasks import (
    LocalSkillSecurityScanTaskHandler,
    LocalSkillSecuritySyncTaskHandler,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule
from agentclaw.community.plugin_api.object_storage import (
    ObjectReadResult,
    ObjectReadStatus,
    ObjectStoragePlugin,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.core.skill_center.test_local_skill_security_tasks import (
    _Provider,
)


def test_public_profile_declares_unavailable_capabilities_and_fails_closed(world):
    provider = world.get(LocalSkillSecurityProviderProtocol)
    assert isinstance(provider, LocalSkillSecurityProviderProtocol)
    assert isinstance(provider, UnavailableLocalSkillSecurityProvider)
    assert CONTRACT_VERSION == 1
    assert provider.capabilities() == LocalSkillSecurityProviderCapabilities(
        accepts_unchecked_asset_metadata=False,
        ordered_latest_revision_sync=False,
    )
    assert any(
        entry.cls is UnavailableLocalSkillSecurityProvider and entry.mode is Mode.LOCAL
        for entry in IMPL_REGISTRY
    )
    with pytest.raises(ValueError, match="unchecked asset metadata"):
        require_observation_capabilities(provider)
    request = SecurityArtifactAccessRequest(
        tenant="teamclaw",
        env="pre",
        provider_skill_id="tc:stable",
        observation_id="observation-a",
        version="1.0",
        sha256="abc",
        snapshot_ref="local-security/teamclaw/pre/observation-a.zip",
    )
    artifact = SecurityRemoteArtifact(
        format="ZIP",
        download_url="https://example.invalid/a.zip",
        checksum="sha256:abc",
        hash_alg="SHA-256",
    )
    info = SecuritySkillInfo(
        tenant="teamclaw",
        env="pre",
        provider_skill_id="tc:stable",
        asset_skill_id=9,
        observation_id="observation-a",
        name="skill",
        snapshot_ref=request.snapshot_ref,
        upload_order=1,
        description="frozen",
        version="1.0",
        owner_id="owner",
        creator_id="creator",
        modifier_id="modifier",
        status="ONLINE",
        platform_del_flag=0,
        artifact=artifact,
    )
    for action in (
        lambda: provider.expose_artifact(request),
        lambda: provider.submit_scan(info),
        lambda: provider.get_scan_report(
            tenant="teamclaw", env="pre", check_id="check-a"
        ),
        lambda: provider.sync_skill_info(info),
    ):
        with pytest.raises(RuntimeError, match="not configured"):
            action()


def _wired_observation(world, package: bytes, *, enabled: bool = True):
    provider = _Provider()
    world.injector.binder.bind(LocalSkillSecurityProviderProtocol, to=provider)
    world.injector.binder.bind(
        LocalSkillSecurityObservationConfig,
        to=LocalSkillSecurityObservationConfig(enabled=enabled),
    )
    assert world.get(LocalSkillSecurityProviderProtocol) is provider
    repository = world.get(LocalSkillSecurityObservationRepositoryProtocol)
    assert isinstance(repository, LocalSkillSecurityObservationRepository)
    snapshots = world.get(ObjectStoragePlugin)
    snapshots.read_object.return_value = ObjectReadResult(
        ObjectReadStatus.FOUND, package
    )
    env = get_current_env()
    incarnation_id = "contract-local-asset-incarnation"
    with avernet_tenant_scope("teamclaw"):
        skill = world.get(SkillRepositoryProtocol).create(
            {
                "name": "my-skill",
                "git_path": "local://bot-a/my-skill",
                "user_id": "owner-a",
                "bolt_id": "bot-a",
                "source_type": "upload",
            }
        )
        skill_id = int(skill["id"])
        repository.bind_formal_incarnation_id(
            env=env, bot_id="bot-a", owner_id="owner-a", skill_id=skill_id,
            incarnation_id=incarnation_id,
        )
        order = repository.reserve_handoff(
            env=env, bot_id="bot-a", owner_id="owner-a", skill_id=skill_id,
            incarnation_id=incarnation_id, observation_id="observation-a",
            sha256="sha256:" + hashlib.sha256(package).hexdigest(),
            snapshot_ref="snapshot-a", actor_id="creator-a",
            cancelled=Event(), name="my-skill", description="frozen description",
            creator_id="creator-a",
        )
        repository.ensure_for_asset(
            env=env,
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=skill_id,
            creator_id="creator-a",
            incarnation_id=incarnation_id,
        )
        recorded = repository.record_observation(
            env=env,
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=skill_id,
            observation_id="observation-a",
            name="my-skill",
            description="frozen description",
            sha256="sha256:" + hashlib.sha256(package).hexdigest(),
            snapshot_ref="snapshot-a",
            modifier_id="modifier-a",
            incarnation_id=incarnation_id,
            upload_order=order,
        )
    payload = {
        "avernet_tenant": "teamclaw",
        "env": env,
        "observation_id": recorded.observation_id,
    }
    return provider, repository, snapshots, recorded, payload


def test_scan_and_sync_consumers_use_wired_provider_contract(world):
    package = b"one exact upload"
    provider, repository, snapshots, recorded, payload = _wired_observation(
        world, package
    )
    assert provider.capabilities() == LocalSkillSecurityProviderCapabilities(
        accepts_unchecked_asset_metadata=True,
        ordered_latest_revision_sync=True,
    )
    require_observation_capabilities(provider)
    scan = world.get(LocalSkillSecurityScanTaskHandler)
    sync = world.get(LocalSkillSecuritySyncTaskHandler)

    assert isinstance(scan.handle(payload), Reschedule)
    snapshots.read_object.assert_called_once_with(recorded.snapshot_ref)
    assert provider.exposed[0].snapshot_ref == recorded.snapshot_ref
    assert provider.exposed[0].sha256 == hashlib.sha256(package).hexdigest()
    assert provider.submitted[0].observation_id == recorded.observation_id
    assert provider.submitted[0].snapshot_ref == recorded.snapshot_ref
    assert provider.submitted[0].upload_order == recorded.upload_order
    assert isinstance(sync.handle(payload), Complete)
    assert len(provider.synced) == 1
    assert provider.synced[0].observation_id == recorded.observation_id
    assert provider.synced[0].snapshot_ref == recorded.snapshot_ref
    assert provider.synced[0].upload_order == recorded.upload_order
    with avernet_tenant_scope("teamclaw"):
        identity = repository.get_identity_for_asset(
            env=payload["env"],
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=recorded.skill_id,
            incarnation_id=recorded.incarnation_id,
        )
    assert identity is not None
    assert identity.sync_completed_observation_id == recorded.observation_id


def test_wired_scan_consumer_records_provider_rejection(world):
    provider, repository, _, recorded, payload = _wired_observation(
        world, b"rejected exact upload"
    )
    provider.submit_rejected = True

    assert isinstance(
        world.get(LocalSkillSecurityScanTaskHandler).handle(payload), Fail
    )
    assert len(provider.submitted) == 1
    assert provider.submitted[0].observation_id == recorded.observation_id
    with avernet_tenant_scope("teamclaw"):
        failed = repository.get_by_observation_id(
            env=payload["env"], observation_id=recorded.observation_id
        )
    assert failed is not None
    assert failed.error_code == "PROVIDER_SUBMISSION_REJECTED"


def test_disabled_wired_consumers_leave_pending_observation_for_recovery(world):
    provider, repository, snapshots, recorded, payload = _wired_observation(
        world, b"disabled exact upload", enabled=False
    )

    assert isinstance(
        world.get(LocalSkillSecurityScanTaskHandler).handle(payload), Complete
    )
    assert isinstance(
        world.get(LocalSkillSecuritySyncTaskHandler).handle(payload), Complete
    )
    snapshots.read_object.assert_not_called()
    assert provider.exposed == []
    assert provider.submitted == []
    assert provider.synced == []
    with avernet_tenant_scope("teamclaw"):
        pending = repository.get_by_observation_id(
            env=payload["env"], observation_id=recorded.observation_id
        )
        identity = repository.get_identity_for_asset(
            env=payload["env"],
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=recorded.skill_id,
            incarnation_id=recorded.incarnation_id,
        )
    assert pending is not None and pending.processing_status == "PENDING"
    assert (
        identity is not None
        and identity.sync_target_observation_id == recorded.observation_id
    )
    assert identity.sync_completed_observation_id is None
