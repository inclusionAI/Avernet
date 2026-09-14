from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import logger
from ..bench.case_contract import build_case_contracts
from ..bench.split import assign_train_test_splits
from ..bench.template_builder import assign_case_template_ids, render_templates
from .common import _write_json
from .existing import _existing_reusable_clawweb_upload_result
from .upload import (
    dual_domain_meta,
    skipped_dual_domain_upload_result,
    upload_bench_domains,
)


def prepare_bench_artifacts(
    args: argparse.Namespace,
    plan: dict[str, Any],
    output_dir: Path,
    *,
    discovery_notes: str = "",
    task_id: str = "",
) -> tuple[Path, Path, list[str], dict[str, Any], dict[str, Any]]:
    """Generate contracts/templates and publish the available Bench domains."""
    split_cases = assign_train_test_splits(list(plan.get("cases") or []))
    contract_cases = assign_case_template_ids(split_cases)
    plan["cases"] = contract_cases
    contract_result = build_case_contracts(
        plan=plan,
        cases=contract_cases,
        goal_text=args.goal,
        user_intent=plan.get("user_intent"),
        discovery_notes=discovery_notes,
        output_dir=output_dir,
        task_id=task_id or args.task_id,
        allow_fallback=bool(getattr(args, "skip_clawweb_report", False)),
    )
    contracts = {
        str(contract.get("case_id")): contract
        for contract in contract_result.get("contracts", [])
    }
    template_dir, zip_path, template_names, manifest = _render_templates(
        plan, output_dir, args.goal, contracts
    )
    artifacts = plan.setdefault("artifacts", {})
    artifacts["case_contracts"] = str(output_dir / "case_contracts.json")
    artifacts["case_contract_audit"] = str(output_dir / "case_contract_audit.json")
    upload_result = _ensure_clawweb_domains(
        args, plan, output_dir, manifest, task_id=task_id or args.task_id
    )
    _record_bench_artifacts(
        plan, output_dir, template_dir, zip_path, upload_result, manifest
    )
    return template_dir, zip_path, template_names, manifest, upload_result


def _render_templates(
    plan: dict[str, Any],
    output_dir: Path,
    goal: str,
    contracts: dict[str, dict[str, Any]] | None = None,
) -> tuple[Path, Path, list[str], dict[str, Any]]:
    logger.info(
        "template render start",
        output_dir=output_dir,
        case_count=len(plan.get("cases") or []),
    )
    result = render_templates(
        plan, output_dir, goal, plan.get("user_intent"), contracts
    )
    template_dir, zip_path, template_names, manifest = result
    logger.info(
        "template render done",
        template_dir=template_dir,
        zip_path=zip_path,
        split_packages=manifest.get("split_packages"),
        template_count=len(template_names),
    )
    return result


def _ensure_clawweb_domains(
    args: argparse.Namespace,
    plan: dict[str, Any],
    output_dir: Path,
    manifest: dict[str, Any],
    *,
    task_id: str,
) -> dict[str, Any]:
    if getattr(args, "skip_clawweb_report", False):
        result = skipped_dual_domain_upload_result(manifest=manifest)
        result["required"] = False
        logger.info("clawweb bench upload skipped", reason=result.get("reason"))
        return result

    existing = (
        None
        if args.overwrite
        else _existing_reusable_clawweb_upload_result(
            output_dir, manifest, revalidate_remote=True
        )
    )
    if existing:
        logger.info(
            "clawweb dual-domain upload reused",
            train_domain_id=existing.get("train_domain_id"),
            test_domain_id=existing.get("test_domain_id"),
        )
        return existing

    result = upload_bench_domains(plan=plan, manifest=manifest, task_id=task_id)
    result["required"] = True
    logger.info(
        "clawweb dual-domain upload complete",
        status=result.get("status"),
        published=result.get("published"),
        verified=result.get("verified"),
        train_domain_id=result.get("train_domain_id"),
        test_domain_id=result.get("test_domain_id"),
    )
    return result


def _record_bench_artifacts(
    plan: dict[str, Any],
    output_dir: Path,
    template_dir: Path,
    zip_path: Path,
    upload_result: dict[str, Any],
    template_manifest: dict[str, Any],
) -> None:
    domain_meta = dual_domain_meta(upload_result)
    plan["clawweb_domains"] = domain_meta
    # Compatibility alias: downstream code written for the old singular field
    # receives the dual-domain aggregate instead of a fabricated single domain.
    plan["clawweb_domain"] = domain_meta
    artifacts = plan.setdefault("artifacts", {})
    artifacts["clawbench_template_dir"] = str(template_dir)
    artifacts["clawbench_zip"] = str(zip_path)
    for split in ("train", "test"):
        package = (template_manifest.get("split_packages") or {}).get(split) or {}
        artifacts[f"clawbench_{split}_zip"] = str(package.get("zip_path") or "")
    artifacts["clawbench_manifest"] = str(template_dir / "manifest.json")
    artifacts["clawweb_upload_result"] = str(output_dir / "clawweb_upload_result.json")
    _write_json(output_dir / "clawweb_upload_result.json", upload_result)
    _write_json(output_dir / "clawbench_manifest.json", template_manifest)
