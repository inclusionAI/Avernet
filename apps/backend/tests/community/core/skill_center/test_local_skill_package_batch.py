"""Observe package batching at the Local Skill Service API and runtime transport."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from agentclaw.community.core.ports.skill_package_upload_port import PackageMetadata, PackagePublication
from agentclaw.community.core.skill_center.services.local_skill_package_runtime import LocalSkillPackageRuntime
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterHTTPStatusError

from .test_local_skill_upload_service import _Filesystem, _Repo, _service, _zip


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

    def resolve_for_bot(self, _bot_id: str, _owner_id: str):
        return SimpleNamespace(
            provider=self.provider, binding_id=self.binding_id, conn_info={"binding_id": self.binding_id}
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
        self.confirmed.append(name)
        payload = {"skill_name": name, "action": "created"}
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


@pytest.mark.asyncio
async def test_unknown_package_stays_fenced_across_workers_until_binding_changes():
    from tests.community.core.apply_engine.test_capability_full_replace_contract import _Database
    from agentclaw.community.core.repository.implementations.skill_center.skill import SkillRepository
    from agentclaw.community.plugin_api.models import BotModel

    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(BotModel(
            bot_id=WHO["bot_id"], entity_id=WHO["owner_id"], owner_id=WHO["owner_id"],
            entity_type="staff", creator_id=WHO["owner_id"], env="dev",
        ))
    binding = _Binding("teclaw")
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
    published = await worker().upload_local_skill_batch(**WHO, packages=_packages("first"))
    assert published[0].receipt.publication is PackagePublication.PUBLISHED
    assert published[0].receipt.metadata is PackageMetadata.COMMITTED
    assert transport.entered == ["first", "first"]
