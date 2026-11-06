"""Evidence-first verification; machine metadata is optional, never an admission gate."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .core import API_PREFIX, LEGACY_ROOT_MARKERS, instant, root_id, validate_request


def metadata(item: dict) -> dict | None:
    text = item.get("userGuidance") or "{}"
    candidates = [text]
    candidates += [line.split("补充说明：", 1)[1] for line in text.splitlines() if line.startswith("补充说明：")]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            value = parsed.get("clawinsight") or parsed.get("nightly")
            if not isinstance(value, dict) or value.get("schema") != 1:
                continue
            sig = value.get("signature")
            if not isinstance(sig, dict) or set(sig) != {"source", "cron_job", "component", "error_code", "operation_id"}:
                continue
            if any(not isinstance(v, str) for v in sig.values()):
                continue
            expected = root_id(str(item["ownerUserId"]), str(item["botId"]), sig)
            if value.get("root_id") != expected or not any(marker + expected in str(item.get("rootCauseSummary", "")) for marker in LEGACY_ROOT_MARKERS):
                continue
            return value
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def plan_verification(item: dict, lane: str, tasks: list[dict], *, complete: bool,
                      now: datetime, current_scope_verified: bool) -> dict:
    if lane not in {"standard", "open"}:
        raise ValueError("invalid verification lane")
    if lane == "standard" and (item.get("status") != "IN_PROGRESS" or not item.get("handledAt")):
        raise ValueError("not in standard verification state")
    if lane == "open" and (item.get("status") not in {"ACTIVE", "IN_PROGRESS"} or item.get("handledAt")):
        raise ValueError("not in open verification state")
    boundary = item.get("handledAt") if lane == "standard" else item.get("gmtModified")
    outcome, reason, relevant, recurring = "INSUFFICIENT_DATA", "missing observation boundary", [], []
    meta = metadata(item)
    if boundary:
        since = instant(str(boundary))
        relevant = [t for t in tasks if t["user_id"] == str(item["ownerUserId"])
                    and t["bot_id"] == str(item["botId"]) and instant(t["start_time"]) > since]
        # Without machine metadata, inspect the whole Owner+Bot scope. Absence of
        # errors in that complete scope also establishes absence of the original
        # error. Unrelated failures are uncertainty, never fabricated recurrence.
        matched = bool(meta)
        if meta:
            sig = meta["signature"]
            relevant = [t for t in relevant if t["source"] == sig["source"]
                        and sig["operation_id"] in t["operation_ids"]]
            recurring = [t for t in relevant if sig in t["signatures"].values()]
        failures = [t for t in relevant if t["signature_ids"] or t["is_complete"] == 0
                    or any(e.get("error_codes") for e in t.get("evidence", []))]
        successes = [t for t in relevant if t["is_complete"] == 1 and t not in failures]
        # A later successful execution of the SAME operation may supersede an
        # earlier failure. A successful unrelated task is not repair evidence.
        recovered = matched and successes and failures and max(
            instant(t["start_time"]) for t in successes) > max(instant(t["end_time"]) for t in failures)
        running = str(item.get("latestEvolveTaskStatus", "")).upper() in {"QUEUED", "PENDING", "RUNNING", "IN_PROGRESS"}
        if running:
            reason = "repair task is still running"
        elif recurring and not recovered:
            outcome, reason = "STILL_PRESENT", "original error persists in the latest comparable execution"
        elif not complete or not current_scope_verified:
            reason = "incomplete history or current source scope unavailable"
        elif now - since < timedelta(days=2 if lane == "standard" else 7):
            reason = "observation period not complete"
        elif failures and not recovered:
            reason = "errors remain in Owner+Bot logs; cannot establish that the original problem disappeared"
        elif any(t["is_complete"] != 1 for t in relevant if t not in failures):
            reason = "unfinished or inconclusive executions remain"
        else:
            outcome = "DISAPPEARED"
            reason = ("later comparable successful execution confirms recovery" if recovered else
                      "complete observation period without errors; zero new sessions is allowed")
    payload = {"improvementId": int(item["improvementId"]), "version": int(item["version"]),
               "outcome": outcome, "newSessionCount": len({t["session_id"] for t in relevant}) if meta else 0}
    if outcome == "DISAPPEARED" and payload["newSessionCount"] == 0 and lane == "standard":
        payload["allowZeroSession"] = True
    if outcome == "STILL_PRESENT":
        payload["lastRecurrenceAt"] = max(instant(t["end_time"]) for t in recurring).isoformat()
    request = {"kind": "verify", "lane": lane, "path": API_PREFIX + "/verification-results" + ("/open" if lane == "open" else ""),
               "key": f"verify-{lane}-{payload['improvementId']}-{payload['version']}", "payload": payload,
               "reason": reason, "checked_task_ids": [t["id"] for t in relevant]}
    validate_request(request)
    return request
