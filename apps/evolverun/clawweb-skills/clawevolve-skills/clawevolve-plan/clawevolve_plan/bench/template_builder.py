from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .. import logger
from .split import assign_train_test_splits, build_split_audit
from ..spec.contract import validate_task_template_markdown


def case_template_id(case: dict[str, Any]) -> str:
    explicit = str(case.get("template_id") or "").strip()
    if explicit:
        return _slug(explicit, 110)
    existing = str(case.get("case_id") or "").strip()
    if existing.startswith("task_"):
        return _slug(existing, 96)
    seed = "\n".join(
        [
            str(case.get("session_id") or ""),
            str(case.get("query") or case.get("prompt") or ""),
            str(case.get("evolution_failure_mode") or case.get("failure_mode") or ""),
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
    mode = _slug(
        str(case.get("evolution_failure_mode") or case.get("failure_mode") or "case"),
        32,
    )
    return f"task_session_{digest}_{mode}"


def render_templates(
    plan: dict[str, Any],
    output_root: Path,
    goal_text: str = "",
    user_intent: dict[str, Any] | None = None,
    contracts: dict[str, dict[str, Any]] | None = None,
) -> tuple[Path, Path, list[str], dict[str, Any]]:
    """Render ClawBench markdown templates from canonical Source cases and package them.

    This is the plan-owned migration of template creation.  The generated files
    intentionally follow clawbench-template/references/TASK_TEMPLATE.md: YAML
    frontmatter plus Prompt, Expected Behavior, Grading Criteria, Automated
    Checks, LLM Judge Rubric, Workspace Files, and Additional Notes sections.
    """
    templates_dir = output_root / "templates"
    if templates_dir.exists():
        shutil.rmtree(templates_dir)
    opt_dir = templates_dir / "opt"
    val_dir = templates_dir / "val"
    opt_dir.mkdir(parents=True, exist_ok=True)
    val_dir.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    manifest_items: list[dict[str, Any]] = []
    source_cases = list(plan.get("cases") or [])
    already_prepared = bool(source_cases) and all(
        str(case.get("split") or case.get("case_split") or "").lower()
        in {"train", "test"}
        and str(case.get("template_id") or "").strip()
        for case in source_cases
    )
    cases = (
        [dict(case) for case in source_cases]
        if already_prepared
        else assign_case_template_ids(assign_train_test_splits(source_cases))
    )
    template_ids = [str(case["template_id"]) for case in cases]
    for case, cid in zip(cases, template_ids):
        names.append(cid)
        split = _split_value(case)
        split_dir = _template_split_dir(templates_dir, split)
        rel_file = f"{split_dir.name}/{cid}.md"
        contract = (contracts or {}).get(
            str(case.get("case_id") or case.get("session_id") or cid)
        )
        if contract and str(contract.get("template_id") or "") != cid:
            raise ValueError(
                f"contract template_id does not match rendered template ID for {cid}"
            )
        content = _render_case_markdown(
            plan, case, cid, split, goal_text, user_intent, contract
        )
        template_path = split_dir / f"{cid}.md"
        template_path.write_text(content, encoding="utf-8")
        item = _manifest_item(cid, case, split, rel_file)
        item["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if contract:
            item["contract_schema_version"] = contract.get("schema_version", "")
            item["grading_strategy"] = contract.get("grading_strategy", {})
            item["replayability"] = contract.get("replayability", {})
            item["contract_provenance"] = contract.get("provenance", {})
        manifest_items.append(item)
    manifest = {
        "schema_version": "clawevolve-plan-clawbench-dataset.v1",
        "bot_id": plan.get("bot_id", ""),
        "template_count": len(names),
        "template_layout": "split_dirs",
        "opt_template_dir": str(opt_dir),
        "val_template_dir": str(val_dir),
        "source": "clawevolve-plan",
        "user_intent": user_intent or {},
        "intent_alignment": {
            "applied_to": ["Expected Behavior", "Grading Criteria", "LLM Judge Rubric"]
        },
        "diagnose_result": plan.get("diagnose_result")
        or (plan.get("artifacts") or {}).get("diagnose_result_json", ""),
        "templates": manifest_items,
    }
    split_audit = build_split_audit(cases)
    (output_root / "bench_split.json").write_text(
        json.dumps(split_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "bench split audit written",
        path=output_root / "bench_split.json",
        train_count=split_audit.get("summary", {}).get("trainCount"),
        test_count=split_audit.get("summary", {}).get("testCount"),
        warnings=split_audit.get("warnings"),
    )
    zip_path = output_root / "clawbench_dataset.zip"
    train_zip_path = output_root / "clawbench_train_dataset.zip"
    test_zip_path = output_root / "clawbench_test_dataset.zip"
    if len(names) != len(set(names)):
        raise ValueError("generated template IDs are not unique")
    if len(manifest_items) != len(cases):
        raise ValueError("template manifest count does not match source case count")

    expected_entries = [str(item["relative_path"]) for item in manifest_items]
    _write_template_zip(zip_path, templates_dir, expected_entries)
    split_packages: dict[str, dict[str, Any]] = {}
    for split, package_path in (("train", train_zip_path), ("test", test_zip_path)):
        split_items = [item for item in manifest_items if item.get("split") == split]
        split_entries = [str(item["relative_path"]) for item in split_items]
        _write_template_zip(
            package_path, templates_dir, split_entries, expected_split=split
        )
        split_packages[split] = {
            "zip_path": str(package_path),
            "template_names": [str(item["id"]) for item in split_items],
            "template_count": len(split_items),
            "relative_paths": split_entries,
            "zip_sha256": _file_sha256(package_path),
        }

    manifest["aggregate_zip_path"] = str(zip_path)
    manifest["aggregate_zip_sha256"] = _file_sha256(zip_path)
    manifest["split_packages"] = split_packages
    (templates_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return templates_dir, zip_path, names, manifest


def _write_template_zip(
    zip_path: Path,
    templates_dir: Path,
    entries: list[str],
    *,
    expected_split: str | None = None,
) -> None:
    validated = _validated_template_entries(
        templates_dir, entries, expected_split=expected_split
    )
    temporary_path = zip_path.with_suffix(f"{zip_path.suffix}.tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    try:
        with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative_path, source in validated:
                archive.write(source, relative_path)
        with zipfile.ZipFile(temporary_path) as archive:
            actual = sorted(
                name for name in archive.namelist() if not name.endswith("/")
            )
        expected = sorted(relative_path for relative_path, _ in validated)
        if actual != expected:
            raise ValueError(
                f"ZIP entries do not match expected templates: {zip_path.name}"
            )
        temporary_path.replace(zip_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _validated_template_entries(
    templates_dir: Path,
    entries: list[str],
    *,
    expected_split: str | None,
) -> list[tuple[str, Path]]:
    if expected_split not in (None, "train", "test"):
        raise ValueError(f"unsupported template package split: {expected_split}")
    expected_prefix = (
        "opt"
        if expected_split == "train"
        else "val"
        if expected_split == "test"
        else None
    )
    templates_root = templates_dir.resolve()
    seen: set[str] = set()
    validated: list[tuple[str, Path]] = []
    for raw_entry in entries:
        relative_path = str(raw_entry).strip()
        raw_parts = relative_path.split("/")
        posix_path = PurePosixPath(relative_path)
        if (
            not relative_path
            or "\\" in relative_path
            or posix_path.is_absolute()
            or len(raw_parts) != 2
            or any(part in ("", ".", "..") for part in raw_parts)
            or raw_parts[0] not in {"opt", "val"}
            or posix_path.suffix != ".md"
        ):
            raise ValueError(
                "template manifest path must match opt/<file>.md or val/<file>.md: "
                f"{relative_path!r}"
            )
        if expected_prefix is not None and raw_parts[0] != expected_prefix:
            raise ValueError(
                f"{expected_split} template path must use {expected_prefix}/: "
                f"{relative_path}"
            )
        if relative_path in seen:
            raise ValueError(f"duplicate template manifest path: {relative_path}")
        seen.add(relative_path)
        source = (templates_root / relative_path).resolve()
        try:
            source.relative_to(templates_root)
        except ValueError as exc:
            raise ValueError(
                f"template manifest path escapes templates directory: {relative_path}"
            ) from exc
        if not source.is_file():
            raise ValueError(f"template file missing while packaging: {relative_path}")
        validated.append((relative_path, source))
    return validated


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assign_case_template_ids(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign the exact template IDs consumed by contracts, files and manifests."""
    template_ids = _unique_template_ids(cases)
    assigned: list[dict[str, Any]] = []
    for case, template_id in zip(cases, template_ids):
        normalized = dict(case)
        normalized["template_id"] = template_id
        assigned.append(normalized)
    return assigned


def _unique_template_ids(cases: list[dict[str, Any]]) -> list[str]:
    identities: set[str] = set()
    used: set[str] = set()
    result: list[str] = []
    for index, case in enumerate(cases):
        identity = str(
            case.get("case_id")
            or case.get("source_session_id")
            or case.get("session_id")
            or ""
        ).strip()
        if not identity:
            identity = hashlib.sha256(
                json.dumps(
                    case, ensure_ascii=False, sort_keys=True, default=str
                ).encode("utf-8")
            ).hexdigest()
        if identity in identities:
            raise ValueError(f"duplicate bench case identity: {identity}")
        identities.add(identity)
        candidate = case_template_id(case)
        if candidate in used:
            suffix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
            candidate = _slug(f"{candidate}_{suffix}", 110)
        if candidate in used:
            raise ValueError(
                f"template ID collision could not be resolved for case {identity} at index {index}"
            )
        used.add(candidate)
        result.append(candidate)
    return result


def _bounded_timeout_seconds(*values: Any) -> int:
    selected = next((value for value in values if value not in (None, "")), 600)
    if isinstance(selected, bool):
        raise ValueError("case timeout_seconds must be an integer between 1 and 3600")
    try:
        timeout = int(selected)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"case timeout_seconds must be an integer between 1 and 3600: {selected!r}"
        ) from exc
    if timeout < 1 or timeout > 3600:
        raise ValueError(f"case timeout_seconds must be between 1 and 3600: {timeout}")
    return timeout


def _split_value(case: dict[str, Any]) -> str:
    raw = str(case.get("split") or case.get("case_split") or "train").strip().lower()
    return "train" if raw in {"train", "training"} else "test"


def _template_split_dir(templates_dir: Path, split: str) -> Path:
    # plan exposes train templates as optimization examples (opt) and test
    # templates as validation examples (val). Keep the logical split names in
    # manifests and step reports as train/test for API compatibility.
    return templates_dir / ("opt" if split == "train" else "val")


def _render_case_markdown(
    plan: dict[str, Any],
    case: dict[str, Any],
    cid: str,
    split: str,
    goal_text: str,
    user_intent: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> str:
    query = _case_query(case)
    if not query:
        replay = (contract or {}).get("replayability") or {}
        if replay.get("replayable") is not False:
            raise ValueError(f"template {cid} has no replayable user prompt")
        query = "请基于当前任务上下文完成用户请求；若上下文不足，先明确说明缺失信息并请求补充。"
    mode = str(
        case.get("evolution_failure_mode")
        or case.get("failure_mode")
        or "unknown_failure_mode"
    )
    case_type = str(case.get("case_type") or "bad")
    timeout = _bounded_timeout_seconds(
        case.get("timeout_seconds"),
        (plan.get("case_preference") or {}).get("timeout_seconds"),
    )
    requires_search = bool(case.get("requires_search"))
    analysis = _clean(
        case.get("problem_analysis") or case.get("root_cause_summary") or ""
    )
    user_req = _clean((plan.get("case_preference") or {}).get("raw_message") or "")
    intent = _clean(
        (user_intent or {}).get("intent_text")
        or (plan.get("user_intent") or {}).get("intent_text")
        or user_req
    )
    task = (contract or {}).get("task_contract") or {}
    strategy = (contract or {}).get("grading_strategy") or {}
    criteria = strategy.get("criteria") or []
    frontmatter = _frontmatter(
        {
            "id": cid,
            "name": f"{case_type.upper()} - {mode}",
            "category": f"session_{_slug(mode, 40)}",
            "grading_type": strategy.get("grading_type", "llm_judge"),
            "gradingType": strategy.get("grading_type", "llm_judge"),
            "targetType": "agent_session",
            "timeout_seconds": timeout,
            "workspace_files": (contract or {}).get("workspace_files", []),
        }
    )
    expected = _bullets(task.get("required_outcomes"), "完成用户要求的结果")
    approaches = _bullets(task.get("acceptable_approaches"), "采用与任务匹配的可行方法")
    actions = _bullets(task.get("required_actions"), "执行必要动作")
    evidence = _bullets(task.get("required_evidence"), "提供可验证证据")
    forbidden = _bullets(task.get("forbidden_behaviors"), "不得编造结果或泄露内部信息")
    criteria_md = (
        "\n".join(
            f"- [ ] {c.get('name')}: {c.get('description')}（权重 {c.get('weight')}%）"
            for c in criteria
        )
        or "- [ ] 完整、真实地完成 Prompt 中的用户意图。"
    )
    rubric_md = (
        "\n\n".join(_criterion_markdown(i, c) for i, c in enumerate(criteria, 1))
        or "### Criterion 1: Task Completion (Weight: 100%)\n\n**Score 1.0**: 完整完成任务并提供证据。\n\n**Score 0.0**: 未完成任务或输出无关。"
    )
    checks = (contract or {}).get("automated_checks") or []
    checks_block = (
        f"```python\n{_checks_markdown(checks)}\n```"
        if checks
        else (
            "No deterministic automated checks. "
            "This task is graded by the LLM Judge Rubric."
        )
    )
    body = f"""{frontmatter}

# Task Template

---

## Prompt

{query}

## Expected Behavior

{expected}

可接受的实现路径：
{approaches}

必须执行的动作：
{actions}

必须提供的证据：
{evidence}

约束与边界：
{forbidden}

{_search_rule(requires_search)}

## Grading Criteria

{criteria_md}

## Automated Checks

{checks_block}

## LLM Judge Rubric

{rubric_md}

## Workspace Files

{_workspace_files((contract or {}).get("workspace_files") or [])}

## Additional Notes

本模板由 clawevolve-plan 基于冻结 Plan Source 生成；章节结构严格遵循 TASK_TEMPLATE.md。

- Source session id: `{case.get("session_id") or ""}`
- Case split: `{split}`
- Case type: `{case_type}`
- Failure mode: `{mode}`
- User intent: {intent or "无额外意图"}
- Goal: {goal_text or "提升任务完成质量并保护成功行为"}
- Historical analysis: {analysis or "N/A"}
- Replayability: {json.dumps((contract or {}).get("replayability") or {}, ensure_ascii=False)}
"""
    validate_task_template_artifact(body, cid)
    return body


def _bullets(values: Any, default: str) -> str:
    items = values if isinstance(values, list) else []
    return (
        "\n".join(f"- {str(x).strip()}" for x in items if str(x).strip())
        or f"- {default}"
    )


def _criterion_markdown(index: int, criterion: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"### Criterion {index}: {criterion.get('name')} (Weight: {criterion.get('weight')}%)",
            "",
            f"**Score 1.0**: {criterion.get('score_1')}",
            "",
            f"**Score 0.75**: {criterion.get('score_075')}",
            "",
            f"**Score 0.5**: {criterion.get('score_05')}",
            "",
            f"**Score 0.25**: {criterion.get('score_025')}",
            "",
            f"**Score 0.0**: {criterion.get('score_0')}",
        ]
    )


def _checks_markdown(checks: list[dict[str, Any]]) -> str:
    serialized = json.dumps(checks, ensure_ascii=False, separators=(",", ":"))
    return f'''def grade(transcript: list, workspace_path: str) -> dict:
    import json
    from pathlib import Path

    checks = json.loads({serialized!r})
    events = transcript if isinstance(transcript, list) else []
    workspace = Path(workspace_path).resolve() if workspace_path else None

    def text_value(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\\n".join(text_value(item) for item in value)
        if isinstance(value, dict):
            return "\\n".join(
                text_value(value.get(key))
                for key in ("text", "content", "message", "result", "error")
                if value.get(key) is not None
            )
        return "" if value is None else str(value)

    def walk(value):
        yield value
        if isinstance(value, dict):
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    def assistant_response():
        parts = []
        for event in events:
            message = event.get("message") if isinstance(event, dict) else None
            if isinstance(message, dict) and str(message.get("role") or "").lower() == "assistant":
                parts.append(text_value(message.get("content")))
        return "\\n".join(part for part in parts if part)

    def tool_names():
        names = []
        for node in walk(events):
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("type") or "").lower().replace("_", "")
            candidate = node.get("toolName") or node.get("tool_name")
            if node_type in {{"toolcall", "functioncall"}}:
                candidate = candidate or node.get("name")
                function = node.get("function")
                if isinstance(function, dict):
                    candidate = candidate or function.get("name")
            if candidate:
                names.append(str(candidate))
        return names

    def has_tool_result(expected=""):
        expected = str(expected or "").strip()
        for node in walk(events):
            if not isinstance(node, dict):
                continue
            role = str(node.get("role") or "").lower().replace("_", "")
            node_type = str(node.get("type") or "").lower().replace("_", "")
            if role not in {{"tool", "toolresult"}} and node_type != "toolresult":
                continue
            name = str(node.get("toolName") or node.get("tool_name") or node.get("name") or "")
            if not expected or name == expected:
                return True
        return False

    def safe_file(raw_path):
        if workspace is None:
            return None
        raw = str(raw_path or "").strip()
        if not raw:
            return None
        candidate = (workspace / raw).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError:
            return None
        return candidate

    response = assistant_response()
    called_tools = tool_names()
    scores = {{}}
    for index, check in enumerate(checks, start=1):
        check_type = str(check.get("type") or "")
        key = f"{{check_type or 'invalid'}}_{{index}}"
        score = 0.0
        expected = str(check.get("text") or check.get("substring") or check.get("value") or "")
        tool = str(check.get("tool") or check.get("tool_name") or check.get("name") or "")
        path = safe_file(check.get("path") or check.get("file"))
        if check_type == "transcript_present":
            score = 1.0 if events else 0.0
        elif check_type == "response_contains" and expected:
            score = 1.0 if expected in response else 0.0
        elif check_type == "response_not_contains" and expected:
            score = 1.0 if response and expected not in response else 0.0
        elif check_type == "tool_called" and tool:
            score = 1.0 if tool in called_tools else 0.0
        elif check_type == "tool_result_present":
            score = 1.0 if has_tool_result(tool) else 0.0
        elif check_type == "file_exists" and path is not None:
            score = 1.0 if path.is_file() else 0.0
        elif check_type == "file_contains" and path is not None and expected:
            try:
                score = 1.0 if expected in path.read_text(encoding="utf-8", errors="replace") else 0.0
            except OSError:
                score = 0.0
        elif check_type == "file_json_schema" and path is not None:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                required = check.get("required")
                schema = check.get("schema")
                if required is None and isinstance(schema, dict):
                    required = schema.get("required")
                required = required if isinstance(required, list) else []
                score = 1.0 if isinstance(payload, dict) and all(item in payload for item in required) else 0.0
            except (OSError, ValueError, TypeError):
                score = 0.0
        scores[key] = score
    return scores'''


def _workspace_files(files: list[Any]) -> str:
    return (
        "None."
        if not files
        else "\n".join(f"- `{json.dumps(item, ensure_ascii=False)}`" for item in files)
    )


def _case_query(case: dict[str, Any]) -> str:
    query = _clean(case.get("query") or case.get("prompt") or "")
    if query and not _looks_like_trace_json(query):
        return query
    recovered = _query_from_raw_session(case.get("raw_session_copy_path"))
    if recovered:
        return recovered
    return "" if _looks_like_trace_json(query) else query


def _looks_like_trace_json(value: str) -> bool:
    text = _clean(value)
    return text.startswith("{") and any(
        token in text[:500]
        for token in (
            "traceSchema",
            "session.started",
            "trace.metadata",
            "schemaVersion",
        )
    )


def _query_from_raw_session(path_value: Any) -> str:
    path = Path(str(path_value or "")).expanduser()
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except Exception:
            messages = []
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if str(msg.get("role") or "").lower() not in {"user", "human"}:
                continue
            text = _clean(_text_value(msg.get("content")))
            if text and not _looks_like_trace_json(text):
                return text
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        text = _clean(
            _text_value(
                payload.get("finalPromptText")
                or data.get("finalPromptText")
                or data.get("final_prompt_text")
            )
        )
        if text and not _looks_like_trace_json(text):
            return text
    return ""


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text_value(x) for x in value if x is not None)
    if isinstance(value, dict):
        for key in (
            "text",
            "content",
            "message",
            "query",
            "prompt",
            "input",
            "result",
            "error",
        ):
            if key in value and value.get(key) is not None:
                return _text_value(value.get(key))
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _frontmatter(values: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def _search_rule(requires_search: bool) -> str:
    if requires_search:
        return "该 case 需要搜索/检索或读取外部/本地证据：必须真实获取相关证据并在最终结果中使用。"
    return "如任务需要文件或工具证据，应真实执行；否则可以直接完成，但不得编造。"


def _manifest_item(
    cid: str, case: dict[str, Any], split: str, rel_file: str
) -> dict[str, Any]:
    return {
        "id": cid,
        "file": rel_file,
        "relative_path": rel_file,
        "template_dir": "opt" if split == "train" else "val",
        "case_id": case.get("case_id") or cid,
        "split": split,
        "case_split": split,
        "case_type": case.get("case_type", ""),
        "failure_mode": case.get("evolution_failure_mode")
        or case.get("failure_mode", ""),
        "session_id": case.get("session_id", ""),
        "source_session_id": case.get("source_session_id")
        or case.get("session_id", ""),
        "analysis_path": case.get("analysis_path", ""),
        "judge_result_path": case.get("judge_result_path", ""),
        "raw_session_copy_path": case.get("raw_session_copy_path", ""),
        "root_cause_cluster_id": case.get("root_cause_cluster_id")
        or case.get("cluster_id")
        or case.get("common_problem_key", ""),
        "split_reason": case.get("split_reason", ""),
        "split_group_key": case.get("split_group_key", ""),
        "split_stratum_key": case.get("split_stratum_key", ""),
        "split_scores": case.get("split_scores") or {},
        "contract_id": case.get("case_id") or case.get("session_id") or cid,
    }


def validate_task_template_artifact(text: str, expected_id: str) -> None:
    """Validate a rendered or cached template and its manifest identity."""
    cid = str(expected_id or "").strip()
    if not cid:
        raise ValueError("task template expected id is empty")
    if (
        not text.startswith("---\n")
        or f"id: {json.dumps(cid, ensure_ascii=False)}" not in text.split("---", 2)[1]
    ):
        raise ValueError(f"template {cid} frontmatter invalid")
    for sec in (
        "## Prompt",
        "## Expected Behavior",
        "## Grading Criteria",
        "## Automated Checks",
        "## LLM Judge Rubric",
        "## Workspace Files",
        "## Additional Notes",
    ):
        if sec not in text:
            raise ValueError(f"template {cid} missing {sec}")
    validate_task_template_markdown(text)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: str, max_len: int = 64) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_")
    return (text or "case")[:max_len]


def ensure_split_packages(
    output_root: Path, templates_dir: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Create or refresh split ZIP metadata for an existing template manifest."""
    items = [item for item in manifest.get("templates") or [] if isinstance(item, dict)]
    packages: dict[str, dict[str, Any]] = {}
    for split, filename in (
        ("train", "clawbench_train_dataset.zip"),
        ("test", "clawbench_test_dataset.zip"),
    ):
        selected = [item for item in items if _split_value(item) == split]
        entries = [
            str(item.get("relative_path") or item.get("file") or "")
            for item in selected
        ]
        if any(not entry for entry in entries):
            raise ValueError(
                f"{split} manifest contains a template without relative_path"
            )
        package_path = output_root / filename
        _write_template_zip(package_path, templates_dir, entries, expected_split=split)
        packages[split] = {
            "zip_path": str(package_path),
            "template_names": [
                str(item.get("id") or item.get("task_id") or "") for item in selected
            ],
            "template_count": len(selected),
            "relative_paths": entries,
            "zip_sha256": _file_sha256(package_path),
        }
    manifest["split_packages"] = packages
    aggregate_path = output_root / "clawbench_dataset.zip"
    if aggregate_path.is_file():
        manifest["aggregate_zip_path"] = str(aggregate_path)
        manifest["aggregate_zip_sha256"] = _file_sha256(aggregate_path)
    (templates_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_root / "clawbench_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
