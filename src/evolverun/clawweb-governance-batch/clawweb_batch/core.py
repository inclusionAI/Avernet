"""Pure count sorting, identity boundaries and irreversible-action gates."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

CAPABILITY = frozenset({"CAPABILITY_BOUNDARY", "TOOL_FAILURE", "WORKFLOW_FAILURE", "CONFIG_MISSING",
                        "PERMISSION_NETWORK", "DATA_ISSUE", "OUTPUT_WRONG", "PARAMETER_ERROR"})
API_PREFIX = "/api/insight/v1/internal/governance"
ROOT_MARKER = "clawinsight-v1:"
LEGACY_ROOT_MARKERS = ("clawinsight-v1:", "nightly-v1:")


def instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # ODPS's unzoned timestamps use the configured source's Asia/Shanghai convention.
    return dt.replace(tzinfo=timezone(timedelta(hours=8))) if dt.tzinfo is None else dt


def compact_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()[:32]


def day_range(start: str, end: str) -> list[str]:
    a, b = datetime.strptime(start, "%Y%m%d"), datetime.strptime(end, "%Y%m%d")
    return [(a + timedelta(days=n)).strftime("%Y%m%d") for n in range((b - a).days + 1)]


def select_watermark(rows: list[dict], today: str, max_age: int, *, cron_only: bool = False) -> dict:
    by_day: dict[str, dict[int, dict]] = {}
    for row in rows:
        by_day.setdefault(row["dt"], {})[int(row["is_cron"])] = row
    # Both lanes are expected by this pipeline. Genuine zero lanes need an upstream manifest,
    # not a guessed zero substituted for an absent partition.
    required_lanes = {1} if cron_only else {0, 1}
    complete = [d for d, lanes in by_day.items() if required_lanes.issubset(lanes)
                and all(int(lanes[lane]["raw_sampled_cnt"]) > 0 for lane in required_lanes)]
    if not complete:
        raise ValueError("no date with both nonempty data lanes; publication is blocked")
    end = max(complete)
    age = (datetime.strptime(today, "%Y%m%d") - datetime.strptime(end, "%Y%m%d")).days
    if age < 0 or age > max_age:
        raise ValueError("latest paired data partitions are stale or future-dated")
    return {"end_date": end, "paired_days": sorted(complete), "age_days": age,
            "readiness": "CRON_PARTITIONS_ONLY" if cron_only else "PAIRED_PARTITIONS_ONLY", "upstream_completeness_verified": False}


def rank_counts(rows: list[dict], top: int, *, cron_only: bool = False) -> list[dict]:
    if cron_only:
        bots = {}
        for row in rows:
            if int(row["is_cron"]) != 1:
                continue
            w, n = float(row["weighted_cnt"]), int(row["raw_sampled_cnt"])
            if not math.isfinite(w) or w < 0 or n < 0:
                raise ValueError("invalid task counts")
            if row.get("task_complete_cate") in {"COMPLETED", "UNKNOWN"}:
                continue
            key = (str(row["user_id"]), str(row["bot_id"]))
            item = bots.setdefault(key, {"user_id":key[0], "bot_id":key[1], "is_cron":1,
                "weighted_cnt":0.0, "raw_sampled_cnt":0, "count_kind":"estimate",
                "ranking_scope":"user_bot_cron", "task_complete_cate":"ALL_FAILURES"})
            item["weighted_cnt"] += w
            item["raw_sampled_cnt"] += n
        selected = sorted((x for x in bots.values() if x["raw_sampled_cnt"] > 0),
            key=lambda x:(-x["weighted_cnt"],-x["raw_sampled_cnt"],x["user_id"],x["bot_id"]))[:top]
        return [{**x,"rank":i} for i,x in enumerate(selected,1)]
    merged: dict[tuple, dict] = {}
    for row in rows:
        key = (str(row["user_id"]), str(row["bot_id"]), int(row["is_cron"]), row["task_complete_cate"])
        w, n = float(row["weighted_cnt"]), int(row["raw_sampled_cnt"])
        if not math.isfinite(w) or w < 0 or n < 0 or key[2] not in (0, 1):
            raise ValueError("invalid task counts")
        value = merged.setdefault(key, dict(zip(("user_id", "bot_id", "is_cron", "task_complete_cate"), key),
                                            weighted_cnt=0.0, raw_sampled_cnt=0))
        value["weighted_cnt"] += w
        value["raw_sampled_cnt"] += n
    result = []
    for lane in (0, 1):
        candidates = [r for r in merged.values() if r["is_cron"] == lane
                      and r["task_complete_cate"] in CAPABILITY and r["raw_sampled_cnt"] > 0]
        candidates.sort(key=lambda r: (-(r["weighted_cnt"] if lane else r["raw_sampled_cnt"]),
                                       -r["raw_sampled_cnt"], r["user_id"], r["bot_id"], r["task_complete_cate"]))
        for i, row in enumerate(candidates[:top], 1):
            result.append({**row, "rank": i, "count_kind": "estimate" if lane else "observed"})
    return result


def root_id(owner: str, bot: str, signature: dict) -> str:
    return compact_hash([owner, bot, signature])


def has_root(item: dict, root: str) -> bool:
    summary = str(item.get("rootCauseSummary", ""))
    return any(marker + root in summary for marker in LEGACY_ROOT_MARKERS)


def rejection_time(item: dict) -> datetime | None:
    for key in ("rejectedAt", "adminRejectedAt", "updatedAt", "gmtModified"):
        value = item.get(key)
        if value:
            try:
                return instant(str(value))
            except (TypeError, ValueError):
                continue
    return None


def is_rejected(item: dict) -> bool:
    if str(item.get("adminReviewStatus", "")).upper() == "REJECTED":
        return True
    if item.get("rejectReasonCode") or item.get("rejectedBy") or item.get("rejectedAt"):
        return True
    return str(item.get("status", "")).upper() == "ARCHIVED" and bool(item.get("adminReviewReason"))


def validate_analysis_shape(proposal: dict, bundle: dict) -> dict:
    required = {"decision", "reason", "signature_id", "evidence_ids", "title", "root_cause",
                "suggested_action", "assignment_reason", "existing_improvement_id"}
    if not isinstance(proposal, dict) or set(proposal) != required:
        raise ValueError("analyst returned unknown/missing fields")
    if proposal["decision"] not in {"CREATE", "WATCH", "DROP"}:
        raise ValueError("invalid analyst decision")
    for k in required - {"evidence_ids", "existing_improvement_id"}:
        if not isinstance(proposal[k], str):
            raise ValueError(f"analyst {k} must be text")
    if not isinstance(proposal["evidence_ids"], list) or any(not isinstance(i, str) for i in proposal["evidence_ids"]):
        raise ValueError("analyst evidence_ids must be string list")
    existing = proposal["existing_improvement_id"]
    if existing is not None:
        ids = {str(x["improvementId"]) for x in bundle["existing_actions"]}
        if str(existing) not in ids or proposal["decision"] != "DROP":
            raise ValueError("analyst invented a dedup record or inconsistent decision")
    return proposal


def validate_analysis(proposal: dict, bundle: dict) -> dict:
    validate_analysis_shape(proposal, bundle)
    if proposal["decision"] != "CREATE":
        return proposal
    signatures = {s["id"]: s for s in bundle["signatures"]}
    if proposal["signature_id"] not in signatures:
        raise ValueError("CREATE must select an observed tool-result signature")
    known = {t["id"]: t for t in bundle["tasks"]}
    selected = proposal["evidence_ids"]
    if not selected or len(set(selected)) != len(selected) or any(i not in known for i in selected):
        raise ValueError("invalid evidence references")
    tasks = [known[i] for i in selected]
    scope = bundle["bucket"]
    if any(t["user_id"] != scope["user_id"] or t["bot_id"] != scope["bot_id"]
           or int(t["is_cron"]) != int(scope["is_cron"]) for t in tasks):
        raise ValueError("evidence crosses owner/bot/cron scope")
    if any(proposal["signature_id"] not in t["signature_ids"] or t["is_complete"] == 1 for t in tasks):
        raise ValueError("evidence does not match observed problem / task is marked completed")
    if len({t["session_id"] for t in tasks}) < 2:
        raise ValueError("CREATE requires two independent observed sessions, never estimated count")
    if bundle["truncated"]:
        raise ValueError("truncated evidence cannot create an actionable item")
    if any(not proposal[k].strip() for k in ("title", "root_cause", "suggested_action", "assignment_reason")):
        raise ValueError("CREATE requires a specific diagnosis and plan")
    latest = max(instant(t["end_time"]) for t in tasks)
    if max(t["dt"] for t in tasks) != bundle["as_of"]:
        raise ValueError("no recurrence on current data day; keep observing")
    signature = signatures[proposal["signature_id"]]["signature"]
    # A later successful comparable operation is counterevidence, not automatic recovery.
    if any(t["is_complete"] == 1 and instant(t["end_time"]) > latest
           and signature["operation_id"] in t["operation_ids"] for t in bundle["tasks"]):
        raise ValueError("newer comparable success requires further investigation")
    return proposal


def make_candidate(proposal: dict, bundle: dict, existing: list[dict], *,
                   rejection_cooldown_days: int = 15) -> dict:
    validate_analysis(proposal, bundle)
    if proposal["decision"] != "CREATE":
        raise ValueError("not a creation proposal")
    if type(rejection_cooldown_days) is not int or rejection_cooldown_days < 0:
        raise ValueError("invalid rejection cooldown")
    bucket = bundle["bucket"]
    signature = next(s["signature"] for s in bundle["signatures"] if s["id"] == proposal["signature_id"])
    root = root_id(bucket["user_id"], bucket["bot_id"], signature)
    same = [x for x in existing if has_root(x, root)]
    task_by_id = {t["id"]: t for t in bundle["tasks"]}
    latest_evidence = max(instant(task_by_id[i]["end_time"]) for i in proposal["evidence_ids"])
    for item in same:
        if item.get("status") in {"PENDING_ADMIN", "ACTIVE", "IN_PROGRESS"}:
            raise ValueError("same root already has an in-flight item")
        if not is_rejected(item):
            continue
        rejected_at = rejection_time(item)
        if rejected_at is None:
            raise ValueError("same root was rejected without a reliable timestamp")
        if latest_evidence <= rejected_at:
            raise ValueError("same root has no evidence newer than its rejection")
        if latest_evidence - rejected_at < timedelta(days=rejection_cooldown_days):
            raise ValueError("same root is inside the rejection cooldown")
    generation = max((int(x["improvementId"]) for x in same), default=0)
    meta = {"schema": 1, "root_id": root, "signature": signature}
    guidance = {"clawinsight": meta, "counts": {("bot_failure_count" if bucket.get("ranking_scope") == "user_bot_cron" else "category_count"): bucket["weighted_cnt"],
                "count_kind": bucket["count_kind"], "sampled_tasks": bucket["raw_sampled_cnt"],
                "verified_tasks": len(proposal["evidence_ids"])}, "as_of": bundle["as_of"]}
    tasks = {t["id"]: t for t in bundle["tasks"]}
    payload = {"ownerUserId": bucket["user_id"], "sourceOwnerUserId": bucket["user_id"],
               "botId": bucket["bot_id"], "title": proposal["title"],
               "sourceRuleId": "clawinsight.evidence.v1", "actionType": "ASSIGN_OWNER",
               "assignmentReason": proposal["assignment_reason"],
               "rootCauseSummary": f"[{ROOT_MARKER}{root}] {proposal['root_cause']}",
               "suggestedAction": proposal["suggested_action"],
               "userGuidance": json.dumps(guidance, ensure_ascii=False, sort_keys=True),
               "selectedTasks": [{"sessionId": tasks[i]["session_id"], "taskIndex": tasks[i]["task_index"]}
                                 for i in sorted(proposal["evidence_ids"])]}
    request = {"kind": "create", "path": API_PREFIX + "/actions", "key": f"clawinsight-{root}-{generation}",
               "payload": payload, "root_id": root}
    validate_request(request)
    return request


def validate_request(request: dict) -> None:
    payload = request["payload"]
    if request["kind"] == "create":
        allowed = {"ownerUserId", "sourceOwnerUserId", "botId", "title", "sourceRuleId", "actionType",
                   "assignmentReason", "rootCauseSummary", "suggestedAction", "userGuidance", "selectedTasks"}
        if set(payload) != allowed or payload["actionType"] != "ASSIGN_OWNER":
            raise ValueError("batch job only creates administrator-reviewed owner assignments")
        if request["path"] != API_PREFIX + "/actions":
            raise ValueError("invalid create endpoint")
        for key, maximum in {"ownerUserId": 128, "sourceOwnerUserId": 128, "botId": 128, "title": 256,
                             "sourceRuleId": 64, "assignmentReason": 1000, "rootCauseSummary": 1000,
                             "suggestedAction": 5000, "userGuidance": 5000}.items():
            if not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= maximum:
                raise ValueError(f"invalid {key} length")
        tasks = payload["selectedTasks"]
        if not 1 <= len(tasks) <= 50:
            raise ValueError("selectedTasks outside API bounds")
        identities = []
        for t in tasks:
            if set(t) != {"sessionId", "taskIndex"} or not isinstance(t["sessionId"], str) or not t["sessionId"]:
                raise ValueError("invalid selectedTask")
            if type(t["taskIndex"]) is not int or t["taskIndex"] < 0:
                raise ValueError("invalid task index")
            identities.append((t["sessionId"], t["taskIndex"]))
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate selectedTask")
    elif request["kind"] == "verify":
        if request["path"] not in {API_PREFIX + "/verification-results", API_PREFIX + "/verification-results/open"}:
            raise ValueError("invalid verification endpoint")
        if set(payload) - {"improvementId", "version", "outcome", "newSessionCount", "lastRecurrenceAt", "allowZeroSession"}:
            raise ValueError("verification cannot change state/action type")
        for k in ("improvementId", "version"):
            if type(payload[k]) is not int or payload[k] < 1:
                raise ValueError(f"invalid {k}")
        n = payload["newSessionCount"]
        if type(n) is not int or n < 0 or payload["outcome"] not in {"DISAPPEARED", "STILL_PRESENT", "INSUFFICIENT_DATA"}:
            raise ValueError("invalid verification outcome/count")
        if payload["outcome"] == "STILL_PRESENT" and n < 1:
            raise ValueError("recurrence requires an actual session")
        if "allowZeroSession" in payload and not (payload["allowZeroSession"] is True
                and request["path"] == API_PREFIX + "/verification-results"
                and payload["outcome"] == "DISAPPEARED" and n == 0):
            raise ValueError("zero-session confirmation is only valid for standard disappearance")
        if (payload["outcome"] == "DISAPPEARED" and n == 0
                and request["path"] == API_PREFIX + "/verification-results"
                and payload.get("allowZeroSession") is not True):
            raise ValueError("standard zero-session closure requires explicit confirmation")
        if payload["outcome"] == "STILL_PRESENT" and not payload.get("lastRecurrenceAt"):
            raise ValueError("recurrence needs timestamp")
    else:
        raise ValueError("unsupported write kind")
    if not isinstance(request["key"], str) or not 1 <= len(request["key"]) <= 128:
        raise ValueError("invalid idempotency key")
