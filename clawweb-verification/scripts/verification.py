#!/usr/bin/env python3
"""Read and write unsigned ClawWeb verification results using Python standard library only."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

OUTCOMES = ("DISAPPEARED", "STILL_PRESENT", "INSUFFICIENT_DATA")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def fail(message):
    raise ValueError(message)


def required_string(value, name):
    if not isinstance(value, str) or not value.strip():
        fail("%s 必填" % name)
    return value.strip()


def integer(value, name, minimum=1, maximum=None):
    if isinstance(value, bool):
        fail("%s 不合法" % name)
    try:
        number = int(value)
    except (TypeError, ValueError):
        fail("%s 不合法" % name)
    if str(value).strip() != str(number) or number < minimum or (maximum is not None and number > maximum):
        fail("%s 不合法" % name)
    return number


def normalize_base_url(value):
    value = required_string(value, "CLAWWEB_URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        fail("CLAWWEB_URL 不能包含凭据、查询参数或 fragment")
    host = (parsed.hostname or "").lower()
    local_http = parsed.scheme == "http" and host in LOCAL_HOSTS
    if parsed.scheme != "https" and not local_http:
        fail("非本地 CLAWWEB_URL 必须使用 HTTPS")
    if not parsed.netloc:
        fail("CLAWWEB_URL 不合法")
    return {
        "origin": "%s://%s" % (parsed.scheme, parsed.netloc),
        "hostname": host,
    }


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))



def read_response(response):
    text = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text[:2000]}


def request(base_url, method, router_path, query="", body=None, dry_run=False):
    body_text = compact_json(body) if body is not None else ""
    headers = {"Content-Type": "application/json"} if body is not None else {}
    url = base_url["origin"] + "/api/insight/v1" + router_path + query
    if dry_run:
        return {
            "dryRun": True,
            "method": method,
            "url": url,
            "body": body,
            "authentication": "none",
        }

    request_value = urllib.request.Request(
        url,
        data=body_text.encode("utf-8") if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request_value, timeout=30) as response:
            result = read_response(response)
    except urllib.error.HTTPError as error:
        result = read_response(error)
        raise RuntimeError("ClawWeb HTTP %d: %s" % (error.code, compact_json(result)))
    except urllib.error.URLError as error:
        raise RuntimeError("ClawWeb 请求失败: %s" % error.reason)
    if result.get("buserviceErrorCode") or result.get("actionType") == "LOGIN":
        raise RuntimeError("请求被 ACE/Buservice 拦截: %s" % result.get("buserviceErrorCode", "LOGIN"))
    return result


def build_parser():
    parser = argparse.ArgumentParser(description="ClawWeb Verification Agent client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List verification candidates")
    list_parser.add_argument("--limit", default="100")
    list_parser.add_argument("--base-url")
    list_parser.add_argument("--json", action="store_true")
    list_parser.add_argument("--dry-run", action="store_true")

    open_parser = subparsers.add_parser("list-open", help="List open improvements for proactive verification")
    open_parser.add_argument("--limit", default="100")
    open_parser.add_argument("--base-url")
    open_parser.add_argument("--json", action="store_true")
    open_parser.add_argument("--dry-run", action="store_true")

    resolved_parser = subparsers.add_parser("list-resolved", help="List resolved/completed improvements for proactive verification")
    resolved_parser.add_argument("--limit", default="100")
    resolved_parser.add_argument("--owner-user-id")
    resolved_parser.add_argument("--bot-id")
    resolved_parser.add_argument("--since", default=None, help="ISO 时间，如 2026-08-01T00:00:00+08:00，默认 30 天前")
    resolved_parser.add_argument("--base-url")
    resolved_parser.add_argument("--json", action="store_true")
    resolved_parser.add_argument("--dry-run", action="store_true")

    open_submit_parser = subparsers.add_parser("submit-open", help="Submit a result for an open improvement")
    open_submit_parser.add_argument("--improvement-id", required=True)
    open_submit_parser.add_argument("--version", required=True)
    open_submit_parser.add_argument("--outcome", required=True)
    open_submit_parser.add_argument("--new-session-count", default="0")
    open_submit_parser.add_argument("--last-recurrence-at")
    open_submit_parser.add_argument("--override-action-type", choices=("ASSIGN_OWNER",), help="仅在 STILL_PRESENT 时将自动修复切换为手动修复")
    open_submit_parser.add_argument("--base-url")
    open_submit_parser.add_argument("--dry-run", action="store_true")

    submit_parser = subparsers.add_parser("submit", help="Submit one verification result")
    submit_parser.add_argument("--improvement-id", required=True)
    submit_parser.add_argument("--version", required=True)
    submit_parser.add_argument("--outcome", required=True)
    submit_parser.add_argument("--new-session-count", default="0")
    submit_parser.add_argument("--last-recurrence-at")
    submit_parser.add_argument("--override-action-type", choices=("ASSIGN_OWNER",), help="仅在 STILL_PRESENT 时将自动修复切换为手动修复")
    submit_parser.add_argument("--base-url")
    submit_parser.add_argument("--dry-run", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()
    base_url = normalize_base_url(args.base_url or os.environ.get("CLAWWEB_URL", ""))

    if args.command == "list":
        limit = integer(args.limit, "limit", minimum=1, maximum=200)
        result = request(
            base_url,
            "GET",
            "/internal/governance/verification-candidates",
            query="?limit=%d" % limit,
            dry_run=args.dry_run,
        )
        if result.get("dryRun") or args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        items = result.get("items") if isinstance(result.get("items"), list) else []
        print(json.dumps({
            "count": len(items),
            "items": [{
                "improvementId": item.get("improvementId"),
                "ownerUserId": item.get("ownerUserId"),
                "botId": item.get("botId"),
                "title": item.get("title"),
                "handledAt": item.get("handledAt"),
                "verificationStatus": item.get("verificationStatus"),
                "version": item.get("version"),
            } for item in items],
        }, ensure_ascii=False, indent=2))
        return

    if args.command == "list-open":
        limit = integer(args.limit, "limit", minimum=1, maximum=200)
        result = request(
            base_url,
            "GET",
            "/internal/governance/verification-candidates/open",
            query="?limit=%d" % limit,
            dry_run=args.dry_run,
        )
        if result.get("dryRun") or args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        items = result.get("items") if isinstance(result.get("items"), list) else []
        print(json.dumps({
            "count": len(items),
            "items": [{
                "improvementId": item.get("improvementId"),
                "ownerUserId": item.get("ownerUserId"),
                "botId": item.get("botId"),
                "title": item.get("title"),
                "status": item.get("status"),
                "actionType": item.get("actionType"),
                "handledAt": item.get("handledAt"),
                "gmtModified": item.get("gmtModified"),
                "latestEvolveTaskId": item.get("latestEvolveTaskId"),
                "latestEvolveTaskStatus": item.get("latestEvolveTaskStatus"),
                "verificationStatus": item.get("verificationStatus"),
                "version": item.get("version"),
            } for item in items],
        }, ensure_ascii=False, indent=2))
        return

    if args.command == "list-resolved":
        limit = integer(args.limit, "limit", minimum=1, maximum=200)
        params = [("limit", str(limit))]
        if args.owner_user_id:
            params.append(("ownerUserId", args.owner_user_id))
        if args.bot_id:
            params.append(("botId", args.bot_id))
        if args.since:
            params.append(("since", args.since))
        query = "?" + urllib.parse.urlencode(params)
        result = request(
            base_url,
            "GET",
            "/internal/governance/actions",
            query=query,
            dry_run=args.dry_run,
        )
        if result.get("dryRun") or args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        items = result.get("items") if isinstance(result.get("items"), list) else []
        # Filter to RESOLVED, VERIFIED, AUTO_VERIFIED
        resolved = [it for it in items if it.get("status") in ("RESOLVED", "VERIFIED", "AUTO_VERIFIED")]
        print(json.dumps({
            "count": len(resolved),
            "items": [{
                "improvementId": it.get("improvementId"),
                "ownerUserId": it.get("ownerUserId"),
                "botId": it.get("botId"),
                "title": it.get("title"),
                "rootCauseSummary": it.get("rootCauseSummary"),
                "actionType": it.get("actionType"),
                "status": it.get("status"),
                "handledAt": it.get("handledAt"),
                "resolvedAt": it.get("resolvedAt"),
                "version": it.get("version"),
            } for it in resolved],
        }, ensure_ascii=False, indent=2))
        return

    if args.command == "submit-open":
        improvement_id = integer(args.improvement_id, "improvement-id")
        version = integer(args.version, "version")
        outcome = required_string(args.outcome, "outcome").upper()
        if outcome not in OUTCOMES:
            fail("outcome 不合法")
        new_session_count = integer(args.new_session_count, "new-session-count", minimum=0)
        if outcome == "DISAPPEARED" and new_session_count < 1:
            fail("DISAPPEARED 至少需要一个新 Session")
        if outcome == "STILL_PRESENT" and new_session_count < 1:
            fail("STILL_PRESENT 至少需要一个新 Session")
        body = {
            "improvementId": improvement_id,
            "version": version,
            "outcome": outcome,
            "newSessionCount": new_session_count,
        }
        if args.last_recurrence_at:
            body["lastRecurrenceAt"] = args.last_recurrence_at
        if args.override_action_type:
            if outcome != "STILL_PRESENT":
                fail("override-action-type 只能用于 STILL_PRESENT")
            body["overrideActionType"] = args.override_action_type
        result = request(
            base_url,
            "POST",
            "/internal/governance/verification-results/open",
            body=body,
            dry_run=args.dry_run,
        )
        if result.get("dryRun"):
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        improvement = result.get("improvement") if isinstance(result.get("improvement"), dict) else result
        print(json.dumps({
            "improvementId": improvement.get("improvementId"),
            "status": improvement.get("status"),
            "verificationStatus": improvement.get("verificationStatus"),
            "verificationNewSessionCount": improvement.get("verificationNewSessionCount"),
            "verificationLastRecurrenceAt": improvement.get("verificationLastRecurrenceAt"),
            "resolvedSource": improvement.get("resolvedSource"),
            "version": improvement.get("version"),
            "ruleEvolutionProposal": result.get("ruleEvolutionProposal"),
        }, ensure_ascii=False, indent=2))
        return

    improvement_id = integer(args.improvement_id, "improvement-id")
    version = integer(args.version, "version")
    outcome = required_string(args.outcome, "outcome").upper()
    if outcome not in OUTCOMES:
        fail("outcome 不合法")
    new_session_count = integer(args.new_session_count, "new-session-count", minimum=0)
    if outcome == "DISAPPEARED" and new_session_count < 1:
        fail("DISAPPEARED 至少需要一个新 Session")
    body = {
        "improvementId": improvement_id,
        "version": version,
        "outcome": outcome,
        "newSessionCount": new_session_count,
    }
    if args.last_recurrence_at:
        body["lastRecurrenceAt"] = args.last_recurrence_at
    if args.override_action_type:
        if outcome != "STILL_PRESENT":
            fail("override-action-type 只能用于 STILL_PRESENT")
        body["overrideActionType"] = args.override_action_type
    result = request(
        base_url,
        "POST",
        "/internal/governance/verification-results",
        body=body,
        dry_run=args.dry_run,
    )
    if result.get("dryRun"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    improvement = result.get("improvement") if isinstance(result.get("improvement"), dict) else result
    print(json.dumps({
        "improvementId": improvement.get("improvementId"),
        "status": improvement.get("status"),
        "verificationStatus": improvement.get("verificationStatus"),
        "verificationNewSessionCount": improvement.get("verificationNewSessionCount"),
        "verificationLastRecurrenceAt": improvement.get("verificationLastRecurrenceAt"),
        "resolvedSource": improvement.get("resolvedSource"),
        "version": improvement.get("version"),
        "ruleEvolutionProposal": result.get("ruleEvolutionProposal"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, json.JSONDecodeError, OSError) as error:
        print("Error: %s" % error, file=sys.stderr)
        sys.exit(1)
