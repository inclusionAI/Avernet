from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .. import logger
from ..bench.case_contract import validate_contract
from ..bench.template_builder import validate_task_template_artifact
from ..io import load_json
from ..spec.contract import validate_objective_markdown, validate_spec_markdown
from .generation import generation_is_complete
from .inputs import _existing_archived_path
from .invocation import invocation_identity_matches
from .upload import (
    BenchSplitPackage,
    revalidate_cached_bench_domains,
    upload_result_is_complete,
)


def _existing_plan_result(
    output_dir: Path,
    *,
    expected_identity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not generation_is_complete(
        output_dir, expected_identity=expected_identity
    ):
        logger.info(
            "existing plan generation is incomplete or belongs to another invocation",
            output_dir=output_dir,
            expected_fingerprint=(expected_identity or {}).get("fingerprint", ""),
        )
        return None
    objective_md = output_dir / "objective.md"
    objective_json = output_dir / "objective.json"
    spec_md = output_dir / "spec-v0.md"
    spec_json = output_dir / "spec-v0.json"
    zip_path = output_dir / "clawbench_dataset.zip"
    upload_json = output_dir / "clawweb_upload_result.json"
    oss_upload_json = output_dir / "oss_upload_result.json"
    template_dir = output_dir / "templates"
    template_manifest_path = template_dir / "manifest.json"
    root_manifest_path = output_dir / "clawbench_manifest.json"
    case_contracts_path = output_dir / "case_contracts.json"
    case_contract_audit_path = output_dir / "case_contract_audit.json"
    input_manifest_path = output_dir / "input_manifest.json"
    split_audit_path = output_dir / "bench_split.json"

    required_files = (
        objective_md,
        objective_json,
        spec_md,
        spec_json,
        zip_path,
        template_manifest_path,
        root_manifest_path,
        case_contracts_path,
        case_contract_audit_path,
        input_manifest_path,
        split_audit_path,
        upload_json,
    )
    missing = [str(path.name) for path in required_files if not path.is_file()]
    if missing:
        if objective_md.exists() or spec_md.exists():
            logger.warning(
                "existing plan artifacts incomplete; regenerate",
                output_dir=output_dir,
                missing=missing,
            )
        return None

    try:
        objective = load_json(objective_json)
        spec = load_json(spec_json)
        template_manifest = load_json(template_manifest_path)
        root_manifest = load_json(root_manifest_path)
        case_contracts = load_json(case_contracts_path)
        case_contract_audit = load_json(case_contract_audit_path)
        input_manifest = load_json(input_manifest_path)
        split_audit = load_json(split_audit_path)
        upload_result = load_json(upload_json)
        if not all(
            isinstance(value, dict)
            for value in (
                objective,
                spec,
                template_manifest,
                root_manifest,
                case_contracts,
                case_contract_audit,
                input_manifest,
                split_audit,
                upload_result,
            )
        ):
            raise ValueError("required JSON artifacts must contain objects")
        if expected_identity is not None and not invocation_identity_matches(
            input_manifest.get("invocation_identity"), expected_identity
        ):
            raise ValueError("input manifest invocation identity does not match")
        objective_markdown = objective_md.read_text(encoding="utf-8")
        spec_markdown = spec_md.read_text(encoding="utf-8")
        validate_objective_markdown(objective_markdown, objective)
        validate_spec_markdown(spec_markdown, spec)
        _validate_domain_markdown_consistency(
            objective_markdown, objective, document_name="objective.md"
        )
        _validate_domain_markdown_consistency(
            spec_markdown,
            (spec.get("deliverables") or {}),
            document_name="spec-v0.md",
        )
        template_items = template_manifest.get("templates")
        if not isinstance(template_items, list) or not template_items:
            raise ValueError("template manifest contains no templates")
        expected_count = int(template_manifest.get("template_count") or 0)
        if expected_count != len(template_items):
            raise ValueError(
                "template count mismatch between manifest declaration and items: "
                f"declared={expected_count}, items={len(template_items)}"
            )
        expected_entries = _validate_cached_templates(
            template_dir, template_items, zip_path
        )
        if root_manifest.get("template_count") != expected_count:
            raise ValueError("root and template manifests disagree on template_count")
        if root_manifest.get("templates") != template_items:
            raise ValueError(
                "root and template manifests disagree on template metadata"
            )
        contracts = case_contracts.get("contracts")
        if not isinstance(contracts, list) or len(contracts) != expected_count:
            raise ValueError("case contract count does not match template_count")
        _validate_contract_manifest_alignment(contracts, template_items)
        _validate_cached_contract_schemas(contracts)
        _validate_case_contract_audit(
            case_contract_audit,
            contracts,
            allow_fallback=bool(
                (expected_identity or {}).get("skip_clawweb_report", False)
            ),
        )
        if len(expected_entries) != expected_count:
            raise ValueError("validated template count does not match template_count")
        _validate_split_artifacts(
            output_dir, template_dir, root_manifest, split_audit, template_items
        )
    except Exception as exc:  # noqa: BLE001 - invalid cache must be regenerated.
        logger.warning(
            "existing plan artifacts invalid; regenerate",
            output_dir=output_dir,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None

    oss_upload_result: dict[str, Any] = {}
    if oss_upload_json.exists():
        try:
            oss_upload_result = load_json(oss_upload_json)
        except Exception as exc:  # noqa: BLE001 - optional cache is not reusable.
            logger.warning(
                "existing oss upload result unreadable",
                path=oss_upload_json,
                error=f"{type(exc).__name__}: {exc}",
            )
    upload_complete = upload_result_is_complete(upload_result)
    return {
        "status": "already_exists" if upload_complete else "upload_incomplete",
        "ready_for_patch_loop": upload_complete,
        "agent_next_action": "use_existing_plan"
        if upload_complete
        else "retry_clawweb_upload",
        "reason": (
            "task_id already has a complete and validated plan artifact set; "
            "pass --overwrite to regenerate"
        ),
        "objective_md": str(objective_md),
        "objective_json": str(objective_json),
        "spec_md": str(spec_md),
        "spec_json": str(spec_json),
        "output_dir": str(output_dir),
        "input_dir": str(output_dir.parent / "input"),
        "template_dir": str(template_dir),
        "zip_path": str(zip_path),
        "template_count": len(template_items),
        "discovery_notes": _existing_archived_path(output_dir, "discovery_notes")
        or _existing_archived_path(output_dir, "discovery_notes_inline"),
        "input_manifest": str(input_manifest_path),
        "clawweb_upload_result": upload_result,
        "oss_upload_result": oss_upload_result,
        "log_file": logger.log_path(),
    }


def _validate_cached_templates(
    template_dir: Path, template_items: list[Any], aggregate_zip: Path
) -> list[str]:
    """Reject stale or tampered cached templates before they can be re-uploaded."""

    root = template_dir.resolve()
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    content_by_path: dict[str, bytes] = {}
    for index, raw_item in enumerate(template_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"template manifest item {index} is not an object")
        template_id = str(raw_item.get("id") or "").strip()
        relative_path = str(
            raw_item.get("relative_path") or raw_item.get("file") or ""
        ).strip()
        split = str(
            raw_item.get("split") or raw_item.get("case_split") or ""
        ).strip().lower()
        if split in {"train", "training"}:
            expected_prefix = "opt"
        elif split in {"test", "validation", "val"}:
            expected_prefix = "val"
        else:
            expected_prefix = ""
        path = PurePosixPath(relative_path)
        parts = relative_path.split("/")
        if not template_id or template_id in seen_ids:
            raise ValueError(
                "template manifest contains an empty or duplicate id: "
                f"{template_id!r}"
            )
        if (
            not expected_prefix
            or not relative_path
            or "\\" in relative_path
            or path.is_absolute()
            or len(parts) != 2
            or parts[0] != expected_prefix
            or any(part in {"", ".", ".."} for part in parts)
            or path.suffix != ".md"
            or path.stem != template_id
            or relative_path in seen_paths
        ):
            raise ValueError(
                "template manifest identity/path mismatch: "
                f"id={template_id!r}, split={split!r}, path={relative_path!r}"
            )
        candidate = root / relative_path
        if candidate.is_symlink():
            raise ValueError(f"template file is symbolic: {relative_path}")
        source = candidate.resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"template path escapes templates directory: {relative_path}"
            ) from exc
        if not source.is_file():
            raise ValueError(f"template file is missing: {relative_path}")
        content = source.read_bytes()
        try:
            markdown = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"template is not valid UTF-8: {relative_path}") from exc
        validate_task_template_artifact(markdown, template_id)
        declared_digest = str(raw_item.get("content_sha256") or "").strip().lower()
        actual_digest = hashlib.sha256(content).hexdigest()
        if not declared_digest:
            raise ValueError(
                f"template content digest is missing for {relative_path}"
            )
        if declared_digest != actual_digest:
            raise ValueError(
                f"template content digest mismatch for {relative_path}: "
                f"expected={declared_digest}, actual={actual_digest}"
            )
        seen_ids.add(template_id)
        seen_paths.add(relative_path)
        content_by_path[relative_path] = content

    filesystem_paths = sorted(
        str(path.relative_to(template_dir).as_posix())
        for path in template_dir.rglob("*.md")
        if path.is_file()
    )
    expected_paths = sorted(content_by_path)
    if filesystem_paths != expected_paths:
        raise ValueError(
            "template files do not match manifest: "
            f"manifest={expected_paths}, filesystem={filesystem_paths}"
        )

    with zipfile.ZipFile(aggregate_zip) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        archive_paths = [info.filename for info in infos]
        if len(archive_paths) != len(set(archive_paths)):
            raise ValueError("aggregate ZIP contains duplicate entries")
        if sorted(archive_paths) != expected_paths:
            raise ValueError("aggregate ZIP entries do not match template manifest")
        for relative_path, expected_content in content_by_path.items():
            if archive.read(relative_path) != expected_content:
                raise ValueError(
                    "aggregate ZIP content differs from cached template: "
                    f"{relative_path}"
                )
    return expected_paths


