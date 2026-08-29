"""Contract tests for immutable Draft revision storage."""

from __future__ import annotations

import io
import zipfile
from uuid import UUID

import pytest

from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStore,
    DraftContentStoreConfig,
    DraftContentStoreError,
    DraftContentStoreErrorCode,
    DraftRevisionIdentity,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.services.draft_content_store import (
    OssDraftContentStore,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.testing.draft_content_store import (
    LocalDraftContentStore,
)
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
)


_SKILL_UUID = "11111111-1111-4111-8111-111111111111"
_REVISION_ID = "22222222-2222-4222-8222-222222222222"


def _identity(*, revision_id: str = _REVISION_ID) -> DraftRevisionIdentity:
    return DraftRevisionIdentity(
        tenant="tenant-a",
        env="pre",
        skill_uuid=_SKILL_UUID,
        target_version=2,
        revision_id=revision_id,
    )


def _package(description: str = "Draft content"):
    return SkillPackageValidator(SkillParser()).validate_directory(
        [
            (
                "weather/SKILL.md",
                (f"---\nname: weather\ndescription: {description}\n---\n").encode(),
            ),
            ("weather/script.py", b"pass"),
        ]
    )


def test_draft_revision_ref_round_trips_the_stable_locator() -> None:
    ref = DraftRevisionRef.from_identity(_identity())

    assert ref.locator == (
        "draft://11111111-1111-4111-8111-111111111111/"
        "v2/22222222-2222-4222-8222-222222222222"
    )
    assert (
        DraftRevisionRef.from_locator(tenant="tenant-a", env="pre", locator=ref.locator)
        == ref
    )
    assert UUID(ref.skill_uuid).version == 4
    assert UUID(ref.revision_id).version == 4


@pytest.mark.parametrize(
    "values",
    [
        {"tenant": "../tenant"},
        {"env": "pre/other"},
        {"skill_uuid": "not-a-uuid"},
        {"target_version": 0},
        {"target_version": True},
        {"revision_id": "not-a-uuid"},
    ],
)
def test_draft_revision_identity_rejects_unsafe_or_invalid_values(values) -> None:
    kwargs = {
        "tenant": "tenant-a",
        "env": "pre",
        "skill_uuid": _SKILL_UUID,
        "target_version": 2,
        "revision_id": _REVISION_ID,
        **values,
    }

    with pytest.raises(DraftContentStoreError) as error:
        DraftRevisionIdentity(**kwargs)

    assert error.value.code is DraftContentStoreErrorCode.INVALID_IDENTITY


def test_draft_locator_rejects_latest_current_and_malformed_values() -> None:
    for locator in (
        f"draft://{_SKILL_UUID}/latest/{_REVISION_ID}",
        f"draft://{_SKILL_UUID}/current/{_REVISION_ID}",
        f"draft://{_SKILL_UUID}/v0/{_REVISION_ID}",
        "https://example.test/revision.zip",
    ):
        with pytest.raises(DraftContentStoreError) as error:
            DraftRevisionRef.from_locator(tenant="tenant-a", env="pre", locator=locator)
        assert error.value.code is DraftContentStoreErrorCode.INVALID_LOCATOR


def test_local_fake_implements_immutable_crud_contract() -> None:
    store: DraftContentStore = LocalDraftContentStore()
    identity = _identity()
    package = _package()

    ref = store.write_revision(identity, package)

    assert store.write_revision(identity, package) == ref
    assert store.read_revision(ref) == package
    with pytest.raises(DraftContentStoreError) as conflict:
        store.write_revision(identity, _package("Conflicting content"))
    assert conflict.value.code is DraftContentStoreErrorCode.CONTENT_CONFLICT
    store.delete_revision(ref)
    store.delete_revision(ref)
    with pytest.raises(DraftContentStoreError) as missing:
        store.read_revision(ref)
    assert missing.value.code is DraftContentStoreErrorCode.NOT_FOUND


class _Objects:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.put_ok = True
        self.delete_ok = True
        self.read_override: ObjectReadResult | object = _UNSET
        self.puts: list[tuple[str, bytes]] = []
        self.deletes: list[str] = []

    def put_object(self, key: str, content: bytes) -> bool:
        self.puts.append((key, content))
        if self.put_ok:
            self.data[key] = content
        return self.put_ok

    def create_object_if_absent(self, key: str, content: bytes) -> ObjectCreateResult:
        if not self.put_ok:
            return ObjectCreateResult.FAILED
        if key in self.data:
            return ObjectCreateResult.ALREADY_EXISTS
        self.puts.append((key, content))
        self.data[key] = content
        return ObjectCreateResult.CREATED

    def get_object(self, key: str) -> bytes | None:
        return self.data.get(key)

    def read_object(self, key: str) -> ObjectReadResult:
        if self.read_override is not _UNSET:
            return self.read_override  # type: ignore[return-value]
        if key not in self.data:
            return ObjectReadResult(ObjectReadStatus.NOT_FOUND)
        return ObjectReadResult(ObjectReadStatus.FOUND, self.data[key])

    def delete_object(self, key: str) -> bool:
        self.deletes.append(key)
        if self.delete_ok:
            self.data.pop(key, None)
        return self.delete_ok


