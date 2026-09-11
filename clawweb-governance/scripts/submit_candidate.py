#!/usr/bin/env python3
"""Submit a ClawWeb Governance candidate using only the Python standard library."""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_FIELDS = {
    "ownerUserId",
    "sourceOwnerUserId",
    "botId",
    "title",
    "userGuidance",
    "sourceRuleId",
    "actionType",
    "assignmentReason",
    "rootCauseSummary",
    "suggestedAction",
    "selectedTasks",
}
FORBIDDEN_FIELDS = {
    "status",
    "adminReviewStatus",
    "sourceType",
    "createdBy",
    "version",
    "appliedEvolveTaskId",
    "latestEvolveTaskId",
}


def fail(message):
    raise ValueError(message)


def required_string(value, name, max_length=None):
    if not isinstance(value, str) or not value.strip():
        fail("%s 必须是非空字符串" % name)
    normalized = value.strip()
    if max_length is not None and len(normalized) > max_length:
        fail("%s 不能超过 %d 个字符" % (name, max_length))
    return normalized


def validate_candidate(raw):
    if not isinstance(raw, dict):
        fail("候选输入必须是 JSON 对象")
    for key in raw:
        if key in FORBIDDEN_FIELDS:
            fail("禁止从客户端设置字段: %s" % key)
        if key not in ALLOWED_FIELDS:
            fail("候选输入包含未知字段: %s" % key)

    candidate = dict(raw)
    candidate["ownerUserId"] = required_string(candidate.get("ownerUserId"), "ownerUserId", 128)
    if candidate.get("sourceOwnerUserId") is not None:
        candidate["sourceOwnerUserId"] = required_string(
            candidate["sourceOwnerUserId"], "sourceOwnerUserId", 128
        )
    candidate["botId"] = required_string(candidate.get("botId"), "botId", 128)
    candidate["title"] = required_string(candidate.get("title"), "title", 256)
    candidate["sourceRuleId"] = required_string(candidate.get("sourceRuleId"), "sourceRuleId", 64)
    action_type = required_string(candidate.get("actionType"), "actionType", 32).upper()
    if action_type not in ("DIRECT_EVOLUTION", "ASSIGN_OWNER"):
        fail("actionType 只能是 DIRECT_EVOLUTION 或 ASSIGN_OWNER")
    candidate["actionType"] = action_type

    selected_tasks = candidate.get("selectedTasks")
    if not isinstance(selected_tasks, list) or not 1 <= len(selected_tasks) <= 50:
        fail("selectedTasks 必须包含 1 到 50 个失败任务")
    seen = set()
    normalized_tasks = []
    for index, task in enumerate(selected_tasks):
        if not isinstance(task, dict):
            fail("selectedTasks[%d] 格式不正确" % index)
        session_id = required_string(task.get("sessionId"), "selectedTasks[%d].sessionId" % index, 128)
        task_index = task.get("taskIndex")
        if isinstance(task_index, bool) or not isinstance(task_index, int) or task_index < 0:
            fail("selectedTasks[%d].taskIndex 不合法" % index)
        identity = "%s:%d" % (session_id, task_index)
        if identity in seen:
            fail("selectedTasks 重复: %s" % identity)
        seen.add(identity)
        normalized_tasks.append({"sessionId": session_id, "taskIndex": task_index})
    candidate["selectedTasks"] = normalized_tasks

    for field, max_length in (
        ("userGuidance", 5000),
        ("assignmentReason", 1000),
        ("rootCauseSummary", 1000),
        ("suggestedAction", 5000),
    ):
        if candidate.get(field) is not None:
            candidate[field] = required_string(candidate[field], field, max_length)
    return candidate


def normalize_base_url(value):
    value = required_string(value, "CLAWWEB_URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        fail("CLAWWEB_URL 不能包含凭据、查询参数或 fragment")
    host = (parsed.hostname or "").lower()
    local_http = parsed.scheme == "http" and host in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not local_http:
        fail("非本地 CLAWWEB_URL 必须使用 HTTPS")
    if not parsed.netloc:
        fail("CLAWWEB_URL 不合法")
    return "%s://%s" % (parsed.scheme, parsed.netloc)


def read_input(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def compact_json(value, sort_keys=False):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
    )


def read_response(response):
    text = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text[:2000]}


def post_json(url, body_text, headers):
    request = urllib.request.Request(
        url,
        data=body_text.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, read_response(response)
    except urllib.error.HTTPError as error:
        result = read_response(error)
        raise RuntimeError("ClawWeb HTTP %d: %s" % (error.code, compact_json(result)))
    except urllib.error.URLError as error:
        raise RuntimeError("ClawWeb 请求失败: %s" % error.reason)


def main():
    parser = argparse.ArgumentParser(description="Submit a ClawWeb Governance candidate")
    parser.add_argument("--input", required=True, help="Candidate JSON path, or - for stdin")
    parser.add_argument("--base-url", help="ClawWeb origin; defaults to CLAWWEB_URL")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base_url = normalize_base_url(args.base_url or os.environ.get("CLAWWEB_URL", ""))
    candidate = validate_candidate(read_input(args.input))
    body_text = compact_json(candidate)
    digest = hashlib.sha256(compact_json(candidate, sort_keys=True).encode("utf-8")).hexdigest()
    idempotency_key = (
        args.idempotency_key
        or os.environ.get("INSIGHT_IDEMPOTENCY_KEY")
        or "gov-%s-%s" % (candidate["ownerUserId"], digest[:24])
    )
    if len(idempotency_key) > 128:
        fail("Idempotency-Key 不能超过 128 个字符")
    endpoint = base_url + "/api/insight/v1/internal/governance/actions"

    if args.dry_run:
        print(json.dumps({
            "endpoint": endpoint,
            "idempotencyKey": idempotency_key,
            "candidate": candidate,
        }, ensure_ascii=False, indent=2))
        return

    status_code, result = post_json(
        endpoint,
        body_text,
        {"Content-Type": "application/json", "Idempotency-Key": idempotency_key},
    )
    if result.get("buserviceErrorCode") or result.get("actionType") == "LOGIN":
        raise RuntimeError("请求被 ACE/Buservice 拦截: %s" % result.get("buserviceErrorCode", "LOGIN"))
    if result.get("status") != "PENDING_ADMIN" or result.get("adminReviewStatus") != "PENDING":
        raise RuntimeError(
            "状态机契约异常: status=%s, adminReviewStatus=%s"
            % (result.get("status"), result.get("adminReviewStatus"))
        )
    print(json.dumps({
        "httpStatus": status_code,
        "improvementId": result.get("improvementId"),
        "ownerUserId": result.get("ownerUserId"),
        "botId": result.get("botId"),
        "title": result.get("title"),
        "actionType": result.get("actionType"),
        "status": result.get("status"),
        "adminReviewStatus": result.get("adminReviewStatus"),
        "createdBy": result.get("createdBy"),
        "version": result.get("version"),
        "idempotencyKey": idempotency_key,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, json.JSONDecodeError, OSError) as error:
        print("Error: %s" % error, file=sys.stderr)
        sys.exit(1)
