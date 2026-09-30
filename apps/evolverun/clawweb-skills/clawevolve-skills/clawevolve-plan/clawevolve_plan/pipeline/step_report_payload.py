from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

from ..constants import clawweb_base_url

from .. import logger
from ..io import load_json
from .common import _read_text_file, _text_preview


def build_step_report_output(
    spec: dict[str, Any],
    objective_doc: dict[str, Any],
    template_manifest: dict[str, Any],
    upload_result: dict[str, Any],
    spec_content: str = "",
) -> dict[str, Any]:
    goal = (
        objective_doc.get("goal") if isinstance(objective_doc.get("goal"), dict) else {}
    )
    criteria = (
        spec.get("acceptance_criteria")
        if isinstance(spec.get("acceptance_criteria"), dict)
        else {}
    )
    train_count, test_count = _bench_split_counts(template_manifest)
    bench_items = _bench_case_items(template_manifest, upload_result)
    metrics = _goal_metrics(criteria)
    content = spec_content.strip() or _fallback_spec_content(spec)
    domains_complete = bool(
        upload_result.get("published") and upload_result.get("verified")
    )
    train_domain = (
        _published_split_domain(upload_result, "train")
        if domains_complete
        else {"domain_id": "", "domain_url": ""}
    )
    test_domain = (
        _published_split_domain(upload_result, "test")
        if domains_complete
        else {"domain_id": "", "domain_url": ""}
    )
    goal_summary = _goal_summary_text(goal, spec)
    logger.info(
        "clawweb step report output built",
        goal_summary=goal_summary,
        metric_count=len(metrics),
        metrics_preview=metrics,
        spec_version=str(spec.get("spec_version") or "v0"),
        spec_content_bytes=len(content.encode("utf-8")),
        spec_content_preview=_text_preview(content),
        bench_train_count=train_count,
        bench_test_count=test_count,
        bench_item_count=len(bench_items),
        bench_items_preview=bench_items[:5],
        train_domain_id=train_domain["domain_id"],
        test_domain_id=test_domain["domain_id"],
        train_domain_full_url=train_domain["domain_url"],
        test_domain_full_url=test_domain["domain_url"],
    )
    return {
        "goal": {
            "summary": goal_summary,
            "metrics": metrics,
        },
        "spec": {
            "version": str(spec.get("spec_version") or "v0"),
            "content_type": "text",
            "content": content,
        },
        "benchCases": {
            "trainCount": train_count,
            "testCount": test_count,
            "items": bench_items,
        },
        "benchDomains": {
            "trainBenchDomainId": train_domain["domain_id"],
            "testBenchDomainId": test_domain["domain_id"],
            "trainBenchDomainFullURL": train_domain["domain_url"],
            "testBenchDomainFullURL": test_domain["domain_url"],
            "validationIndependent": bool(
                upload_result.get("validation_independent", True)
            ),
            "validationMode": str(
                upload_result.get("validation_mode") or "isolated_train_test"
            ),
        },
    }


def existing_step_report_output(
    output_dir: Path, upload_result: dict[str, Any]
) -> dict[str, Any]:
    objective_json = output_dir / "objective.json"
    spec_json = output_dir / "spec-v0.json"
    manifest_json = output_dir / "clawbench_manifest.json"
    objective_doc = load_json(objective_json) if objective_json.exists() else {}
    spec = load_json(spec_json) if spec_json.exists() else {}
    template_manifest = load_json(manifest_json) if manifest_json.exists() else {}
    spec_md = output_dir / "spec-v0.md"
    return build_step_report_output(
        spec, objective_doc, template_manifest, upload_result, _read_text_file(spec_md)
    )


def _fallback_spec_content(spec: dict[str, Any]) -> str:
    strategy = _goal_summary_text({}, spec)
    directions = spec.get("active_optimization_directions") or []
    lines = ["## 优化策略", ""]
    if strategy:
        lines.append(strategy)
    if isinstance(directions, list) and directions:
        lines.extend(["", "## 重点方向", ""])
        for idx, item in enumerate(directions, start=1):
            title = (
                item.get("title")
                or item.get("name")
                or item.get("summary")
                or item.get("id")
                or item
                if isinstance(item, dict)
                else item
            )
            lines.append(f"{idx}. {title}")
    content = "\n".join(str(line) for line in lines).strip()
    logger.info(
        "fallback spec report content built",
        bytes=len(content.encode("utf-8")),
        preview=_text_preview(content),
    )
    return content or "## 优化策略\n\n根据诊断结果完成针对性优化。"


def _goal_summary_text(goal: dict[str, Any], spec: dict[str, Any]) -> str:
    if isinstance(goal, dict):
        goal_text = str(goal.get("goal_text") or "").strip()
        if goal_text:
            return goal_text
    contract = spec.get("objective_contract") or {}
    if isinstance(contract, dict):
        summary = contract.get("objective_summary")
        if isinstance(summary, list):
            items = [str(item).strip() for item in summary if str(item).strip()]
            if items:
                return items[0]
        elif str(summary or "").strip():
            return str(summary).strip()
    alignment = str(spec.get("objective_alignment") or "").strip()
    if alignment:
        return alignment
    current_strategy = spec.get("current_strategy_summary")
    if isinstance(current_strategy, list):
        items = [str(item).strip() for item in current_strategy if str(item).strip()]
        if items:
            return items[0]
    elif str(current_strategy or "").strip():
        return str(current_strategy).strip()
    return "完成优化目标与 Bench 规划"


