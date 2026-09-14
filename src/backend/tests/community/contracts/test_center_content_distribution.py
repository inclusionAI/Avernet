"""Consumer conformance for the CenterContentDistribution Service API."""

from __future__ import annotations

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.center_content_distribution import (
    CenterContentDistribution,
    CenterContentState,
)
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
    ObjectStoragePlugin,
)


def _identity() -> CanonicalCenterVersionIdentity:
    return CanonicalCenterVersionIdentity(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "17"
    )


def test_prepare_round_trips_through_canonical_and_object_store(world) -> None:
    identity = _identity()
    world.get(CanonicalCenterVersionStore).write_version(
        CanonicalCenterVersion.from_files(identity, {"SKILL.md": b"# exact\n"})
    )
    objects = world.get(ObjectStoragePlugin)
    stored: dict[str, bytes] = {}

    def create(key: str, value: bytes | str):
        content = value.encode() if isinstance(value, str) else value
        if key in stored:
            return ObjectCreateResult.ALREADY_EXISTS
        stored[key] = content
        return ObjectCreateResult.CREATED

    objects.create_object_if_absent.side_effect = create
    objects.read_object.side_effect = lambda key: (
        ObjectReadResult(ObjectReadStatus.FOUND, stored[key])
        if key in stored
        else ObjectReadResult(ObjectReadStatus.NOT_FOUND)
    )

    result = world.get(CenterContentDistribution).prepare(identity)

    assert result.state is CenterContentState.READY
    assert result.package_sha256 is not None
    assert objects.create_object_if_absent.call_count == 2
    objects.sign_url.assert_called_once()


def test_lookup_preserves_object_store_failure_as_retryable_unavailable(world) -> None:
    objects = world.get(ObjectStoragePlugin)
    objects.read_object.return_value = ObjectReadResult(ObjectReadStatus.FAILED)

    result = world.get(CenterContentDistribution).lookup(_identity())

    assert result.state is CenterContentState.UNAVAILABLE
    assert result.code == "CENTER_CONTENT_LOOKUP_FAILED"
    assert result.retryable is True
    objects.sign_url.assert_not_called()
