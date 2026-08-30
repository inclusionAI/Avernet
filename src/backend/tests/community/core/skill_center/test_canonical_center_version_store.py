"""Exact-version canonical Center Store contracts."""

from __future__ import annotations

import inspect

import pytest

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterFile,
    CanonicalCenterStoreConfig,
    CanonicalCenterStoreError,
    CanonicalCenterStoreErrorCode,
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.services.canonical_center_store import (
    OssCanonicalCenterVersionStore,
)
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
)
from agentclaw.community.testing.canonical_center_store import (
    LocalCanonicalCenterVersionStore,
)


SKILL_UUID = "11111111-1111-4111-8111-111111111111"


def _identity(version: str = "12") -> CanonicalCenterVersionIdentity:
    return CanonicalCenterVersionIdentity(
        skill_uuid=SKILL_UUID,
        sc_version_number=version,
    )


def _version(
    *,
    version: str = "12",
    skill_md: bytes = b"---\nname: demo\ndescription: Demo\n---\n",
) -> CanonicalCenterVersion:
    return CanonicalCenterVersion.from_files(
        _identity(version),
        {
            "SKILL.md": skill_md,
            "scripts/run.py": b"print(1)\n",
        },
    )


class _MemoryObjects:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_create_suffix: str | None = None
        self.fail_reads: set[str] = set()
        self.deleted: list[str] = []

    def create_object_if_absent(
        self, key: str, content: bytes | str
    ) -> ObjectCreateResult:
        if self.fail_create_suffix and key.endswith(self.fail_create_suffix):
            return ObjectCreateResult.FAILED
        raw = content.encode() if isinstance(content, str) else content
        if key in self.objects:
            return ObjectCreateResult.ALREADY_EXISTS
        self.objects[key] = raw
        return ObjectCreateResult.CREATED

    def read_object(self, key: str) -> ObjectReadResult:
        if key in self.fail_reads:
            return ObjectReadResult(ObjectReadStatus.FAILED)
        if key not in self.objects:
            return ObjectReadResult(ObjectReadStatus.NOT_FOUND)
        return ObjectReadResult(ObjectReadStatus.FOUND, self.objects[key])

    def delete_object(self, key: str) -> bool:
        self.deleted.append(key)
        self.objects.pop(key, None)
        return True


def _store(objects: _MemoryObjects) -> OssCanonicalCenterVersionStore:
    return OssCanonicalCenterVersionStore(
        object_storage=objects,
        config=CanonicalCenterStoreConfig(),
    )


def test_store_interface_exposes_only_write_read_and_verify() -> None:
    methods = {
        name
        for name, member in inspect.getmembers(
            CanonicalCenterVersionStore, inspect.isfunction
        )
        if not name.startswith("_")
    }

    assert methods == {"write_version", "read_version", "verify_version"}


@pytest.mark.parametrize(
    "version",
    ["latest", "LATEST", "current", "../1", "1/2", " 1", "1 "],
)
def test_identity_rejects_aliases_and_unsafe_version_segments(version: str) -> None:
    with pytest.raises(CanonicalCenterStoreError) as error:
        _identity(version)

    assert error.value.code is CanonicalCenterStoreErrorCode.INVALID_IDENTITY


def test_identity_rejects_noncanonical_uppercase_uuid() -> None:
    with pytest.raises(CanonicalCenterStoreError) as error:
        CanonicalCenterVersionIdentity(
            skill_uuid="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            sc_version_number="1",
        )

    assert error.value.code is CanonicalCenterStoreErrorCode.INVALID_IDENTITY


def test_version_requires_safe_unique_files_and_root_skill_md() -> None:
    for files in (
        {"scripts/run.py": b"pass\n"},
        {"../SKILL.md": b"unsafe"},
        {"skill/SKILL.md": b"wrapped"},
        {"SKILL.md": b"ok", "a/../b": b"unsafe"},
    ):
        with pytest.raises(CanonicalCenterStoreError) as error:
            CanonicalCenterVersion.from_files(_identity(), files)
        assert error.value.code is CanonicalCenterStoreErrorCode.INVALID_FILE_TREE


def test_public_values_cannot_bypass_safe_file_tree_construction() -> None:
    with pytest.raises(CanonicalCenterStoreError) as error:
        CanonicalCenterFile(
            path="../escape",
            content=b"unsafe",
            sha256="0" * 64,
        )
    assert error.value.code is CanonicalCenterStoreErrorCode.INVALID_FILE_TREE

    skill = CanonicalCenterFile(
        path="SKILL.md",
        content=b"valid",
        sha256="ec654fac9599f62e79e2706abef23dfb7c07c08185aa86db4d8695f0b718d1b3",
    )
    with pytest.raises(CanonicalCenterStoreError) as error:
        CanonicalCenterVersion(identity=_identity(), files=(skill, skill))
    assert error.value.code is CanonicalCenterStoreErrorCode.INVALID_FILE_TREE