def _published_split_domain(
    upload_result: dict[str, Any], split: str
) -> dict[str, str]:
    domain = (upload_result.get("domains") or {}).get(split) or {}
    if domain.get("published") is not True or domain.get("verified") is not True:
        return {"domain_id": "", "domain_url": ""}
    return {
        "domain_id": str(domain.get("domain_id") or ""),
        "domain_url": _bench_domain_full_url(domain),
    }


def _bench_domain_full_url(upload_result: dict[str, Any]) -> str:
    explicit = str(
        upload_result.get("domain_url")
        or upload_result.get("domainFullURL")
        or upload_result.get("domain_full_url")
        or ""
    ).strip()
    if explicit:
        return explicit
    domain_id = str(upload_result.get("domain_id") or "").strip()
    owner = str(
        upload_result.get("owner_user_id") or upload_result.get("user_id") or ""
    ).strip()
    base_url = str(upload_result.get("base_url") or clawweb_base_url()).rstrip("/")
    return (
        f"{base_url}/bench/domains/"
        f"{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(domain_id, safe='')}"
        if owner and domain_id
        else ""
    )


def _bench_split_counts(template_manifest: dict[str, Any]) -> tuple[int, int]:
    train = 0
    test = 0
    for item in template_manifest.get("templates") or []:
        if not isinstance(item, dict):
            continue
        split = _report_split(item.get("split") or item.get("case_split"))
        if split == "train":
            train += 1
        else:
            test += 1
    if train == 0 and test == 0:
        train = int(template_manifest.get("template_count") or 0)
    logger.info(
        "bench split counts computed",
        train_count=train,
        test_count=test,
        template_count=template_manifest.get("template_count"),
    )
    return train, test


def _published_template_versions(domain: dict[str, Any]) -> dict[str, int]:
    versions: dict[str, int] = {}
    verification = domain.get("verify_published") or {}
    body = verification.get("body") if isinstance(verification, dict) else None
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        rows = body.get("items") or data.get("items") or []
    else:
        rows = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("templateName") or "").strip()
        raw_version = item.get("publishedVersion") or item.get("version")
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            continue
        if name and version > 0:
            versions[name] = version
    return versions


def _bench_case_items(
    template_manifest: dict[str, Any], upload_result: dict[str, Any]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    domains = upload_result.get("domains") or {}
    for index, item in enumerate(template_manifest.get("templates") or [], start=1):
        if not isinstance(item, dict):
            continue
        task_id = str(
            item.get("id")
            or item.get("task_id")
            or item.get("case_id")
            or f"case_{index:03d}"
        ).strip()
        source_case_id = str(
            item.get("case_id") or item.get("sourceCaseId") or task_id
        ).strip()
        split = _report_split(item.get("split") or item.get("case_split"))
        if not task_id or not source_case_id:
            logger.warning(
                "bench case item skipped because id is empty",
                index=index,
                raw_item=item,
            )
            continue
        report_item: dict[str, Any] = {
            "sourceCaseId": source_case_id,
            "taskId": task_id,
            "split": split,
        }
        domain = domains.get(split) or {}
        if domain.get("published") is True and domain.get("verified") is True:
            template_name = str(
                item.get("template_name") or item.get("templateName") or task_id
            ).strip()
            versions = _published_template_versions(domain)
            template: dict[str, Any] = {
                "ownerUserId": str(
                    domain.get("owner_user_id") or domain.get("user_id") or ""
                ),
                "domainId": str(domain.get("domain_id") or ""),
                "templateName": template_name,
            }
            if versions.get(template_name):
                template["version"] = versions[template_name]
            report_item["template"] = template
        items.append(report_item)
    logger.info("bench case items built", item_count=len(items), preview=items[:5])
    return items


def _report_split(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"train", "training"}:
        return "train"
    return "test"


def _goal_metrics(criteria: dict[str, Any]) -> list[dict[str, Any]]:
    primary = _primary_metric(criteria)
    metrics = [
        {
            "key": primary["name"],
            "name": primary["display_name"],
            "operator": primary["operator"],
            "target": primary["target"],
            "unit": primary["unit"],
        },
    ]
    logger.info("goal metrics built", metrics=metrics)
    return metrics


def _primary_metric(criteria: dict[str, Any]) -> dict[str, Any]:
    raw = criteria.get("primary_metric")
    if not isinstance(raw, dict):
        raise ValueError("acceptance_criteria.primary_metric is required")
    metric = raw
    try:
        target = float(metric.get("target"))
    except (TypeError, ValueError):
        raise ValueError("acceptance_criteria.primary_metric.target must be a ratio in [0, 1]") from None
    if not 0 <= target <= 1:
        raise ValueError("acceptance_criteria.primary_metric.target must be a ratio in [0, 1]")
    name = str(metric.get("name") or "").strip()
    if not name:
        raise ValueError("acceptance_criteria.primary_metric.name is required")
    operator = str(metric.get("operator") or "").strip()
    if operator != ">=":
        raise ValueError("acceptance_criteria.primary_metric.operator must be >=")
    return {
        "name": name,
        "display_name": str(metric.get("display_name") or name),
        "operator": operator,
        "target": target,
        "unit": str(metric.get("unit") or "ratio"),
    }
