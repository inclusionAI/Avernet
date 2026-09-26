from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..constants import DEFAULT_TIMEOUT_SECONDS, clawweb_base_url
from ..pipeline.paths import validate_step_id, validate_task_id
from .contract import (
    PLAN_SOURCE_DESCRIPTOR_VERSION,
    PLAN_SOURCE_SCHEMA_VERSION,
    PlanSourceError,
    digest_json,
    validate_plan_source,
)


StepInputFetcher = Callable[[str, str], dict[str, Any]]
LEGACY_PLAN_SOURCE_SCHEMA_VERSION = "plan-source/v1"
LEGACY_PLAN_SOURCE_DESCRIPTOR_VERSION = "plan-source-descriptor/v1"


@dataclass(frozen=True)
class PlanSourceResolution:
    status: str
    digest: str
    source: dict[str, Any]
    source_path: Path
    descriptor_path: Path


def fetch_step_input(
    task_id: str,
    step_id: str,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    root = str(base_url or clawweb_base_url()).rstrip("/")
    url = (
        root
        + "/api/evolve/internal/tasks/"
        + urllib.parse.quote(task_id, safe="")
        + "/steps/"
        + urllib.parse.quote(step_id, safe="")
        + "/input"
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(1, 6):
        request = urllib.request.Request(url, method="GET")
        try:
            with opener.open(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                raw = response.read(10 * 1024 * 1024 + 1)
                if len(raw) > 10 * 1024 * 1024:
                    raise PlanSourceError(
                        "PLAN_SOURCE_INPUT_TOO_LARGE",
                        "Step Input 响应超过 10 MiB 安全限制",
                        stage="interface",
                        retryable=False,
                    )
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise PlanSourceError(
                        "PLAN_SOURCE_SCHEMA_INVALID",
                        f"Step Input 不是有效 JSON: {error}",
                        stage="interface",
                        retryable=False,
                    ) from error
        except Exception as error:
            if isinstance(error, PlanSourceError):
                raise
            if isinstance(error, urllib.error.HTTPError):
                raw = error.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}
                retryable = error.code >= 500 or error.code in {408, 409, 425, 429}
                if retryable and attempt < 5:
                    time.sleep(3)
                    continue
                raise PlanSourceError(
                    str(payload.get("code") or "PLAN_SOURCE_INPUT_UNAVAILABLE"),
                    str(payload.get("error") or f"Step Input HTTP {error.code}"),
                    stage=str(payload.get("stage") or "interface"),
                    retryable=bool(payload.get("retryable", retryable)),
                ) from error
            if attempt < 5:
                time.sleep(3)
                continue
            raise PlanSourceError(
                "PLAN_SOURCE_INPUT_UNAVAILABLE",
                f"Step Input 请求失败: {type(error).__name__}: {error}",
                stage="interface",
                retryable=True,
            ) from error
    raise AssertionError("unreachable")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _descriptor(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    if document.get("protocolVersion") != "1.2":
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            f"Step Input protocolVersion 不支持: {document.get('protocolVersion', '')}",
            stage="interface",
        )
    inputs = document.get("inputs")
    descriptor = inputs.get("planSource") if isinstance(inputs, dict) else None
    if not isinstance(descriptor, dict):
        raise PlanSourceError("PLAN_SOURCE_SCHEMA_INVALID", "Step Input 缺少 inputs.planSource", stage="interface")
    if descriptor.get("descriptorVersion") != PLAN_SOURCE_DESCRIPTOR_VERSION:
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            f"descriptorVersion 不支持: {descriptor.get('descriptorVersion', '')}",
            stage="interface",
        )
    if descriptor.get("schemaVersion") != PLAN_SOURCE_SCHEMA_VERSION:
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            f"descriptor schemaVersion 不支持: {descriptor.get('schemaVersion', '')}",
            stage="interface",
        )
    digest = str(descriptor.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise PlanSourceError("PLAN_SOURCE_DIGEST_MISMATCH", "descriptor digest 格式无效", stage="digest_validation")
    delivery = descriptor.get("delivery")
    if not isinstance(delivery, dict) or delivery.get("type") != "inline" or not isinstance(delivery.get("content"), dict):
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            "Resolver 当前要求 inline Plan Source delivery",
            stage="interface",
        )
    source = validate_plan_source(delivery["content"])
    if descriptor.get("sourceType") != source["source"]["type"]:
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            "descriptor sourceType 与 Source source.type 不一致",
            stage="interface",
        )
    actual_digest = digest_json(source)
    if actual_digest != digest:
        raise PlanSourceError(
            "PLAN_SOURCE_DIGEST_MISMATCH",
            f"Plan Source digest 不一致: expected={digest}, actual={actual_digest}",
            stage="digest_validation",
        )
    descriptor_copy = {key: value for key, value in descriptor.items() if key != "delivery"}
    descriptor_copy["delivery"] = {"type": "inline"}
    return source, descriptor_copy, digest


