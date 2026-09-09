from __future__ import annotations

import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
)
from agentclaw.community.core.skill_center.center_content_distribution import (
    CenterContentState,
)
from agentclaw.community.core.skill_center.services.center_content_distribution import (
    CanonicalCenterContentDistribution,
    CenterContentDistributionConfig,
)
from agentclaw.community.plugins.local.oss_storage import MockObjectStoragePlugin
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
)


class _CanonicalStore:
    def __init__(self, version: CanonicalCenterVersion) -> None:
        self.version = version
        self.reads = 0

    def read_version(self, ref):
        assert ref.identity == self.version.identity
        self.reads += 1
        return self.version


def _world():
    identity = CanonicalCenterVersionIdentity(
        skill_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        sc_version_number="17",
    )
    version = CanonicalCenterVersion.from_files(
        identity,
        {"SKILL.md": b"# exact\n", "assets/icon.bin": b"\x00\xff"},
    )
    objects = MockObjectStoragePlugin()
    stored: dict[str, bytes] = {}

    def create(key: str, content: bytes | str):
        from agentclaw.community.plugin_api.object_storage import ObjectCreateResult

        raw = content.encode() if isinstance(content, str) else content
        if key in stored:
            return ObjectCreateResult.ALREADY_EXISTS
        stored[key] = raw
        return ObjectCreateResult.CREATED

    def read(key: str):
        from agentclaw.community.plugin_api.object_storage import (
            ObjectReadResult,
            ObjectReadStatus,
        )

        if key not in stored:
            return ObjectReadResult(ObjectReadStatus.NOT_FOUND)
        return ObjectReadResult(ObjectReadStatus.FOUND, stored[key])

    objects.create_object_if_absent.side_effect = create
    objects.read_object.side_effect = read
    canonical = _CanonicalStore(version)
    service = CanonicalCenterContentDistribution(
        canonical_store=canonical,
        object_storage=objects,
        url_signer=objects,
        config=CenterContentDistributionConfig(env="pre"),
        now=lambda: datetime(2026, 9, 9, 1, 2, 3, tzinfo=UTC),
    )
    return identity, version, objects, stored, canonical, service


def test_lookup_is_light_and_prepare_publishes_deterministic_exact_zip() -> None:
    identity, version, objects, stored, canonical, service = _world()

    pending = service.lookup(identity)
    assert pending.state is CenterContentState.PENDING
    assert canonical.reads == 0
    objects.sign_url.assert_not_called()

    ready = service.prepare(identity)
    assert ready.state is CenterContentState.READY
    assert ready.package_size is not None
    assert ready.package_sha256 is not None
    assert ready.expires_at == "2026-09-09T02:02:03Z"
    assert canonical.reads == 1

    package_key = objects.sign_url.call_args.args[0]
    package = stored[package_key]
    assert hashlib.sha256(package).hexdigest() == ready.package_sha256
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert archive.namelist() == ["SKILL.md", "assets/icon.bin"]
        assert archive.read("SKILL.md") == version.skill_md
        assert archive.read("assets/icon.bin") == b"\x00\xff"

    descriptor_key = next(key for key in stored if key.endswith("ready.json"))
    descriptor = json.loads(stored[descriptor_key])
    assert descriptor == {
        "format_version": 1,
        "package_key": package_key,
        "package_sha256": ready.package_sha256,
        "package_size": ready.package_size,
        "sc_version_number": "17",
        "skill_uuid": identity.skill_uuid,
    }

    repeated = service.prepare(identity)
    assert repeated.package_sha256 == ready.package_sha256
    assert canonical.reads == 1
    assert service.lookup(identity).package_sha256 == ready.package_sha256


def test_concurrent_prepare_reuses_one_canonical_read_and_package() -> None:
    identity, _version, _objects, _stored, canonical, service = _world()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: service.prepare(identity), range(4)))

    assert canonical.reads == 1
    assert len({result.package_sha256 for result in results}) == 1
    assert all(result.state is CenterContentState.READY for result in results)


def test_prepare_keeps_object_create_failure_retryable() -> None:
    identity, _version, objects, _stored, _canonical, service = _world()
    objects.create_object_if_absent.side_effect = None
    objects.create_object_if_absent.return_value = ObjectCreateResult.FAILED

    result = service.prepare(identity)

    assert result.state is CenterContentState.UNAVAILABLE
    assert result.code == "CENTER_CONTENT_PACKAGE_WRITE_FAILED"
    assert result.retryable is True


def test_prepare_keeps_object_readback_failure_retryable() -> None:
    identity, _version, objects, stored, _canonical, service = _world()
    reads = 0

    def read_with_failed_package_readback(key: str):
        nonlocal reads
        reads += 1
        if reads == 1:
            return ObjectReadResult(ObjectReadStatus.NOT_FOUND)
        if key in stored:
            return ObjectReadResult(ObjectReadStatus.FAILED)
        return ObjectReadResult(ObjectReadStatus.NOT_FOUND)

    objects.read_object.side_effect = read_with_failed_package_readback

    result = service.prepare(identity)

    assert result.state is CenterContentState.UNAVAILABLE
    assert result.code == "CENTER_CONTENT_PACKAGE_WRITE_FAILED"
    assert result.retryable is True