_UNSET = object()


def _oss_store(objects: _Objects) -> OssDraftContentStore:
    return OssDraftContentStore(
        object_storage=objects,
        package_validator=SkillPackageValidator(SkillParser()),
        config=DraftContentStoreConfig(),
    )


def test_oss_adapter_writes_reads_and_deletes_one_canonical_zip() -> None:
    objects = _Objects()
    store = _oss_store(objects)
    package = _package()

    ref = store.write_revision(_identity(), package)

    expected_key = (
        "aidesktop/aidesktop_pre/bolt_shared/skills-upload/space-drafts/"
        "tenant-a/pre/11111111-1111-4111-8111-111111111111/v2/revisions/"
        "22222222-2222-4222-8222-222222222222.zip"
    )
    assert objects.puts == [(expected_key, package.canonical_zip)]
    assert store.read_revision(ref) == package
    store.delete_revision(ref)
    assert objects.deletes == [expected_key]


def test_oss_adapter_is_idempotent_for_same_bytes_and_rejects_conflicts() -> None:
    objects = _Objects()
    store = _oss_store(objects)
    identity = _identity()
    package = _package()

    store.write_revision(identity, package)
    store.write_revision(identity, package)

    assert len(objects.puts) == 1
    with pytest.raises(DraftContentStoreError) as conflict:
        store.write_revision(identity, _package("Different"))
    assert conflict.value.code is DraftContentStoreErrorCode.CONTENT_CONFLICT


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("put", DraftContentStoreErrorCode.WRITE_FAILED),
        ("verify", DraftContentStoreErrorCode.WRITE_FAILED),
        ("delete", DraftContentStoreErrorCode.DELETE_FAILED),
    ],
)
def test_oss_adapter_maps_storage_failures_to_stable_errors(
    failure: str, expected_code: DraftContentStoreErrorCode
) -> None:
    objects = _Objects()
    store = _oss_store(objects)
    if failure == "put":
        objects.put_ok = False
    elif failure == "verify":
        objects.read_override = ObjectReadResult(ObjectReadStatus.FAILED)
    else:
        objects.delete_ok = False

    with pytest.raises(DraftContentStoreError) as error:
        if failure == "delete":
            store.delete_revision(DraftRevisionRef.from_identity(_identity()))
        else:
            store.write_revision(_identity(), _package())

    assert error.value.code is expected_code


def test_oss_adapter_fails_closed_on_missing_or_noncanonical_content() -> None:
    objects = _Objects()
    store = _oss_store(objects)
    ref = DraftRevisionRef.from_identity(_identity())

    with pytest.raises(DraftContentStoreError) as missing:
        store.read_revision(ref)
    assert missing.value.code is DraftContentStoreErrorCode.NOT_FOUND

    objects.read_override = ObjectReadResult(ObjectReadStatus.FAILED)
    with pytest.raises(DraftContentStoreError) as unavailable:
        store.read_revision(ref)
    assert unavailable.value.code is DraftContentStoreErrorCode.READ_FAILED
    objects.read_override = _UNSET

    store.write_revision(_identity(), _package())
    key = objects.puts[0][0]
    objects.data[key] = b"not a zip"
    with pytest.raises(DraftContentStoreError) as corrupt:
        store.read_revision(ref)
    assert corrupt.value.code is DraftContentStoreErrorCode.CORRUPT_CONTENT

    package = _package()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, content in reversed(package.files):
            archive.writestr(path, content)
    objects.data[key] = stream.getvalue()
    with pytest.raises(DraftContentStoreError) as noncanonical:
        store.read_revision(ref)
    assert noncanonical.value.code is DraftContentStoreErrorCode.CORRUPT_CONTENT


def test_oss_adapter_rejects_unsafe_base_prefix_configuration() -> None:
    objects = _Objects()
    for template in (
        "/absolute/{env}",
        "../escape/{env}",
        "aidesktop/no-env-token",
        "aidesktop/{tenant}",
        "aidesktop/{env!r}",
        "aidesktop/{env:>10}",
        "aidesktop/{env}/bad space",
        None,
    ):
        with pytest.raises(DraftContentStoreError) as error:
            OssDraftContentStore(
                object_storage=objects,
                package_validator=SkillPackageValidator(SkillParser()),
                config=DraftContentStoreConfig(
                    base_prefix_template=template  # type: ignore[arg-type]
                ),
            )
        assert error.value.code is DraftContentStoreErrorCode.INVALID_CONFIGURATION