def _local_descriptor(source: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "descriptorVersion": PLAN_SOURCE_DESCRIPTOR_VERSION,
        "sourceType": source["source"]["type"],
        "schemaVersion": PLAN_SOURCE_SCHEMA_VERSION,
        "digest": digest,
        "delivery": {"type": "inline"},
    }


def _archive_legacy_insight_snapshot(
    source_path: Path,
    descriptor_path: Path,
    *,
    allow_network: bool,
) -> bool:
    """Archive a verified v1 derived snapshot before rebuilding from SourceRef.

    The v1 document is an Adapter output, not the frozen fact source. Its
    SourceRef remains in ClawWeb. Plan never interprets v1; it only verifies and
    retains the old snapshot before requesting a canonical v2 replacement.
    """

    if not source_path.is_file() or not descriptor_path.is_file():
        return False
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(source, dict) or not isinstance(descriptor, dict):
        return False
    if (
        source.get("schema_version") != LEGACY_PLAN_SOURCE_SCHEMA_VERSION
        and descriptor.get("descriptorVersion")
        != LEGACY_PLAN_SOURCE_DESCRIPTOR_VERSION
    ):
        return False
    try:
        source_meta = source.get("source")
        if (
            not isinstance(source_meta, dict)
            or source_meta.get("type") != "insight_improvement"
        ):
            raise ValueError("仅 Insight 派生快照支持 v1 到 v2 重建")
        if source.get("schema_version") != LEGACY_PLAN_SOURCE_SCHEMA_VERSION:
            raise ValueError("legacy Source schemaVersion 不一致")
        if (
            descriptor.get("descriptorVersion")
            != LEGACY_PLAN_SOURCE_DESCRIPTOR_VERSION
        ):
            raise ValueError("legacy descriptorVersion 不一致")
        if descriptor.get("schemaVersion") != LEGACY_PLAN_SOURCE_SCHEMA_VERSION:
            raise ValueError("legacy descriptor schemaVersion 不一致")
        if descriptor.get("sourceType") != "insight_improvement":
            raise ValueError("legacy descriptor sourceType 不一致")
        if descriptor.get("delivery") != {"type": "inline"}:
            raise ValueError("legacy descriptor delivery 不一致")
        expected_digest = str(descriptor.get("digest") or "")
        actual_digest = digest_json(source)
        if expected_digest != actual_digest:
            raise ValueError(
                f"legacy digest 不一致: expected={expected_digest}, actual={actual_digest}"
            )
        if not allow_network:
            raise PlanSourceError(
                "PLAN_SOURCE_NETWORK_DISABLED",
                "本地仅有 v1 Insight 快照；需要从冻结 SourceRef 重建 v2，但当前禁止联网",
                stage="resolver",
                retryable=False,
            )
        suffix = actual_digest.removeprefix("sha256:")[:12]
        source_archive = source_path.with_name(
            f"source.plan-source-v1.{suffix}.json"
        )
        descriptor_archive = descriptor_path.with_name(
            f"source-descriptor.plan-source-v1.{suffix}.json"
        )
        if source_archive.exists() or descriptor_archive.exists():
            raise ValueError("legacy snapshot archive already exists; refusing to overwrite")
        os.replace(source_path, source_archive)
        try:
            os.replace(descriptor_path, descriptor_archive)
        except Exception:
            os.replace(source_archive, source_path)
            raise
        return True
    except PlanSourceError:
        raise
    except Exception as error:
        raise PlanSourceError(
            "PLAN_SOURCE_DIGEST_MISMATCH",
            f"旧 v1 Insight 快照无法安全迁移: {type(error).__name__}: {error}",
            stage="digest_validation",
            retryable=False,
        ) from error


