"""Observe package batching at the Local Skill Service API and runtime transport."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.ports.skill_package_upload_port import PackageMetadata, PackagePublication
from agentclaw.community.core.skill_center.services.local_skill_package_runtime import LocalSkillPackageRuntime
from agentclaw.community.core.repository.implementations.skill_center.capability_projection import (
    CapabilityProjectionRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import SkillRepository
from agentclaw.community.core.skill_center.runtime_projection_contract import ProjectionScope
from agentclaw.community.plugin_api.models import BotModel
from tests.community.core.apply_engine.test_capability_full_replace_contract import _Database
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterHTTPStatusError

from .test_local_skill_upload_service import (
    _CapturedSecurityObserver, _Filesystem, _Guard, _Repo, _ReplacementRuntime,
    _replacement_service, _service, _zip,
)


WHO = dict(bot_id="bot", owner_id="owner", actor_id="owner")


def _packages(*names: str) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (name, _zip({"SKILL.md": f"---\nname: {name}\ndescription: test\n---\n".encode()}))
        for name in names
    )


class _Binding:
    def __init__(self, provider: str = "arca") -> None:
        self.provider = provider
        self.binding_id = 4
        self.device_id = "device-a"

    def persist(self, db):
        with db.transactional_orm_session() as session:
            bot = session.query(BotModel).filter_by(bot_id="bot", entity_id="owner").one()
            bot.binding_id = self.binding_id
            bot.device_id = self.device_id
            if session.get(EntityDeviceBinding, self.binding_id) is None:
                session.add(EntityDeviceBinding(
                    id=self.binding_id, entity_id="owner", entity_type="staff", device_id=self.device_id,
                    device_provider=self.provider, env="dev", status="ACTIVE", applied_by="owner",
                ))

    def resolve_for_bot(self, _bot_id: str, _owner_id: str):
        return SimpleNamespace(
            provider=self.provider, binding_id=self.binding_id, device_id=self.device_id,
            conn_info={"binding_id": self.binding_id},
        )


class _Transport:
    def __init__(self, *, parallel=True, fail_first=False, unknown_first=False) -> None:
        self.parallel = parallel
        self.fail_first = fail_first
        self.unknown_first = unknown_first
        self.entered = []
        self.confirmed = []
        self.second_started = asyncio.Event()
        self.first_failed = asyncio.Event()
        self.finish_second = asyncio.Event()

    async def invoke(self, _conn_info, method, path):
        assert (method, path) == ("GET", "/api/engine/capabilities")
        supported = ["skills.local_package.apply.v1"]
        if self.parallel:
            supported.append("skills.local_package.parallel_targets.v1")
        return {"success": True, "data": {"supported": supported}}

    async def invoke_multipart(self, _conn_info, path, *, files, data, headers):
        teclaw = path == "/api/v1/file/skill-package"
        assert path in {"/api/skills/local/apply", "/api/v1/file/skill-package"}
        name = data["skills_name" if teclaw else "skill_name"]
        self.entered.append(name)
        if self.fail_first and name == "first":
            if self.parallel and not teclaw:
                await self.second_started.wait()
            self.first_failed.set()
            raise DeviceAdapterHTTPStatusError(400, '{"error":"invalid_package"}')
        if self.unknown_first and name == "first":
            raise TimeoutError()
        if self.fail_first and name == "second":
            self.second_started.set()
            await self.finish_second.wait()
        raw = files["file"][1]
        digest = hashlib.sha256(raw).hexdigest()
        action = "replaced" if name in self.confirmed else "created"
        self.confirmed.append(name)
        payload = {"skill_name": name, "action": action}
        if teclaw:
            return {"success": True, "sha256": digest, **payload}
        return {"success": True, "data": {"content_digest": "sha256:" + digest, **payload}}


@pytest.mark.asyncio
async def test_batch_stops_dispatching_after_first_failure_and_drains_inflight_packages():
    transport = _Transport(fail_first=True)
    repo = _Repo()
    service = _service(
        _Filesystem(), repo=repo, local_skill_parallelism=2,
        package_runtime=LocalSkillPackageRuntime(_Binding(), transport),
        security_observation_enabled=False,
    )

    upload = asyncio.create_task(
        service.upload_local_skill_batch(**WHO, packages=_packages("first", "second", "third"))
    )
    await asyncio.wait_for(transport.first_failed.wait(), timeout=3)
    assert not upload.done()
    transport.finish_second.set()
    result = await asyncio.wait_for(upload, timeout=3)

    assert [(item.receipt.name, item.receipt.publication, item.receipt.metadata) for item in result] == [
        ("first", PackagePublication.FAILED, PackageMetadata.NOT_ATTEMPTED),
        ("second", PackagePublication.PUBLISHED, PackageMetadata.COMMITTED),
        ("third", PackagePublication.NOT_DISPATCHED, PackageMetadata.NOT_ATTEMPTED),
    ]
    assert transport.confirmed == ["second"]
    assert [row["name"] for row in repo.created] == ["second"]


def _bound_database(binding: _Binding | None = None) -> _Database:
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(BotModel(
            bot_id=WHO["bot_id"], entity_id=WHO["owner_id"], owner_id=WHO["owner_id"],
            entity_type="staff", creator_id=WHO["owner_id"], env="dev",
        ))
    (binding or _Binding()).persist(db)
    return db


@pytest.mark.asyncio
async def test_unknown_package_stays_fenced_across_workers_and_rebinding_the_same_device():
    binding = _Binding("teclaw")
    db = _bound_database(binding)
    transport = _Transport(unknown_first=True)

    def worker():
        return _service(
            _Filesystem(), repo=SkillRepository(db), local_skill_parallelism=2,
            package_runtime=LocalSkillPackageRuntime(binding, transport),
            security_observation_enabled=False,
        )

    first = await worker().upload_local_skill_batch(**WHO, packages=_packages("first", "second"))
    assert [item.receipt.publication for item in first] == [
        PackagePublication.UNKNOWN, PackagePublication.NOT_DISPATCHED,
    ]
    transport.unknown_first = False

    blocked = await worker().upload_local_skill_batch(**WHO, packages=_packages("first"))
    assert blocked[0].receipt.publication is PackagePublication.UNKNOWN
    assert transport.entered == ["first"]

    binding.binding_id += 1
    binding.persist(db)
    rebound = await worker().upload_local_skill_batch(**WHO, packages=_packages("first"))
    assert rebound[0].receipt.publication is PackagePublication.UNKNOWN
    assert transport.entered == ["first"]

    binding.binding_id += 1
    binding.device_id = "device-b"
    binding.persist(db)
    published = await worker().upload_local_skill_batch(**WHO, packages=_packages("first"))
    assert published[0].receipt.publication is PackagePublication.PUBLISHED
    assert published[0].receipt.metadata is PackageMetadata.COMMITTED
    assert transport.entered == ["first", "first"]

    binding.binding_id += 1
    binding.device_id = "device-a"
    binding.persist(db)
    returned = await worker().upload_local_skill_batch(**WHO, packages=_packages("first"))
    assert returned[0].receipt.publication is PackagePublication.UNKNOWN
    assert transport.entered == ["first", "first"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "requires_mapping"), [("teclaw", False), ("arca", True)])
async def test_deferred_replacement_keeps_asset_and_only_records_required_mapping(provider, requires_mapping):
    binding = _Binding(provider)
    db = _bound_database(binding)
    skills = SkillRepository(db)
    transport = _Transport()
    runtime = _ReplacementRuntime([])
    service = _replacement_service(
        _Filesystem(), skills, runtime,
        provider=provider, package_runtime=LocalSkillPackageRuntime(binding, transport),
        security_observation_enabled=False,
    )
    first = await service.upload_local_skill_batch(**WHO, packages=_packages("first"))
    skill_id = first[0].upload["skill"]["id"]
    replacement = _zip({
        "SKILL.md": b"---\nname: first\ndescription: replacement\n---\n",
        "data.txt": b"updated bytes",
    })
    result = await service.upload_local_skill_batch(**WHO, packages=(("first", replacement),))

    assert result[0].receipt.metadata is PackageMetadata.COMMITTED
    assert result[0].receipt.content_digest != first[0].receipt.content_digest
    assert result[0].upload["skill"]["id"] == skill_id
    assert result[0].upload["skill"]["description"] == "replacement"
    assert transport.confirmed == ["first", "first"]
    assert runtime.calls == 0
    state = CapabilityProjectionRepository(db).read(bot_id="bot", entity_id="owner")
    if requires_mapping:
        assert state is not None and state.scope.skills
        assert state.retry_pending
    else:
        assert state is None


@pytest.mark.asyncio
async def test_independent_replacement_commits_tracked_mapping_before_unlocked_delivery():
    db = _bound_database()
    skills = SkillRepository(db)
    projections = CapabilityProjectionRepository(db)
    guard = _Guard()
    observer = _CapturedSecurityObserver(guard)

    class ObservedDelivery(_ReplacementRuntime):
        async def project(self, *, scope=None, **kwargs):
            assert not guard._lock.locked()
            assert len(observer.calls) == 2
            pending = projections.read(bot_id="bot", entity_id="owner")
            assert scope == ProjectionScope(skills=True)
            assert pending is not None and pending.scope.skills
            assert pending.version == previous.version + 1
            return await super().project(scope=scope, **kwargs)

    runtime = ObservedDelivery([True])
    transport = _Transport()
    service = _replacement_service(
        _Filesystem(), skills, runtime, guard=guard, security_observer=observer,
        package_runtime=LocalSkillPackageRuntime(_Binding(), transport),
    )
    first = await service.upload_local_skill(**WHO, package=_packages("first")[0][1])
    assert first["operation"] == "created"
    assert projections.read(bot_id="bot", entity_id="owner") is None
    previous = projections.require(bot_id="bot", entity_id="owner", scope=ProjectionScope(skills=True))

    updated = await service.upload_local_skill(**WHO, package=_packages("first")[0][1])

    assert updated["skill"]["id"] == first["skill"]["id"]
    assert updated["runtime_projection"]["status"] == "CONVERGED"
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_acknowledged_publication_is_preserved_when_metadata_fails():
    class MetadataUnavailable(_Repo):
        def create(self, _row):
            raise RuntimeError("metadata database unavailable")

    transport = _Transport()
    service = _service(
        _Filesystem(), repo=MetadataUnavailable(),
        package_runtime=LocalSkillPackageRuntime(_Binding("teclaw"), transport),
        security_observation_enabled=False,
    )

    result = await service.upload_local_skill_batch(**WHO, packages=_packages("first", "second"))

    assert result[0].receipt.publication is PackagePublication.PUBLISHED
    assert result[0].receipt.metadata is PackageMetadata.UNKNOWN
    assert result[1].receipt.publication is PackagePublication.NOT_DISPATCHED
    assert transport.confirmed == ["first"]


@pytest.mark.asyncio
async def test_old_device_publication_cannot_commit_metadata_on_the_replacement():
    binding = _Binding("teclaw")
    db = _bound_database(binding)
    skills = SkillRepository(db)

    class RebindingTransport(_Transport):
        async def invoke_multipart(self, *args, **kwargs):
            result = await super().invoke_multipart(*args, **kwargs)
            # The old Runtime can acknowledge before the next device lookup
            # observes the persisted rebind.
            replacement = _Binding("teclaw")
            replacement.binding_id = 6
            replacement.device_id = "device-b"
            replacement.persist(db)
            return result

    transport = RebindingTransport()
    service = _service(
        _Filesystem(), repo=skills,
        package_runtime=LocalSkillPackageRuntime(binding, transport),
        security_observation_enabled=False,
    )

    result = await service.upload_local_skill_batch(**WHO, packages=_packages("first", "second"))

    assert result[0].receipt.publication is PackagePublication.PUBLISHED
    assert result[0].receipt.metadata is PackageMetadata.FAILED
    assert result[0].upload is None
    assert result[1].receipt.publication is PackagePublication.NOT_DISPATCHED
    assert skills.list_bot_local_by_name(bot_id="bot", name="first") == []