def _validate_contract_manifest_alignment(
    contracts: list[Any], template_items: list[Any]
) -> None:
    contract_pairs: set[tuple[str, str]] = set()
    for index, contract in enumerate(contracts, start=1):
        if not isinstance(contract, dict):
            raise ValueError(f"case contract {index} is not an object")
        case_id = str(contract.get("case_id") or "").strip()
        template_id = str(contract.get("template_id") or "").strip()
        if not case_id or not template_id:
            raise ValueError(f"case contract {index} has no case_id/template_id")
        pair = (case_id, template_id)
        if pair in contract_pairs:
            raise ValueError(f"duplicate case contract identity: {pair}")
        contract_pairs.add(pair)

    manifest_pairs: set[tuple[str, str]] = set()
    for index, item in enumerate(template_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"template manifest item {index} is not an object")
        case_id = str(item.get("case_id") or item.get("contract_id") or "").strip()
        template_id = str(item.get("id") or "").strip()
        pair = (case_id, template_id)
        if not case_id or not template_id or pair in manifest_pairs:
            raise ValueError(f"invalid or duplicate manifest contract identity: {pair}")
        manifest_pairs.add(pair)
    if contract_pairs != manifest_pairs:
        raise ValueError(
            "case contracts and template manifest are not one-to-one: "
            f"contracts={sorted(contract_pairs)}, manifest={sorted(manifest_pairs)}"
        )