def _local_resolution(
    source_path: Path,
    descriptor_path: Path,
) -> PlanSourceResolution | None:
    if not source_path.exists() or not descriptor_path.exists():
        return None
    try:
        source = validate_plan_source(json.loads(source_path.read_text(encoding="utf-8")))
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        if not isinstance(descriptor, dict):
            raise ValueError("source-descriptor.json 必须是对象")
        if descriptor.get("descriptorVersion") != PLAN_SOURCE_DESCRIPTOR_VERSION:
            raise ValueError(f"descriptorVersion 不支持: {descriptor.get('descriptorVersion', '')}")
        if descriptor.get("schemaVersion") != PLAN_SOURCE_SCHEMA_VERSION:
            raise ValueError(f"descriptor schemaVersion 不支持: {descriptor.get('schemaVersion', '')}")
        if descriptor.get("sourceType") != source["source"]["type"]:
            raise ValueError("descriptor sourceType 与 Source source.type 不一致")
        if descriptor.get("delivery") != {"type": "inline"}:
            raise ValueError("本地 descriptor delivery 必须是无正文的 inline 描述")
        expected_digest = str(descriptor.get("digest") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
            raise ValueError("descriptor digest 格式无效")
        actual_digest = digest_json(source)
        if actual_digest != expected_digest:
            raise ValueError(
                f"现有 Plan Source 已变化: expected={expected_digest}, actual={actual_digest}"
            )
    except Exception as error:
        raise PlanSourceError(
            "PLAN_SOURCE_DIGEST_MISMATCH",
            f"现有 Plan Source/descriptor 无法验证，禁止覆盖: {type(error).__name__}: {error}",
            stage="digest_validation",
        ) from error
    return PlanSourceResolution(
        "reused",
        expected_digest,
        source,
        source_path,
        descriptor_path,
    )


def _verify_existing_source(source_path: Path, expected_digest: str) -> dict[str, Any] | None:
    if not source_path.exists():
        return None
    try:
        existing = validate_plan_source(json.loads(source_path.read_text(encoding="utf-8")))
        actual_digest = digest_json(existing)
    except Exception as error:
        raise PlanSourceError(
            "PLAN_SOURCE_DIGEST_MISMATCH",
            f"现有 Plan Source 无法验证，禁止覆盖: {type(error).__name__}: {error}",
            stage="digest_validation",
        ) from error
    if actual_digest != expected_digest:
        raise PlanSourceError(
            "PLAN_SOURCE_DIGEST_MISMATCH",
            f"现有 Plan Source 已变化，禁止覆盖: expected={expected_digest}, actual={actual_digest}",
            stage="digest_validation",
        )
    return existing


def _verify_existing_descriptor(
    descriptor_path: Path,
    expected_descriptor: dict[str, Any],
) -> bool:
    if not descriptor_path.exists():
        return False
    try:
        existing_descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise PlanSourceError(
            "PLAN_SOURCE_DIGEST_MISMATCH",
            f"现有 source-descriptor.json 无法验证，禁止覆盖: {type(error).__name__}: {error}",
            stage="digest_validation",
        ) from error
    if existing_descriptor != expected_descriptor:
        raise PlanSourceError(
            "PLAN_SOURCE_DIGEST_MISMATCH",
            "现有 source-descriptor.json 与冻结 descriptor 不一致",
            stage="digest_validation",
        )
    return True


def resolve_plan_source(
    *,
    task_id: str,
    step_id: str,
    evolve_results_dir: str = "",
    local_source_path: str = "",
    step_input_fetcher: StepInputFetcher | None = None,
    allow_network: bool = True,
) -> PlanSourceResolution:
    safe_task_id = validate_task_id(task_id)
    safe_step_id = validate_step_id(step_id)
    base = Path(evolve_results_dir).expanduser() if str(evolve_results_dir).strip() else Path(
        "/home/admin/.openclaw/workspace/clawevolve_results"
    )
    run_root = (base / safe_task_id).resolve()
    input_dir = (run_root / "plan" / "input").resolve()
    if input_dir != run_root / "plan" / "input" or run_root not in input_dir.parents:
        raise PlanSourceError("PLAN_SOURCE_WRITE_FAILED", "Plan Source 目标目录越界", stage="resolver")
    source_path = input_dir / "source.json"
    descriptor_path = input_dir / "source-descriptor.json"

    producer_path_value = str(local_source_path or "").strip()
    _archive_legacy_insight_snapshot(
        source_path,
        descriptor_path,
        allow_network=allow_network and not producer_path_value,
    )
    local = _local_resolution(source_path, descriptor_path)
    if local is not None:
        if producer_path_value:
            producer_path = Path(producer_path_value).expanduser()
            try:
                producer = validate_plan_source(
                    json.loads(producer_path.read_text(encoding="utf-8"))
                )
            except Exception as error:
                raise PlanSourceError(
                    "PLAN_SOURCE_INPUT_UNAVAILABLE",
                    f"本地 Producer Plan Source 无法验证: {type(error).__name__}: {error}",
                    stage="resolver",
                ) from error
            producer_digest = digest_json(producer)
            if producer_digest != local.digest:
                raise PlanSourceError(
                    "PLAN_SOURCE_DIGEST_MISMATCH",
                    "Diagnose Plan Source 与已冻结快照不一致，禁止静默复用或覆盖: "
                    f"snapshot={local.digest}, producer={producer_digest}",
                    stage="digest_validation",
                )
        return local

    if producer_path_value:
        producer_path = Path(producer_path_value).expanduser()
        if not producer_path.is_file():
            raise PlanSourceError(
                "PLAN_SOURCE_INPUT_UNAVAILABLE",
                f"本地 Producer Plan Source 不存在: {producer_path}",
                stage="resolver",
            )
        try:
            source = validate_plan_source(
                json.loads(producer_path.read_text(encoding="utf-8"))
            )
            expected_digest = digest_json(source)
            descriptor = _local_descriptor(source, expected_digest)
            existing = _verify_existing_source(source_path, expected_digest)
            descriptor_exists = _verify_existing_descriptor(
                descriptor_path, descriptor
            )
            if existing is None:
                _atomic_write_json(source_path, source)
            if not descriptor_exists:
                _atomic_write_json(descriptor_path, descriptor)
        except PlanSourceError:
            raise
        except Exception as error:
            raise PlanSourceError(
                "PLAN_SOURCE_WRITE_FAILED",
                f"本地 Producer Plan Source 解析或落盘失败: {type(error).__name__}: {error}",
                stage="resolver",
                retryable=isinstance(error, OSError),
            ) from error
        return PlanSourceResolution(
            "reused" if existing is not None else "written",
            expected_digest,
            existing or source,
            source_path,
            descriptor_path,
        )

    if not allow_network:
        raise PlanSourceError(
            "PLAN_SOURCE_NETWORK_DISABLED",
            "--skip-clawweb-report 禁止联网，且本地缺少可验证的 Plan Source/descriptor",
            stage="resolver",
            retryable=False,
        )

    if step_input_fetcher is None:
        def step_input_fetcher(requested_task_id: str, requested_step_id: str) -> dict[str, Any]:
            return fetch_step_input(
                requested_task_id,
                requested_step_id,
            )

    document = step_input_fetcher(safe_task_id, safe_step_id)
    source, descriptor, expected_digest = _descriptor(document)
    existing = _verify_existing_source(source_path, expected_digest)
    descriptor_exists = _verify_existing_descriptor(descriptor_path, descriptor)

    try:
        if existing is None:
            _atomic_write_json(source_path, source)
        if not descriptor_exists:
            _atomic_write_json(descriptor_path, descriptor)
    except Exception as error:
        raise PlanSourceError(
            "PLAN_SOURCE_WRITE_FAILED",
            f"Plan Source 原子落盘失败: {type(error).__name__}: {error}",
            stage="resolver",
            retryable=isinstance(error, OSError),
        ) from error
    return PlanSourceResolution(
        "reused" if existing is not None else "written",
        expected_digest,
        existing or source,
        source_path,
        descriptor_path,
    )
