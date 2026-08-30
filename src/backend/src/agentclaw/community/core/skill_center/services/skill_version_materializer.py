"""Exact SC download -> validation -> Store -> PUBLISHED Ready Gate."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Callable

from injector import inject

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    MaterializingSkillVersion,
    PublishedMaterializedSkillVersion,
    SkillVersionMaterializationError,
    SkillVersionMaterializationRequest,
    SkillVersionMaterializerProtocol,
    SkillVersionScannerProtocol,
    SkillVersionScanResult,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillVersionMaterializationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageValidator,
    ValidatedSkillPackage,
)
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownloadRequest,
    SkillCenterGateway,
)
from agentclaw.community.plugin_api.skill_scanner import SkillScannerPlugin


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("metadata keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump())
    raise ValueError("metadata must be JSON-compatible")


def _dependency(entry: object) -> dict[str, object]:
    value = _json_value(entry)
    if not isinstance(value, dict):
        raise ValueError("MCP dependency must be an object")
    code = value.get("code") or value.get("server_code") or value.get("serverCode")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("MCP dependency requires code")
    normalized: dict[str, object] = {"code": code.strip()}
    for target, alternatives in {
        "name": ("name",),
        "url": ("url",),
        "icon_url": ("icon_url", "iconUrl"),
        "description": ("description",),
    }.items():
        selected = next((value.get(key) for key in alternatives if value.get(key)), None)
        if selected is not None:
            normalized[target] = selected
    return normalized


def _dependencies(*groups: tuple[object, ...]) -> list[dict[str, object]]:
    by_code: dict[str, dict[str, object]] = {}
    for group in groups:
        for raw in group:
            item = _dependency(raw)
            current = by_code.setdefault(str(item["code"]), {"code": item["code"]})
            for key, value in item.items():
                if key != "code" and key not in current:
                    current[key] = value
    result = [by_code[code] for code in sorted(by_code)]
    mcp_dependency_codes(result)
    return result


def _mapping_tuple(values: object, *, field: str) -> tuple[Mapping[str, object], ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"scanner {field} must be a list")
    normalized: list[Mapping[str, object]] = []
    for value in values:
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        if not isinstance(value, Mapping) or any(
            not isinstance(key, str) for key in value
        ):
            raise ValueError(f"scanner {field} entries must be objects")
        normalized.append(dict(value))
    return tuple(normalized)


class SdkSkillVersionScanner:
    """Adapt the configured Scanner SDK to one validated exact package."""

    @inject
    def __init__(self, scanner: SkillScannerPlugin) -> None:
        self._scanner = scanner

    def scan(self, package: ValidatedSkillPackage) -> SkillVersionScanResult:
        try:
            sdk = self._scanner.create_sdk()
            if sdk is None:
                raise RuntimeError("Skill Scanner is unavailable")
            with tempfile.TemporaryDirectory(prefix="skill-version-scan-") as root:
                base = Path(root)
                for relative, content in package.files:
                    target = base.joinpath(*relative.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                skill_path = base / "SKILL.md"
                scan_result = sdk.scan(str(skill_path))
                dependencies = sdk.get_mcp_dependencies(
                    skill_path=str(skill_path),
                    base_dir=str(base),
                    min_confidence=0.8,
                )
                return SkillVersionScanResult(
                    risk_tags=_mapping_tuple(
                        getattr(scan_result, "risk_tags", None),
                        field="risk_tags",
                    ),
                    mcp_dependencies=_mapping_tuple(
                        dependencies,
                        field="mcp_dependencies",
                    ),
                )
        except SkillVersionMaterializationError:
            raise
        except Exception as exc:
            raise SkillVersionMaterializationError(str(exc)) from exc


class SkillVersionMaterializer(SkillVersionMaterializerProtocol):
    """Publish only after every immutable consumer input verifies successfully."""

    def __init__(
        self,
        *,
        versions: SkillVersionMaterializationRepositoryProtocol,
        gateway: SkillCenterGateway,
        http: HttpClient,
        validator: SkillPackageValidator,
        scanner: SkillVersionScannerProtocol,
        store: CanonicalCenterVersionStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._versions = versions
        self._gateway = gateway
        self._http = http
        self._validator = validator
        self._scanner = scanner
        self._store = store
        self._clock = clock

    def materialize(
        self, request: SkillVersionMaterializationRequest
    ) -> PublishedMaterializedSkillVersion:
        try:
            target = self._versions.get_materialization_target(
                env=request.env,
                skill_id=request.skill_id,
                skill_version_id=request.skill_version_id,
            )
            if target is None:
                raise ValueError("exact Skill Version does not exist")
            identity = CanonicalCenterVersionIdentity(
                skill_uuid=target.skill_uuid,
                sc_version_number=target.sc_version_number,
            )
            ref = CanonicalCenterVersionRef(identity)
            if target.sc_skill_id < 1 or target.sc_version_id < 1:
                raise ValueError("exact SC identity is incomplete")
            if target.status == "PUBLISHED":
                if not self._store.verify_version(ref):
                    raise ValueError("PUBLISHED Version has no verified canonical content")
                return self._published_target(target)

            exact = self._gateway.get_exact_download(
                SkillCenterExactDownloadRequest(
                    skill_code=target.skill_code,
                    version_number=target.sc_version_number,
                    scope=request.scope,
                    team_id=request.team_id,
                )
            )
            if (
                exact.skill_code != target.skill_code
                or exact.version_number != target.sc_version_number
                or not exact.download_url
            ):
                raise ValueError("Skill Center returned a different exact Version")
            expected_digest = exact.sha256.lower()
            response = self._http.get(exact.download_url, timeout=30.0)
            response.raise_for_status()
            package_bytes = bytes(response.content)
            if hashlib.sha256(package_bytes).hexdigest() != expected_digest:
                raise ValueError("downloaded package digest does not match SC")

            package = self._validator.validate_zip(package_bytes)
            if package.name != target.name:
                raise ValueError("materialized SKILL.md name changed")
            scan = self._scanner.scan(package)
            dependencies = _dependencies(
                tuple(scan.mcp_dependencies),
                tuple(
                    {
                        "code": item.server_code,
                        "name": item.name,
                        "icon_url": item.icon_url,
                        "description": item.description,
                    }
                    for item in exact.mcp_services
                ),
            )
            risk_tags = [_json_value(item) for item in scan.risk_tags]
            if any(not isinstance(item, dict) for item in risk_tags):
                raise ValueError("scanner risk tags must be objects")
            metadata_json = json.dumps(
                {
                    "config": [_json_value(item) for item in package.config],
                    "mcp_dependencies": dependencies,
                    "risk_tags": sorted(
                        risk_tags,
                        key=lambda item: json.dumps(item, sort_keys=True),
                    ),
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            version = CanonicalCenterVersion.from_files(
                identity, dict(package.files)
            )
            written = self._store.write_version(version)
            if written != ref or not self._store.verify_version(ref):
                raise ValueError("canonical exact Version did not verify")
            return self._versions.publish_materialized(
                env=request.env,
                skill_id=request.skill_id,
                skill_version_id=request.skill_version_id,
                metadata_json=metadata_json,
                description=package.description,
                published_at=self._clock(),
            )
        except SkillVersionMaterializationError:
            raise
        except Exception as exc:
            raise SkillVersionMaterializationError(str(exc)) from exc

    @staticmethod
    def _published_target(
        target: MaterializingSkillVersion,
    ) -> PublishedMaterializedSkillVersion:
        if target.metadata_json is None or target.published_at is None:
            raise ValueError("PUBLISHED Version has incomplete metadata")
        metadata = json.loads(target.metadata_json)
        if not isinstance(metadata, dict):
            raise ValueError("PUBLISHED Version metadata must be an object")
        dependencies = metadata.get("mcp_dependencies")
        if not isinstance(dependencies, list):
            raise ValueError("PUBLISHED Version has incomplete MCP metadata")
        mcp_dependency_codes(dependencies)
        return PublishedMaterializedSkillVersion(
            skill_version_id=target.skill_version_id,
            skill_id=target.skill_id,
            version_ordinal=target.version_ordinal,
            status="PUBLISHED",
            skill_uuid=target.skill_uuid,
            sc_version_number=target.sc_version_number,
            sc_skill_id=target.sc_skill_id,
            sc_version_id=target.sc_version_id,
            name=target.name,
            description=target.description,
            metadata_json=target.metadata_json,
            published_at=target.published_at,
        )


__all__ = ["SdkSkillVersionScanner", "SkillVersionMaterializer"]
