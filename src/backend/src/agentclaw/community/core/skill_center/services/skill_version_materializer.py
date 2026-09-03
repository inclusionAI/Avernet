"""Exact SC download -> validation -> Store -> PUBLISHED Ready Gate."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import logging
import time
from urllib.parse import urlsplit

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
from agentclaw.community.core.skill_center.skill_center_gateway_service_protocol import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownloadRequest,
    SkillCenterReadScope,
)


logger = logging.getLogger(__name__)


def _safe_url_parts(url: object) -> tuple[str | None, str | None]:
    """Return only host and path; signatures, query values, and userinfo stay secret."""
    if not isinstance(url, str):
        return None, None
    parsed = urlsplit(url)
    return parsed.hostname or None, parsed.path or None


def _response_metadata(response: object) -> tuple[int | None, str | None]:
    status = getattr(response, "status_code", None)
    status_code = status if isinstance(status, int) else None
    headers = getattr(response, "headers", None)
    content_type = headers.get("content-type") if hasattr(headers, "get") else None
    return status_code, content_type if isinstance(content_type, str) else None


def _duration_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


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
        selected = next(
            (value.get(key) for key in alternatives if value.get(key)), None
        )
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


class SkillVersionMaterializer(SkillVersionMaterializerProtocol):
    """Publish only after every immutable consumer input verifies successfully."""

    def __init__(
        self,
        *,
        versions: SkillVersionMaterializationRepositoryProtocol,
        gateway: SkillCenterGatewayServiceProtocol,
        http: HttpClient,
        validator: SkillPackageValidator,
        store: CanonicalCenterVersionStore,
    ) -> None:
        self._versions = versions
        self._gateway = gateway
        self._http = http
        self._validator = validator
        self._store = store

    def materialize(
        self, request: SkillVersionMaterializationRequest
    ) -> PublishedMaterializedSkillVersion:
        stage = "target_read"
        started_at = time.perf_counter()
        stage_started_at = started_at
        stage_durations_ms: dict[str, int] = {}

        def advance_stage(next_stage: str) -> None:
            nonlocal stage, stage_started_at
            stage_durations_ms[stage] = _duration_ms(stage_started_at)
            stage = next_stage
            stage_started_at = time.perf_counter()

        def finish_stage() -> None:
            stage_durations_ms[stage] = _duration_ms(stage_started_at)

        target: MaterializingSkillVersion | None = None
        download_host: str | None = None
        download_path: str | None = None
        response: object | None = None
        package_bytes: bytes | None = None
        package: ValidatedSkillPackage | None = None
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
                advance_stage("canonical_verify")
                if not self._store.verify_version(ref):
                    raise ValueError(
                        "PUBLISHED Version has no verified canonical content"
                    )
                published = self._published_target(target)
                finish_stage()
                self._log_success(
                    request=request,
                    target=target,
                    duration_ms=_duration_ms(started_at),
                    stage_durations_ms=stage_durations_ms,
                    package_bytes=None,
                    package_file_count=None,
                    already_published=True,
                )
                return published

            advance_stage("exact_download")
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
            download_host, download_path = _safe_url_parts(exact.download_url)
            expected_digest = exact.sha256.lower()
            advance_stage("package_download")
            response = self._http.get(exact.download_url, timeout=30.0)
            response.raise_for_status()
            package_bytes = bytes(response.content)
            advance_stage("digest_verify")
            if hashlib.sha256(package_bytes).hexdigest() != expected_digest:
                raise ValueError("downloaded package digest does not match SC")

            advance_stage("package_validate")
            # SC exact downloads wrap the Skill files in an opaque transport
            # directory (commonly the skillCode/UUID) for both Public and Team
            # reads.  It is not a runtime name and must not participate in the
            # TeamClaw name invariant below.
            package = self._validator.validate_skill_center_exact_zip(package_bytes)
            advance_stage("name_match")
            if (
                request.scope is not SkillCenterReadScope.PUBLIC
                and package.name != target.name
            ):
                raise ValueError("materialized SKILL.md name changed")
            advance_stage("metadata_build")
            dependencies = _dependencies(
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
            metadata_json = json.dumps(
                {
                    "config": [_json_value(item) for item in package.config],
                    "mcp_dependencies": dependencies,
                    "risk_tags": [],
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            version = CanonicalCenterVersion.from_files(identity, dict(package.files))
            advance_stage("canonical_write")
            written = self._store.write_version(version)
            advance_stage("canonical_verify")
            if written != ref or not self._store.verify_version(ref):
                raise ValueError("canonical exact Version did not verify")
            advance_stage("publish")
            published = self._versions.publish_materialized(
                env=request.env,
                skill_id=request.skill_id,
                skill_version_id=request.skill_version_id,
                name=package.name,
                metadata_json=metadata_json,
                description=package.description,
            )
            finish_stage()
            self._log_success(
                request=request,
                target=target,
                duration_ms=_duration_ms(started_at),
                stage_durations_ms=stage_durations_ms,
                package_bytes=len(package_bytes),
                package_file_count=len(package.files),
                already_published=False,
            )
            return published
        except SkillVersionMaterializationError as exc:
            finish_stage()
            self._log_failure(
                request=request,
                target=target,
                stage=exc.stage or stage,
                failure_type=type(exc.__cause__ or exc).__name__,
                download_host=download_host,
                download_path=download_path,
                response=response,
                duration_ms=_duration_ms(started_at),
                stage_durations_ms=stage_durations_ms,
            )
            if exc.stage is not None:
                raise
            raise SkillVersionMaterializationError(str(exc), stage=stage) from exc
        except Exception as exc:
            finish_stage()
            self._log_failure(
                request=request,
                target=target,
                stage=stage,
                failure_type=type(exc).__name__,
                download_host=download_host,
                download_path=download_path,
                response=response,
                duration_ms=_duration_ms(started_at),
                stage_durations_ms=stage_durations_ms,
            )
            raise SkillVersionMaterializationError(str(exc), stage=stage) from exc

    @staticmethod
    def _log_failure(
        *,
        request: SkillVersionMaterializationRequest,
        target: MaterializingSkillVersion | None,
        stage: str,
        failure_type: str,
        download_host: str | None,
        download_path: str | None,
        response: object | None,
        duration_ms: int,
        stage_durations_ms: Mapping[str, int],
    ) -> None:
        """Emit correlation facts without serializing exception text or download URLs."""
        http_status, http_content_type = _response_metadata(response)
        diagnostics = {
            "operation": "skill_version_materialization",
            "stage": stage,
            "env": request.env,
            "scope": request.scope.value,
            "team_id": request.team_id,
            "skill_id": request.skill_id,
            "skill_version_id": request.skill_version_id,
            "skill_uuid": target.skill_uuid if target is not None else None,
            "skill_code": target.skill_code if target is not None else None,
            "sc_version_number": (
                target.sc_version_number if target is not None else None
            ),
            "sc_skill_id": target.sc_skill_id if target is not None else None,
            "sc_version_id": target.sc_version_id if target is not None else None,
            "download_host": download_host,
            "download_path": download_path,
            "http_status": http_status,
            "http_content_type": http_content_type,
            "failure_type": failure_type,
            "duration_ms": duration_ms,
            "stage_durations_ms": json.dumps(stage_durations_ms, sort_keys=True),
        }
        logger.warning(
            "skill_center_materialization_failed "
            "operation=%(operation)s stage=%(stage)s env=%(env)s scope=%(scope)s "
            "team_id=%(team_id)s skill_id=%(skill_id)s "
            "skill_version_id=%(skill_version_id)s skill_uuid=%(skill_uuid)s "
            "skill_code=%(skill_code)s sc_version_number=%(sc_version_number)s "
            "sc_skill_id=%(sc_skill_id)s sc_version_id=%(sc_version_id)s "
            "download_host=%(download_host)s download_path=%(download_path)s "
            "http_status=%(http_status)s http_content_type=%(http_content_type)s "
            "failure_type=%(failure_type)s duration_ms=%(duration_ms)s "
            "stage_durations_ms=%(stage_durations_ms)s",
            diagnostics,
            extra=diagnostics,
        )

    @staticmethod
    def _log_success(
        *,
        request: SkillVersionMaterializationRequest,
        target: MaterializingSkillVersion,
        duration_ms: int,
        stage_durations_ms: Mapping[str, int],
        package_bytes: int | None,
        package_file_count: int | None,
        already_published: bool,
    ) -> None:
        diagnostics = {
            "operation": "skill_version_materialization",
            "env": request.env,
            "scope": request.scope.value,
            "team_id": request.team_id,
            "skill_id": request.skill_id,
            "skill_version_id": request.skill_version_id,
            "skill_uuid": target.skill_uuid,
            "skill_code": target.skill_code,
            "sc_version_number": target.sc_version_number,
            "duration_ms": duration_ms,
            "stage_durations_ms": json.dumps(stage_durations_ms, sort_keys=True),
            "package_bytes": package_bytes,
            "package_file_count": package_file_count,
            "already_published": already_published,
        }
        logger.info(
            "skill_center_materialization_succeeded "
            "operation=%(operation)s env=%(env)s scope=%(scope)s team_id=%(team_id)s "
            "skill_id=%(skill_id)s skill_version_id=%(skill_version_id)s "
            "skill_uuid=%(skill_uuid)s skill_code=%(skill_code)s "
            "sc_version_number=%(sc_version_number)s duration_ms=%(duration_ms)s "
            "stage_durations_ms=%(stage_durations_ms)s package_bytes=%(package_bytes)s "
            "package_file_count=%(package_file_count)s already_published=%(already_published)s",
            diagnostics,
            extra=diagnostics,
        )

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


__all__ = ["SkillVersionMaterializer"]