def _validate_cached_contract_schemas(contracts: list[Any]) -> None:
    for index, contract in enumerate(contracts, start=1):
        if not isinstance(contract, dict):
            raise ValueError(f"case contract {index} is not an object")
        replay = contract.get("replayability")
        query_available = bool(
            isinstance(replay, dict) and replay.get("query_available") is True
        )
        synthetic_case = {
            "case_id": contract.get("case_id"),
            "template_id": contract.get("template_id"),
            "case_type": contract.get("case_type"),
            "split": contract.get("split"),
            "source_session_id": contract.get("source_session_id"),
            "query": "cached-query-present" if query_available else "",
        }
        validate_contract(contract, synthetic_case)


def _validate_case_contract_audit(
    audit: dict[str, Any],
    contracts: list[Any],
    *,
    allow_fallback: bool,
) -> None:
    status = str(audit.get("status") or "").strip().lower()
    allowed_statuses = {"validated", "fallback"} if allow_fallback else {"validated"}
    if status not in allowed_statuses:
        raise ValueError(
            f"case contract audit status is not reusable in this mode: {status!r}"
        )
    if audit.get("schema_version") not in {
        "clawevolve.case-contract.v1",
    }:
        raise ValueError("case contract audit schema_version is invalid")
    if int(audit.get("contract_count") or 0) != len(contracts):
        raise ValueError("case contract audit count does not match contracts")
    batches = audit.get("batches")
    if not isinstance(batches, list) or not batches:
        raise ValueError("case contract audit batches are missing")
    expected_case_ids = [
        str(contract.get("case_id") or "").strip()
        for contract in contracts
        if isinstance(contract, dict)
    ]
    audited_case_ids: list[str] = []
    seen_batches: set[int] = set()
    for index, batch in enumerate(batches, start=1):
        if not isinstance(batch, dict):
            raise ValueError(f"case contract audit batch {index} is not an object")
        try:
            batch_number = int(batch.get("batch"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"case contract audit batch {index} has invalid batch number"
            ) from exc
        if batch_number <= 0 or batch_number in seen_batches:
            raise ValueError(
                f"case contract audit has duplicate/invalid batch number: {batch_number}"
            )
        seen_batches.add(batch_number)
        batch_status = str(batch.get("status") or "").strip().lower()
        if batch_status not in allowed_statuses:
            raise ValueError(
                f"case contract audit batch {batch_number} is not reusable: "
                f"{batch_status!r}"
            )
        case_ids = batch.get("case_ids")
        if not isinstance(case_ids, list) or not case_ids:
            raise ValueError(
                f"case contract audit batch {batch_number} has no case_ids"
            )
        normalized = [str(value or "").strip() for value in case_ids]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError(
                f"case contract audit batch {batch_number} has empty/duplicate case_ids"
            )
        audited_case_ids.extend(normalized)
    if sorted(seen_batches) != list(range(1, len(batches) + 1)):
        raise ValueError("case contract audit batch numbers are not contiguous")
    derived_status = (
        "fallback"
        if any(str(batch.get("status") or "").lower() == "fallback" for batch in batches)
        else "validated"
    )
    if status != derived_status:
        raise ValueError("case contract audit status disagrees with batch statuses")
    if len(audited_case_ids) != len(set(audited_case_ids)):
        raise ValueError("case contract audit assigns a case to multiple batches")
    if sorted(audited_case_ids) != sorted(expected_case_ids):
        raise ValueError("case contract audit batches do not match contracts")


def _validate_split_artifacts(
    output_dir: Path,
    template_dir: Path,
    manifest: dict[str, Any],
    split_audit: dict[str, Any],
    template_items: list[Any],
) -> None:
    summary = split_audit.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("bench split audit summary is missing")
    manifest_counts = {
        split: sum(
            1
            for item in template_items
            if isinstance(item, dict)
            and str(item.get("split") or item.get("case_split") or "").lower()
            == split
        )
        for split in ("train", "test")
    }
    audit_items = split_audit.get("items")
    if not isinstance(audit_items, list) or len(audit_items) != len(template_items):
        raise ValueError("bench split audit item count does not match templates")
    audit_pairs: list[tuple[str, str]] = []
    for index, item in enumerate(audit_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"bench split audit item {index} is not an object")
        pair = (
            str(item.get("caseId") or "").strip(),
            str(item.get("split") or "").strip().lower(),
        )
        if not pair[0] or pair[1] not in {"train", "test"}:
            raise ValueError(f"bench split audit item {index} is invalid: {pair}")
        audit_pairs.append(pair)
    if len(audit_pairs) != len(set(audit_pairs)):
        raise ValueError("bench split audit contains duplicate case/split entries")
    manifest_pairs = [
        (str(item.get("case_id") or "").strip(), str(item.get("split") or "").lower())
        for item in template_items
        if isinstance(item, dict)
    ]
    if len(manifest_pairs) != len(set(manifest_pairs)):
        raise ValueError("template manifest contains duplicate case/split entries")
    if set(audit_pairs) != set(manifest_pairs):
        raise ValueError("bench split audit cases do not match template manifest")
    for split, summary_key in (("train", "trainCount"), ("test", "testCount")):
        count = manifest_counts[split]
        if split == "train" and count <= 0:
            raise ValueError(f"cached plan has no {split} templates")
        if int(summary.get(summary_key) or 0) != count:
            raise ValueError(
                f"bench split {split} count does not match template manifest"
            )
        package = BenchSplitPackage.from_manifest(manifest, split)
        expected_zip = output_dir / f"clawbench_{split}_dataset.zip"
        if package.zip_path.resolve() != expected_zip.resolve():
            raise ValueError(
                f"{split} package path is not the canonical Plan output ZIP"
            )
        error = package.validation_error(allow_empty=split == "test" and count == 0)
        if error:
            raise ValueError(error)
        raw_package = ((manifest.get("split_packages") or {}).get(split) or {})
        if int(raw_package.get("template_count") or 0) != count:
            raise ValueError(
                f"{split} package template_count does not match template manifest"
            )
        if len(package.template_names) != count:
            raise ValueError(
                f"{split} package count does not match template manifest"
            )
        expected_names = sorted(
            str(item.get("id") or "")
            for item in template_items
            if isinstance(item, dict)
            and str(item.get("split") or item.get("case_split") or "").lower()
            == split
        )
        if sorted(package.template_names) != expected_names:
            raise ValueError(
                f"{split} package names do not match template manifest"
            )
        expected_paths = sorted(
            str(item.get("relative_path") or item.get("file") or "")
            for item in template_items
            if isinstance(item, dict)
            and str(item.get("split") or item.get("case_split") or "").lower()
            == split
        )
        declared_paths = raw_package.get("relative_paths")
        if not isinstance(declared_paths, list) or sorted(
            str(value or "") for value in declared_paths
        ) != expected_paths:
            raise ValueError(
                f"{split} package relative_paths do not match template manifest"
            )
        _validate_split_package_content(package, template_dir, template_items)
    aggregate_path = Path(str(manifest.get("aggregate_zip_path") or ""))
    canonical_aggregate = output_dir / "clawbench_dataset.zip"
    if aggregate_path.resolve() != canonical_aggregate.resolve():
        raise ValueError("aggregate ZIP path is not the canonical Plan output ZIP")
    declared_aggregate_digest = str(
        manifest.get("aggregate_zip_sha256") or ""
    ).strip().lower()
    if not declared_aggregate_digest:
        raise ValueError("aggregate ZIP digest is missing")
    actual_aggregate_digest = hashlib.sha256(
        canonical_aggregate.read_bytes()
    ).hexdigest()
    if declared_aggregate_digest != actual_aggregate_digest:
        raise ValueError("aggregate ZIP digest does not match cached artifact")
    if int(summary.get("total") or 0) != len(template_items):
        raise ValueError("bench split total does not match template manifest")


def _validate_split_package_content(
    package: BenchSplitPackage,
    template_dir: Path,
    template_items: list[Any],
) -> None:
    expected: dict[str, bytes] = {}
    for item in template_items:
        if not isinstance(item, dict):
            continue
        split = str(item.get("split") or item.get("case_split") or "").lower()
        if split != package.split:
            continue
        relative_path = str(item.get("relative_path") or item.get("file") or "")
        expected[relative_path] = (template_dir / relative_path).read_bytes()
    with zipfile.ZipFile(package.zip_path) as archive:
        actual = {
            info.filename: archive.read(info)
            for info in archive.infolist()
            if not info.is_dir()
        }
    if actual != expected:
        raise ValueError(
            f"{package.split} package content does not match local templates"
        )


def _validate_domain_markdown_consistency(
    markdown: str, container: dict[str, Any], *, document_name: str
) -> None:
    domain_meta = container.get("clawweb_domains") or container.get(
        "clawweb_domain"
    )
    if not isinstance(domain_meta, dict):
        return
    for split in ("train", "test"):
        domain_id = str(
            ((domain_meta.get("domains") or {}).get(split) or {}).get("domain_id")
            or ""
        ).strip()
        if domain_id and domain_id not in markdown:
            raise ValueError(
                f"{document_name} does not contain current {split} domain id"
            )


def _existing_reusable_clawweb_upload_result(
    output_dir: Path,
    manifest: dict[str, Any],
    *,
    revalidate_remote: bool = False,
) -> dict[str, Any] | None:
    path = output_dir / "clawweb_upload_result.json"
    if not path.exists():
        return None
    try:
        result = load_json(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "existing clawweb upload result unreadable; will reupload",
            path=path,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None
    if not upload_result_is_complete(result):
        logger.info(
            "existing clawweb upload result incomplete; will reupload",
            path=path,
            status=result.get("status"),
        )
        return None
    packages = manifest.get("split_packages") or {}
    for split in ("train", "test"):
        actual = (result.get("domains") or {}).get(split) or {}
        alias = split == "test" and actual.get("alias_of") == "train"
        expected = packages.get("train" if alias else split) or {}
        if str(actual.get("zip_sha256") or "") != str(expected.get("zip_sha256") or ""):
            return None
        if sorted(actual.get("expected_template_names") or []) != sorted(
            expected.get("template_names") or []
        ):
            return None
    if revalidate_remote:
        result = revalidate_cached_bench_domains(result, manifest)
        if result is None:
            logger.info(
                "existing clawweb upload result failed remote revalidation; "
                "will repair",
                path=path,
            )
            return None
    result.setdefault("reused", True)
    result.setdefault("reused_from", str(path))
    return result
