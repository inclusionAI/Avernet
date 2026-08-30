"""Behavior contract for exact Center Version materialization."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

import pytest

from agentclaw.community.core.skill_center.materialization_contract import (
    MaterializingSkillVersion,
    PublishedMaterializedSkillVersion,
    SkillVersionMaterializationError,
    SkillVersionMaterializationRequest,
    SkillVersionScanResult,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SdkSkillVersionScanner,
    SkillVersionMaterializer,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownload,
    SkillCenterReadScope,
)
from agentclaw.community.testing.canonical_center_store import (
    LocalCanonicalCenterVersionStore,
)


def _package(*, name: str = "weather") -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "SKILL.md",
            (
                "---\n"
                f"name: {name}\n"
                "description: Exact weather reports.\n"
                "config: [{name: city, required: true}]\n"
                "---\n# Weather\n"
            ),
        )
        archive.writestr("scripts/fetch.py", "print('weather')\n")
    return stream.getvalue()


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Http:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[str] = []

    def get(self, path: str, **_kwargs) -> _Response:
        self.calls.append(path)
        return _Response(self.content)


class _Gateway:
    def __init__(self, package: bytes) -> None:
        self.package = package
        self.calls = []

    def get_exact_download(self, request):
        self.calls.append(request)
        return SkillCenterExactDownload(
            skill_code=request.skill_code,
            version_number=request.version_number,
            download_url="https://download.example/exact.zip",
            sha256=hashlib.sha256(self.package).hexdigest(),
            mcp_services=(),
        )


class _Scanner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def scan(self, package):
        self.calls += 1
        if self.fail:
            raise RuntimeError("scanner unavailable")
        assert package.name == "weather"
        return SkillVersionScanResult(
            risk_tags=({"name": "network", "level": "LOW"},),
            mcp_dependencies=(
                {"code": "mcp.weather", "name": "Weather MCP"},
            ),
        )


class _Versions:
    def __init__(self, target: MaterializingSkillVersion) -> None:
        self.target = target
        self.published = None

    def get_materialization_target(self, **_kwargs):
        return self.target

    def publish_materialized(self, **kwargs):
        self.published = kwargs
        return PublishedMaterializedSkillVersion(
            skill_version_id=self.target.skill_version_id,
            skill_id=self.target.skill_id,
            version_ordinal=self.target.version_ordinal,
            status="PUBLISHED",
            skill_uuid=self.target.skill_uuid,
            sc_version_number=self.target.sc_version_number,
            name=self.target.name,
            description=kwargs["description"],
            metadata_json=kwargs["metadata_json"],
            published_at=kwargs["published_at"],
        )


def _target() -> MaterializingSkillVersion:
    return MaterializingSkillVersion(
        skill_version_id=101,
        skill_id=10,
        version_ordinal=1,
        status="MATERIALIZING",
        skill_uuid="00000000-0000-4000-8000-000000000010",
        skill_code="public-weather",
        sc_version_number="1.0.0",
        sc_sha256=None,
        name="weather",
        description=None,
        metadata_json=None,
        published_at=None,
    )


def _materializer(*, package: bytes, scanner: _Scanner, versions: _Versions):
    return SkillVersionMaterializer(
        versions=versions,
        gateway=_Gateway(package),
        http=_Http(package),
        validator=SkillPackageValidator(SkillParser()),
        scanner=scanner,
        store=LocalCanonicalCenterVersionStore(),
        clock=lambda: datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )


def test_exact_package_becomes_published_only_after_all_ready_inputs_exist() -> None:
    package = _package()
    versions = _Versions(_target())
    scanner = _Scanner()
    materializer = _materializer(
        package=package, scanner=scanner, versions=versions
    )

    published = materializer.materialize(
        SkillVersionMaterializationRequest(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            scope=SkillCenterReadScope.PUBLIC,
        )
    )

    assert published.status == "PUBLISHED"
    assert published.skill_uuid == "00000000-0000-4000-8000-000000000010"
    assert published.sc_version_number == "1.0.0"
    assert scanner.calls == 1
    assert versions.published is not None
    metadata = json.loads(versions.published["metadata_json"])
    assert metadata == {
        "config": [{"name": "city", "required": True}],
        "mcp_dependencies": [
            {"code": "mcp.weather", "name": "Weather MCP"}
        ],
        "risk_tags": [{"level": "LOW", "name": "network"}],
    }
    assert versions.published["sc_sha256"] == hashlib.sha256(package).hexdigest()


@pytest.mark.parametrize(
    ("package", "scanner"),
    [
        (_package(name="different-name"), _Scanner()),
        (_package(), _Scanner(fail=True)),
    ],
)
def test_validation_or_scanner_failure_never_publishes(
    package: bytes, scanner: _Scanner
) -> None:
    versions = _Versions(_target())
    materializer = _materializer(
        package=package, scanner=scanner, versions=versions
    )

    with pytest.raises(SkillVersionMaterializationError):
        materializer.materialize(
            SkillVersionMaterializationRequest(
                env="pre",
                skill_id=10,
                skill_version_id=101,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )

    assert versions.published is None


def test_sdk_scanner_reads_only_the_validated_exact_package(tmp_path) -> None:
    package = SkillPackageValidator(SkillParser()).validate_zip(_package())

    class _Scan:
        risk_tags = [{"name": "filesystem", "level": "MEDIUM"}]

    class _Sdk:
        def scan(self, skill_path: str):
            assert skill_path.endswith("/SKILL.md")
            assert open(skill_path, "rb").read().startswith(b"---\n")
            return _Scan()

        def get_mcp_dependencies(self, *, skill_path, base_dir, min_confidence):
            assert skill_path.endswith("/SKILL.md")
            assert base_dir == skill_path.removesuffix("/SKILL.md")
            assert min_confidence == 0.8
            return [{"code": "mcp.fs", "name": "FS"}]

    class _Plugin:
        def create_sdk(self):
            return _Sdk()

    result = SdkSkillVersionScanner(_Plugin()).scan(package)

    assert result == SkillVersionScanResult(
        risk_tags=({"name": "filesystem", "level": "MEDIUM"},),
        mcp_dependencies=({"code": "mcp.fs", "name": "FS"},),
    )


def test_sdk_scanner_unavailable_fails_closed() -> None:
    package = SkillPackageValidator(SkillParser()).validate_zip(_package())

    class _Plugin:
        def create_sdk(self):
            return None

    with pytest.raises(SkillVersionMaterializationError, match="unavailable"):
        SdkSkillVersionScanner(_Plugin()).scan(package)