def test_write_uses_exact_canonical_root_and_roundtrips_verified_tree() -> None:
    objects = _MemoryObjects()
    store = _store(objects)
    version = _version()

    ref = store.write_version(version)

    root = (
        "aidesktop/aidesktop_prod/bolt_shared/skills-center/"
        f"{SKILL_UUID}/12"
    )
    control_root = (
        "aidesktop/aidesktop_prod/bolt_shared/skills-center-control/"
        f"{SKILL_UUID}/12"
    )
    assert ref.locator == f"center-version://{SKILL_UUID}/12"
    assert {
        key for key in objects.objects if key.startswith(f"{root}/")
    } == {
        f"{root}/SKILL.md",
        f"{root}/scripts/run.py",
    }
    assert f"{control_root}/write-intent.json" in objects.objects
    assert f"{control_root}/content-manifest.json" in objects.objects
    assert not any("ready" in key.lower() for key in objects.objects)
    assert store.verify_version(ref) is True
    assert store.read_version(ref) == version
    assert store.read_version(ref).skill_md.startswith(b"---\n")


def test_same_exact_identity_is_idempotent_but_conflict_fails_closed() -> None:
    store = _store(_MemoryObjects())
    original = _version()

    first = store.write_version(original)
    second = store.write_version(original)

    assert first == second
    with pytest.raises(CanonicalCenterStoreError) as error:
        store.write_version(_version(skill_md=b"different"))
    assert error.value.code is CanonicalCenterStoreErrorCode.CONTENT_CONFLICT
    assert store.read_version(first) == original


def test_partial_write_failure_stays_incomplete_and_same_content_retry_resumes() -> None:
    objects = _MemoryObjects()
    objects.fail_create_suffix = "scripts/run.py"
    store = _store(objects)

    with pytest.raises(CanonicalCenterStoreError) as error:
        store.write_version(_version())

    assert error.value.code is CanonicalCenterStoreErrorCode.WRITE_FAILED
    assert not any(key.endswith("content-manifest.json") for key in objects.objects)
    assert any(key.endswith("SKILL.md") for key in objects.objects)

    objects.fail_create_suffix = None
    ref = store.write_version(_version())
    assert store.verify_version(ref) is True


def test_manifest_publish_failure_keeps_verified_tree_for_same_content_retry() -> None:
    objects = _MemoryObjects()
    objects.fail_create_suffix = "content-manifest.json"
    store = _store(objects)

    with pytest.raises(CanonicalCenterStoreError) as error:
        store.write_version(_version())

    assert error.value.code is CanonicalCenterStoreErrorCode.WRITE_FAILED
    assert not any(key.endswith("content-manifest.json") for key in objects.objects)
    objects.fail_create_suffix = None
    ref = store.write_version(_version())
    assert store.verify_version(ref) is True


def test_transient_prepublish_read_failure_never_rolls_back_partial_tree() -> None:
    objects = _MemoryObjects()
    store = _store(objects)
    version = _version()
    skill_key = (
        "aidesktop/aidesktop_prod/bolt_shared/skills-center/"
        f"{SKILL_UUID}/12/SKILL.md"
    )
    objects.fail_reads.add(skill_key)

    with pytest.raises(CanonicalCenterStoreError) as error:
        store.write_version(version)
    assert error.value.code is CanonicalCenterStoreErrorCode.READ_FAILED
    assert not any(key.endswith("content-manifest.json") for key in objects.objects)
    assert skill_key in objects.objects

    objects.fail_reads.clear()
    ref = store.write_version(version)
    assert store.read_version(ref) == version


def test_missing_or_corrupt_canonical_tree_is_unavailable() -> None:
    objects = _MemoryObjects()
    store = _store(objects)
    ref = store.write_version(_version())
    skill_key = next(key for key in objects.objects if key.endswith("/SKILL.md"))
    objects.objects[skill_key] = b"tampered"

    assert store.verify_version(ref) is False
    with pytest.raises(CanonicalCenterStoreError) as error:
        store.read_version(ref)
    assert error.value.code is CanonicalCenterStoreErrorCode.CORRUPT_CONTENT


def test_malformed_integrity_manifest_is_never_treated_as_complete() -> None:
    objects = _MemoryObjects()
    store = _store(objects)
    ref = store.write_version(_version())
    manifest_key = next(
        key for key in objects.objects if key.endswith("/content-manifest.json")
    )
    objects.objects[manifest_key] = b"not-json"

    assert store.verify_version(ref) is False
    with pytest.raises(CanonicalCenterStoreError) as error:
        store.read_version(ref)
    assert error.value.code is CanonicalCenterStoreErrorCode.CORRUPT_CONTENT


def test_storage_failure_is_not_reported_as_missing_or_not_ready() -> None:
    objects = _MemoryObjects()
    store = _store(objects)
    ref = store.write_version(_version())
    manifest_key = next(
        key for key in objects.objects if key.endswith("/content-manifest.json")
    )
    objects.fail_reads.add(manifest_key)

    with pytest.raises(CanonicalCenterStoreError) as error:
        store.verify_version(ref)
    assert error.value.code is CanonicalCenterStoreErrorCode.READ_FAILED


def test_local_fake_matches_immutable_exact_version_semantics() -> None:
    store = LocalCanonicalCenterVersionStore()
    version = _version(version="v1.2.3")

    ref = store.write_version(version)

    assert store.verify_version(ref) is True
    assert store.read_version(ref) == version
    assert store.write_version(version) == ref
    with pytest.raises(CanonicalCenterStoreError) as error:
        store.write_version(
            _version(version="v1.2.3", skill_md=b"conflicting package")
        )
    assert error.value.code is CanonicalCenterStoreErrorCode.CONTENT_CONFLICT
