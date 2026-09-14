from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .. import logger
from ..models import SessionRow
from .sessions import parse_jsonl_file


EXPORT_API_VERSION = "session-export/v1"
SOURCE_SCHEMA = "clawevolve.session-source.v1"
EXPORT_MANIFEST_SCHEMA = "session-package/v1"
HTTP_ATTEMPTS = 3
HTTP_RETRY_SECONDS = 3.0
POLL_SECONDS = 5.0
EXPORT_WAIT_TIMEOUT_SECONDS = 30 * 60


class ServiceSessionExportError(RuntimeError):
    """Raised when the frozen service-session source cannot be acquired."""


@dataclass(frozen=True)
class ServiceSessionAcquisition:
    rows: list[SessionRow]
    source_bot_id: str
    source_metadata: dict[str, Any]


def acquire_exported_sessions(
    *,
    clawweb_url: str,
    task_id: str,
    step_id: str,
    source_user_id: str,
    source_bot_id: str,
    download_network: str,
    input_dir: Path,
    max_sessions: int,
    since: str = "",
    until: str = "",
    timeout_seconds: int = EXPORT_WAIT_TIMEOUT_SECONDS,
    parse_content: bool = True,
) -> ServiceSessionAcquisition:
    """Acquire service-Bot sessions through ClawWeb's public export API."""

    if not task_id or not step_id:
        raise ServiceSessionExportError("service session export requires task_id and step_id")
    base_url = str(clawweb_url or "").rstrip("/")
    if not base_url:
        raise ServiceSessionExportError("service session export requires clawweb_url")
    source_user_id = str(source_user_id or "").strip()
    source_bot_id = str(source_bot_id or "").strip()
    if not source_user_id or not source_bot_id:
        raise ServiceSessionExportError(
            "service session export requires source_user_id and source_bot_id"
        )
    if download_network not in {"office", "production"}:
        raise ServiceSessionExportError("invalid service session download network")
    source_dir = input_dir / "session-source"
    if source_dir.exists():
        shutil.rmtree(source_dir)
    raw_sessions_dir = source_dir / "raw-sessions"
    sessions_dir = source_dir / "sessions"
    raw_sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    collection_endpoint = f"{base_url}/api/integrations/v1/session-exports"
    # Re-running the same handler Step is idempotent. A user-triggered Continue
    # creates a new Step and therefore a fresh export, so a terminal failed
    # export cannot permanently poison the parent Diagnose task.
    idempotency_key = (
        f"clawevolve-diagnose:{task_id}:{step_id}:service-session-export:v1"
    )
    request_body = {
        "exportScope": "bot",
        "target": {
            "userId": source_user_id,
            "botId": source_bot_id,
            "stage": "service",
            "engineType": "openclaw",
        },
        "requestName": f"{task_id} service session diagnose input",
    }
    logger.info(
        "service session export ensure start",
        task_id=task_id,
        step_id=step_id,
        source_user_id=source_user_id,
        source_bot_id=source_bot_id,
        download_network=download_network,
    )
    created = _request_json(
        collection_endpoint,
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    _validate_export_response(created)
    export_id = str(created.get("exportId") or "").strip()
    if not export_id:
        raise ServiceSessionExportError("SESSION_EXPORT_INVALID_RESPONSE: missing exportId")
    status_endpoint = (
        f"{collection_endpoint}/{urllib.parse.quote(export_id, safe='')}"
        f"?downloadNetwork={urllib.parse.quote(download_network, safe='')}"
    )
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    # POST is only the idempotent creation acknowledgement. In particular, a
    # repeated POST for an already-terminal export returns a compact response
    # without target/artifact/error. Always read the canonical detail resource
    # before interpreting state so same-Step re-entry can resume safely.
    result = _request_json(status_endpoint, method="GET")
    _validate_export_response(result)
    while str(result.get("status") or "").lower() in {
        "pending",
        "dispatched",
        "running",
    }:
        if time.monotonic() >= deadline:
            raise ServiceSessionExportError("SESSION_EXPORT_TIMEOUT")
        time.sleep(POLL_SECONDS)
        result = _request_json(status_endpoint, method="GET")
        _validate_export_response(result)

    target = result.get("target") if isinstance(result.get("target"), dict) else {}
    if (
        str(target.get("userId") or "") != source_user_id
        or str(target.get("botId") or "") != source_bot_id
        or str(target.get("stage") or "") != "service"
    ):
        raise ServiceSessionExportError("SESSION_EXPORT_SOURCE_MISMATCH")
    safe_result = _without_download_url(result)
    source_payload = {
        "schemaVersion": SOURCE_SCHEMA,
        "sourceMode": "service_export",
        "taskId": task_id,
        "stepId": step_id,
        "source": {
            "userId": source_user_id,
            "botId": source_bot_id,
            "stage": "service",
        },
        "downloadNetwork": download_network,
        "idempotencyKey": idempotency_key,
        "request": request_body,
        "export": safe_result,
    }
    _write_json(source_dir / "source.json", source_payload)
    status = str(result.get("status") or "").lower()
    if status != "succeeded":
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        code = str(error.get("code") or "SESSION_EXPORT_FAILED")
        message = str(error.get("message") or "service session export failed")
        if code == "SESSION_FILE_NOT_FOUND":
            acquisition_manifest = {
                "schemaVersion": "clawevolve.session-acquisition.v1",
                "sourceMode": "service_export",
                "maxSessions": max(1, int(max_sessions)),
                "since": since,
                "until": until,
                "selectedCount": 0,
                "entries": [],
                "warning": code,
            }
            _write_json(source_dir / "acquisition-manifest.json", acquisition_manifest)
            logger.warning(
                "service session export returned no session files",
                task_id=task_id,
                step_id=step_id,
                source_bot_id=source_bot_id,
            )
            return ServiceSessionAcquisition(
                rows=[],
                source_bot_id=source_bot_id,
                source_metadata={
                    "source": "clawweb_session_export",
                    "source_mode": "service_export",
                    "export_id": export_id,
                    "warning": code,
                    "artifacts": {
                        "source": str(source_dir / "source.json"),
                        "acquisition_manifest": str(source_dir / "acquisition-manifest.json"),
                        "raw_sessions_dir": str(raw_sessions_dir),
                        "sessions_dir": str(sessions_dir),
                    },
                },
            )
        raise ServiceSessionExportError(f"{code}: {message}")
    artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else {}
    download_url = str(artifact.get("downloadUrl") or "")
    expected_sha = str(artifact.get("sha256") or "").lower()
    if not download_url or not expected_sha:
        raise ServiceSessionExportError("SESSION_EXPORT_INVALID_ARTIFACT: missing downloadUrl or sha256")

    with tempfile.TemporaryDirectory(
        prefix=f"clawevolve-session-export-{_safe_name(task_id)}-"
    ) as tmp_text:
        tmp = Path(tmp_text)
        archive = tmp / "source.tar.gz"
        extracted = tmp / "extracted"
        extracted.mkdir()
        _download_artifact(
            endpoint=status_endpoint,
            initial_url=download_url,
            destination=archive,
            expected_sha256=expected_sha,
            expected_size=_optional_int(artifact.get("size")),
        )
        manifest = _safe_extract_and_validate(archive, extracted)
        _write_json(source_dir / "export-manifest.json", manifest)
        _persist_raw_sessions(
            manifest=manifest,
            extracted=extracted,
            raw_sessions_dir=raw_sessions_dir,
        )
        acquisition = _select_and_persist_sessions(
            manifest=manifest,
            extracted=extracted,
            sessions_dir=sessions_dir,
            raw_sessions_dir=raw_sessions_dir,
            max_sessions=max(1, int(max_sessions)),
            since=since,
            until=until,
            parse_content=parse_content,
        )
    _write_json(source_dir / "acquisition-manifest.json", acquisition[1])
    logger.info(
        "service session acquisition done",
        task_id=task_id,
        step_id=step_id,
        export_id=export_id,
        exported_files=(result.get("resolution") or {}).get("fileCount"),
        selected_sessions=len(acquisition[0]),
        max_sessions=max_sessions,
    )
    return ServiceSessionAcquisition(
        rows=acquisition[0],
        source_bot_id=source_bot_id,
        source_metadata={
            "source": "clawweb_session_export",
            "source_mode": "service_export",
            "export_id": export_id,
            "artifacts": {
                "source": str(source_dir / "source.json"),
                "manifest": str(source_dir / "export-manifest.json"),
                "acquisition_manifest": str(source_dir / "acquisition-manifest.json"),
                "raw_sessions_dir": str(raw_sessions_dir),
                "sessions_dir": str(sessions_dir),
            },
        },
    )


def _request_json(
    url: str,
    *,
    method: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error = ""
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with opener.open(request, timeout=120) as response:  # noqa: S310 - frozen internal endpoint.
                parsed = json.loads(response.read().decode("utf-8", errors="replace"))
                if not isinstance(parsed, dict):
                    raise ServiceSessionExportError("session export API returned non-object JSON")
                return parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {raw[:500]}"
            if exc.code < 500 and exc.code not in {408, 429}:
                break
        except Exception as exc:  # noqa: BLE001 - bounded retry at network boundary.
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < HTTP_ATTEMPTS:
            time.sleep(HTTP_RETRY_SECONDS)
    raise ServiceSessionExportError(f"SESSION_EXPORT_API_FAILED: {last_error}")


def _download_artifact(
    *,
    endpoint: str,
    initial_url: str,
    destination: Path,
    expected_sha256: str,
    expected_size: int | None,
) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = initial_url
    last_error = ""
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(url, method="GET")
            with opener.open(request, timeout=300) as response:  # noqa: S310 - signed OSS URL.
                digest = hashlib.sha256()
                size = 0
                with destination.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            if expected_size is not None and size != expected_size:
                raise ServiceSessionExportError(
                    f"SESSION_EXPORT_SIZE_MISMATCH: expected={expected_size} actual={size}"
                )
            actual_sha = digest.hexdigest()
            if actual_sha != expected_sha256:
                raise ServiceSessionExportError(
                    f"SESSION_EXPORT_SHA256_MISMATCH: expected={expected_sha256} actual={actual_sha}"
                )
            return
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code in {401, 403} and attempt < HTTP_ATTEMPTS:
                refreshed = _request_json(endpoint, method="GET")
                refreshed_artifact = refreshed.get("artifact") if isinstance(refreshed.get("artifact"), dict) else {}
                url = str(refreshed_artifact.get("downloadUrl") or url)
            elif exc.code < 500 and exc.code not in {408, 429}:
                break
        except Exception as exc:  # noqa: BLE001 - bounded retry at OSS boundary.
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < HTTP_ATTEMPTS:
            time.sleep(HTTP_RETRY_SECONDS)
    raise ServiceSessionExportError(f"SESSION_EXPORT_DOWNLOAD_FAILED: {last_error}")


def _safe_extract_and_validate(archive: Path, destination: Path) -> dict[str, Any]:
    try:
        handle = tarfile.open(archive, mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise ServiceSessionExportError(f"SESSION_EXPORT_INVALID_ARCHIVE: {exc}") from exc
    with handle:
        for member in handle.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\x00" in member.name:
                raise ServiceSessionExportError(f"SESSION_EXPORT_UNSAFE_PATH: {member.name}")
            allowed = member.name == "manifest.json" or (
                path.parts == ("sessions",) and member.isdir()
            ) or (len(path.parts) == 2 and path.parts[0] == "sessions")
            if not allowed or not (member.isfile() or member.isdir()):
                raise ServiceSessionExportError(f"SESSION_EXPORT_UNSAFE_ENTRY: {member.name}")
        handle.extractall(destination)  # noqa: S202 - every member was no-follow validated above.
    manifest_path = destination / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ServiceSessionExportError(f"SESSION_EXPORT_INVALID_MANIFEST: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != EXPORT_MANIFEST_SCHEMA:
        raise ServiceSessionExportError("SESSION_EXPORT_INVALID_MANIFEST_SCHEMA")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ServiceSessionExportError("SESSION_EXPORT_INVALID_MANIFEST_FILES")
    for item in files:
        if not isinstance(item, dict):
            raise ServiceSessionExportError("SESSION_EXPORT_INVALID_MANIFEST_FILE")
        archive_path = str(item.get("archivePath") or "")
        path = PurePosixPath(archive_path)
        if len(path.parts) != 2 or path.parts[0] != "sessions" or ".." in path.parts:
            raise ServiceSessionExportError(f"SESSION_EXPORT_INVALID_FILE_PATH: {archive_path}")
        file_path = destination / Path(*path.parts)
        if not file_path.is_file() or file_path.is_symlink():
            raise ServiceSessionExportError(f"SESSION_EXPORT_MISSING_FILE: {archive_path}")
        expected_size = _optional_int(item.get("size"))
        expected_sha = str(item.get("sha256") or "").lower()
        if expected_size is None or file_path.stat().st_size != expected_size:
            raise ServiceSessionExportError(f"SESSION_EXPORT_FILE_SIZE_MISMATCH: {archive_path}")
        if not expected_sha or _sha256_file(file_path) != expected_sha:
            raise ServiceSessionExportError(f"SESSION_EXPORT_FILE_SHA256_MISMATCH: {archive_path}")
    return manifest


def _select_and_persist_sessions(
    *,
    manifest: dict[str, Any],
    extracted: Path,
    sessions_dir: Path,
    raw_sessions_dir: Path | None = None,
    max_sessions: int,
    since: str,
    until: str,
    parse_content: bool = True,
) -> tuple[list[SessionRow], dict[str, Any]]:
    since_dt = _parse_datetime(since)
    until_dt = _parse_datetime(until)
    audit: list[dict[str, Any]] = []
    grouped: dict[str, list[tuple[dict[str, Any], Path]]] = {}
    for item in manifest.get("files") or []:
        archive_path = str(item.get("archivePath") or "")
        source = extracted / Path(*PurePosixPath(archive_path).parts)
        record = {"archivePath": archive_path, "sessionId": str(item.get("sessionId") or ""), "status": "candidate"}
        if raw_sessions_dir is not None:
            record["rawPath"] = str(raw_sessions_dir / source.name)
        if not _is_main_agent_source(item):
            record.update(status="skipped", reason="non_main_agent")
            audit.append(record)
            continue
        session_id = str(item.get("sessionId") or _session_id_from_filename(source.name)).strip()
        if not session_id:
            record.update(status="skipped", reason="missing_session_id")
            audit.append(record)
            continue
        grouped.setdefault(session_id, []).append((item, source))

    chosen: list[tuple[dict[str, Any], Path]] = []
    for session_id, versions in grouped.items():
        versions.sort(key=lambda pair: _version_rank(pair[0], pair[1]), reverse=True)
        selected_item, selected_path = versions[0]
        chosen.append((selected_item, selected_path))
        for item, path in versions[1:]:
            record = {
                "archivePath": str(item.get("archivePath") or path.name),
                "sessionId": session_id,
                "status": "skipped",
                "reason": "superseded_session_version",
            }
            if raw_sessions_dir is not None:
                record["rawPath"] = str(raw_sessions_dir / path.name)
            audit.append(record)

    parsed: list[tuple[SessionRow, dict[str, Any], Path]] = []
    for item, path in chosen:
        rows = parse_jsonl_file(path, row_limit=1) if parse_content else []
        row = rows[0] if rows else _session_row_from_manifest(item, path)
        dt = _parse_datetime(row.created_at)
        if (since_dt or until_dt) and dt is None:
            reason = "missing_time"
        elif since_dt and dt is not None and dt < since_dt:
            reason = "before_since"
        elif until_dt and dt is not None and dt > until_dt:
            reason = "after_until"
        else:
            parsed.append((row, item, path))
            continue
        record = {"archivePath": str(item.get("archivePath") or ""), "sessionId": row.session_id, "status": "skipped", "reason": reason}
        if raw_sessions_dir is not None:
            record["rawPath"] = str(raw_sessions_dir / path.name)
        audit.append(record)

    parsed.sort(key=lambda value: (_datetime_sort_key(value[0].created_at), value[0].session_id), reverse=True)
    rows: list[SessionRow] = []
    for index, (row, item, path) in enumerate(parsed):
        if index >= max_sessions:
            record = {"archivePath": str(item.get("archivePath") or ""), "sessionId": row.session_id, "status": "skipped", "reason": "max_sessions"}
            if raw_sessions_dir is not None:
                record["rawPath"] = str(raw_sessions_dir / path.name)
            audit.append(record)
            continue
        target = sessions_dir / _safe_session_filename(row.session_id, path.name)
        shutil.copyfile(path, target, follow_symlinks=False)
        rows.append(replace(row, path=str(target)))
        record = {"archivePath": str(item.get("archivePath") or ""), "sessionId": row.session_id, "status": "selected", "persistedPath": str(target), "createdAt": row.created_at}
        if raw_sessions_dir is not None:
            record["rawPath"] = str(raw_sessions_dir / path.name)
        audit.append(record)
    return rows, {
        "schemaVersion": "clawevolve.session-acquisition.v1",
        "sourceMode": "service_export",
        "maxSessions": max_sessions,
        "since": since,
        "until": until,
        "selectedCount": len(rows),
        "contentParsing": "enabled" if parse_content else "disabled",
        "entries": audit,
    }


def _session_row_from_manifest(item: dict[str, Any], path: Path) -> SessionRow:
    file_metadata = _read_session_locator_metadata(path)
    session_id = str(
        item.get("sessionId")
        or file_metadata.get("sessionId")
        or _session_id_from_filename(path.name)
    ).strip()
    created_at = str(
        item.get("createdAt")
        or item.get("sessionStartedAt")
        or item.get("startTime")
        or item.get("timestamp")
        or file_metadata.get("createdAt")
        or ""
    ).strip()
    return SessionRow(
        session_id=session_id,
        path=str(path),
        bot_id=str(item.get("botId") or ""),
        created_at=created_at,
        first_question="",
        user_text="",
        assistant_text="",
        tool_text="",
        raw_text="",
        original_model=str(item.get("model") or file_metadata.get("model") or ""),
        original_model_source=(
            "session_export_manifest.model"
            if item.get("model")
            else "session_file_metadata.model"
            if file_metadata.get("model")
            else ""
        ),
        raw_session={},
    )


def _read_session_locator_metadata(path: Path) -> dict[str, str]:
    """Read only stable header metadata; never extract conversation content."""

    metadata = {"sessionId": "", "createdAt": "", "model": ""}
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for index, line in enumerate(handle):
                if index >= 32:
                    break
                try:
                    value = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(value, dict):
                    continue
                if not metadata["sessionId"]:
                    metadata["sessionId"] = str(
                        value.get("sessionId") or value.get("session_id") or value.get("id") or ""
                    )
                if not metadata["createdAt"]:
                    metadata["createdAt"] = str(
                        value.get("timestamp") or value.get("created_at") or value.get("start_time") or ""
                    )
                if not metadata["model"] and str(value.get("type") or "").lower() == "model_change":
                    metadata["model"] = str(value.get("modelId") or value.get("model") or "")
                if metadata["sessionId"] and metadata["createdAt"] and metadata["model"]:
                    break
    except OSError:
        return metadata
    return metadata


def _persist_raw_sessions(
    *,
    manifest: dict[str, Any],
    extracted: Path,
    raw_sessions_dir: Path,
) -> None:
    """Freeze every verified exported JSONL before any Diagnose filtering.

    `sessions/` remains the effective Judge input. `raw-sessions/` is immutable
    acquisition evidence used to reproduce parser/filter decisions, including
    files that are empty, internal, non-main, out of range, or above the limit.
    """

    raw_sessions_dir.mkdir(parents=True, exist_ok=True)
    for item in manifest.get("files") or []:
        archive_path = str(item.get("archivePath") or "")
        source = extracted / Path(*PurePosixPath(archive_path).parts)
        target = raw_sessions_dir / source.name
        if target.exists():
            raise ServiceSessionExportError(
                f"SESSION_EXPORT_DUPLICATE_RAW_FILE: {source.name}"
            )
        shutil.copyfile(source, target, follow_symlinks=False)


def _validate_export_response(value: dict[str, Any]) -> None:
    if value.get("apiVersion") != EXPORT_API_VERSION:
        raise ServiceSessionExportError("SESSION_EXPORT_INVALID_API_VERSION")
    if value.get("exportScope") != "bot":
        raise ServiceSessionExportError("SESSION_EXPORT_INVALID_SCOPE")


def _is_main_agent_source(item: dict[str, Any]) -> bool:
    aliases = [item.get("sourceAlias")]
    duplicates = item.get("duplicateAliases")
    if isinstance(duplicates, list):
        aliases.extend(duplicates)
    for alias in aliases:
        text = str(alias or "").replace("\\", "/").rstrip("/")
        if re.search(r"(?:^|/)agent/main$", text) or text.startswith("agent:main:"):
            return True
    return False


def _version_rank(item: dict[str, Any], path: Path) -> tuple[int, str]:
    deleted = bool(item.get("isDeletedArchive")) or ".jsonl.deleted." in path.name
    return (0 if deleted else 1, path.name)


def _session_id_from_filename(filename: str) -> str:
    return filename.split(".jsonl", 1)[0]


def _safe_session_filename(session_id: str, source_name: str) -> str:
    suffix = ".jsonl"
    if ".jsonl.deleted." in source_name:
        suffix = source_name[source_name.index(".jsonl") :]
    return f"{_safe_name(session_id)}{suffix}"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return safe[:180] or "session"


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        try:
            number = float(text)
            while number > 10_000_000_000:
                number /= 1000
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def _datetime_sort_key(value: str) -> float:
    parsed = _parse_datetime(value)
    return parsed.timestamp() if parsed else 0.0


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _without_download_url(result: dict[str, Any]) -> dict[str, Any]:
    safe = json.loads(json.dumps(result, ensure_ascii=False))
    artifact = safe.get("artifact")
    if isinstance(artifact, dict):
        artifact.pop("downloadUrl", None)
        artifact.pop("downloadUrlExpiresAt", None)
    return safe


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
