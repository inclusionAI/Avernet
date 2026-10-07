"""Application orchestration over declared adapters; no SQL/HTTP/LLM decisions here."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .artifacts import freeze_request, read_json, write_json
from .config import Config
from .contracts import Analyst, DataSource, EffectCenter, EvidenceStore
from .core import (compact_hash, day_range, has_root, instant, make_candidate,
                   rank_counts, select_watermark, validate_analysis, validate_request)
from .evidence import extract_tasks, redact
from .verification import metadata, plan_verification


def review_history(items: list[dict]) -> list[dict]:
    keys = ("improvementId", "ownerUserId", "botId", "title", "status", "adminReviewStatus",
            "adminReviewReason", "rejectReasonCode", "rejectedAt", "rejectedBy", "rootCauseSummary",
            "createdAt", "updatedAt")
    return [{k: redact(str(x[k])) if isinstance(x.get(k), str) else x.get(k) for k in keys} for x in items]


def representative_tasks(tasks: list[dict], category: str, lane: int) -> list[dict]:
    relevant = [t for t in tasks if int(t["is_cron"]) == lane]
    failed = [t for t in relevant if (category == "ALL_FAILURES" or t["failure_class"] == category) and t["signature_ids"]]
    failed.sort(key=lambda t: (instant(t["end_time"]), t["id"]), reverse=True)
    chosen = failed[:4]
    chosen += sorted(relevant, key=lambda t: (instant(t["end_time"]), t["id"]), reverse=True)[:2]
    chosen += [t for t in relevant if t["is_complete"] == 1][:2]
    return list({t["id"]: t for t in chosen}.values())


def run(cfg: Config, source: DataSource, center: EffectCenter, analyst: Analyst, nas: EvidenceStore,
        *, now: datetime, apply: bool, top: int | None = None, end_date: str | None = None) -> dict:
    if apply and not cfg.allow_writes:
        raise PermissionError("--apply also requires deployment allow_writes=true")
    today = now.strftime("%Y%m%d")
    lookup_start = (now - timedelta(days=cfg.lookback_days - 1)).strftime("%Y%m%d")
    daily = source.daily_counts(lookup_start, today)
    watermark = select_watermark(daily, today, cfg.max_data_age_days, cron_only=cfg.cron_only)
    if end_date:
        if apply and end_date != watermark["end_date"]:
            raise ValueError("historical replay is dry-run only")
        if end_date not in watermark["paired_days"] or end_date > watermark["end_date"]:
            raise ValueError("requested date is not a paired available partition")
        watermark["end_date"] = end_date
    end = watermark["end_date"]
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=cfg.window_days - 1)).strftime("%Y%m%d")
    watermark["start_date"] = start
    watermark["missing_days"] = sorted(set(day_range(start, end)) - set(watermark["paired_days"]))
    output = cfg.output_dir / (end + ("-apply" if apply else "-dry-run"))
    output.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cfg.state_dir / "analysis-cache.json"
    cache_doc = read_json(cache_path, {"schema": 1, "entries": {}})
    cache_entries = cache_doc.get("entries", {}) if isinstance(cache_doc, dict) else {}
    if not isinstance(cache_entries, dict):
        cache_entries = {}
    ranked = rank_counts(source.ranking(start, end), top or cfg.top_per_lane, cron_only=cfg.cron_only)
    report = {"schema": 1, "mode": "apply" if apply else "dry-run", "started_at": now.isoformat(),
              "watermark": watermark, "nas": nas.available(), "ranking": ranked,
              "governance": [], "verification": [], "requests": [], "receipts": [],
              "errors": [], "external_writes": 0, "analysis_cache_hits": 0,
              "analysis_cache_writes": 0, "output_dir": str(output)}
    write_json(output / "run.json", report)
    cache, planned_roots = {}, set()
    for number, bucket in enumerate(ranked):
        scope = (bucket["user_id"], bucket["bot_id"])
        print(f"[governance] {number + 1}/{len(ranked)} owner={bucket['user_id']} bot={bucket['bot_id']} cron={bucket['is_cron']} estimated_failures={bucket['weighted_cnt']}", flush=True)
        try:
            if scope not in cache:
                rows = source.sessions(*scope, start, end, cfg.sessions_per_bot)
                tasks, warnings = extract_tasks(rows[:cfg.sessions_per_bot], cfg.max_message_bytes)
                if any((t["user_id"], t["bot_id"]) != scope for t in tasks):
                    raise ValueError("cross-owner/bot source data")
                cache[scope] = tasks, warnings, len(rows) > cfg.sessions_per_bot, center.actions(*scope)
            tasks, warnings, sampled, existing = cache[scope]
            chosen = representative_tasks(tasks, bucket["task_complete_cate"], bucket["is_cron"])
            signatures = {k: v for t in chosen for k, v in t["signatures"].items()}
            bundle = {"schema": 1, "as_of": end, "bucket": bucket, "window_missing_days": watermark["missing_days"],
                      "sampled_session_history": sampled, "truncated": bool(warnings), "warnings": warnings,
                      "signatures": [{"id": k, "signature": v} for k, v in sorted(signatures.items())],
                      "tasks": chosen, "current_config": nas.inspect(chosen, *scope),
                      "existing_actions": review_history(existing)}
            evidence_file = f"evidence/{number + 1:03d}-{compact_hash(scope)}.json"
            write_json(output / evidence_file, bundle)
            cache_key = compact_hash({
                "as_of": bundle["as_of"], "bucket": bucket,
                "tasks": [(t["id"], t["end_time"], sorted(t["signature_ids"])) for t in chosen],
                "config": bundle["current_config"], "existing": bundle["existing_actions"],
            })
            cached = cache_entries.get(cache_key)
            if isinstance(cached, dict):
                result = cached
                report["analysis_cache_hits"] += 1
            elif not signatures:
                result = {"decision": "WATCH", "reason": "no grounded runtime signature in representative evidence"}
            else:
                result = analyst.review(bundle)
                try:
                    validate_analysis(result, bundle)
                except ValueError as exc:
                    result = {"decision": "WATCH", "reason": f"mechanical gate: {exc}", "proposed": result}
            if not cached:
                cache_entries[cache_key] = result
                while len(cache_entries) > 256:
                    cache_entries.pop(next(iter(cache_entries)))
                report["analysis_cache_writes"] += 1
            if result["decision"] == "CREATE":
                try:
                    request = make_candidate(result, bundle, existing,
                                             rejection_cooldown_days=cfg.rejection_cooldown_days)
                    if request["root_id"] in planned_roots:
                        raise ValueError("same root already planned in this batch")
                    planned_roots.add(request["root_id"])
                    report["requests"].append(request)
                except ValueError as exc:
                    result = {"decision": "WATCH", "reason": f"dedup/contract gate: {exc}", "proposed": result}
            report["governance"].append({"bucket": bucket, "evidence_file": evidence_file, "result": result})
        except Exception as exc:
            # Failure is not 'no candidates': persist stage+type, return nonzero at end.
            report["errors"].append({"stage": "governance", "bucket_index": number, "error_type": type(exc).__name__,
                                      "message": safe_error(exc)})
        write_json(output / "run.json", report)
    write_json(cache_path, {"schema": 1, "entries": cache_entries})
    for lane in ("standard", "open"):
        try:
            items = center.verification_candidates(lane, cfg.verification_limit)
        except Exception as exc:
            report["errors"].append({"stage": f"verification-list-{lane}", "error_type": type(exc).__name__, "message": safe_error(exc)})
            continue
        report.setdefault("verification_queues", {})[lane] = {"selected": len(items), "limit": cfg.verification_limit,
                                                              "possibly_more": len(items) >= cfg.verification_limit}
        for listed in items:
            try:
                item = dict(listed)  # Queue is the authorized contract; protected action-detail is not required.
                meta, boundary = metadata(item), item.get("handledAt") if lane == "standard" else item.get("gmtModified")
                tasks, complete, scope_verified, warnings = [], False, False, []
                owner, bot = str(item["ownerUserId"]), str(item["botId"])
                if boundary:
                    # Even legacy items receive a bounded raw-log review. Without a trustworthy
                    # root binding those findings are audit evidence, never an auto-close decision.
                    boundary_day = instant(str(boundary)).strftime("%Y%m%d")
                    since = max(boundary_day, start) if not meta else boundary_day
                    if since <= end and (datetime.strptime(end, "%Y%m%d") - datetime.strptime(since, "%Y%m%d")).days <= 30:
                        rows = source.sessions(owner, bot, since, end, cfg.verification_sessions)
                        tasks, warnings = extract_tasks(rows[:cfg.verification_sessions], cfg.max_message_bytes)
                        if any(t["user_id"] != owner or t["bot_id"] != bot for t in tasks):
                            raise ValueError("verification evidence crosses owner/bot scope")
                        covered = not warnings and len(rows) <= cfg.verification_sessions
                        if meta:
                            sig = meta["signature"]
                            scope_verified = nas.scope_root(sig["source"], owner, bot) is not None
                            day_end = datetime.strptime(end, "%Y%m%d").strftime("%Y-%m-%dT23:59:59+08:00")
                            gap_start = (instant(day_end) if end < today else instant(now.strftime("%Y-%m-%dT00:00:00+08:00"))).isoformat()
                            gap = nas.scan_since(sig["source"], owner, bot, gap_start, cfg.max_message_bytes)
                            latest = {t["session_id"]: max(instant(x["end_time"]) for x in tasks if x["session_id"] == t["session_id"]) for t in tasks}
                            for t in gap["tasks"]:
                                if t["session_id"] not in latest or instant(t["end_time"]) > latest[t["session_id"]]:
                                    tasks.append(t)
                            warnings += gap["warnings"]
                            complete = covered and gap["complete"] and not (set(day_range(since, end)) - set(watermark["paired_days"]))
                evidence_file = f"verification-evidence/{lane}-{item['improvementId']}.json"
                write_json(output / evidence_file, {"schema": 1, "improvementId": item["improvementId"],
                    "version": item["version"], "title": redact(str(item.get("title", ""))),
                    "root_summary": redact(str(item.get("rootCauseSummary", ""))), "boundary": boundary,
                    "legacy_root": meta is None, "coverage_complete": complete, "warnings": warnings,
                    "tasks": tasks})
                request = plan_verification(item, lane, tasks, complete=complete, now=now,
                                            current_scope_verified=scope_verified)
                request["evidence_file"] = evidence_file
                request["examined_sessions"] = len({t["session_id"] for t in tasks})
                report["verification"].append(request)
                report["requests"].append(request)
            except Exception as exc:
                report["errors"].append({"stage": f"verification-{lane}", "improvementId": listed.get("improvementId"),
                                          "error_type": type(exc).__name__, "message": safe_error(exc)})
    # No mutations occur when any required read/analysis has failed.
    if apply and report["errors"]:
        report["write_status"] = "BLOCKED_BY_ERRORS"
    elif apply:
        apply_requests(report, cfg, center, output)
    else:
        report["write_status"] = "DRY_RUN_NO_WRITES"
    report["status"] = "FAILED" if report["errors"] else "SUCCEEDED"
    write_json(output / "requests.json", report["requests"])
    write_json(output / "run.json", report)
    write_summary(output / "review.md", report)
    return report


def safe_error(exc: Exception) -> str:
    # Driver exceptions can include signed URLs or authentication headers. Never serialize them.
    if isinstance(exc, (ValueError, PermissionError)):
        return redact(str(exc))[:240]
    if isinstance(exc, RuntimeError) and str(exc).startswith(("HTTP_", "HTTP transport")):
        return str(exc)[:100]
    return "external operation failed; inspect protected runtime diagnostics"


def apply_requests(report: dict, cfg: Config, center: EffectCenter, output: Path) -> None:
    for request in report["requests"]:
        try:
            validate_request(request)
            if request["kind"] == "verify" and request["payload"]["outcome"] == "INSUFFICIENT_DATA":
                report["receipts"].append({"key": request["key"], "skipped": "insufficient-data-observation-only"})
                continue  # Do not refresh gmtModified every night and restart the open observation window.
            if request["kind"] == "create":
                p = request["payload"]
                current = center.actions(p["ownerUserId"], p["botId"])
                if any(has_root(i, request["root_id"])
                       and i.get("status") in {"PENDING_ADMIN", "ACTIVE", "IN_PROGRESS"} for i in current):
                    report["receipts"].append({"key": request["key"], "skipped": "same-root-existing"})
                    continue
            else:
                p = request["payload"]
                queue = center.verification_candidates(request["lane"], 200)
                matches = [i for i in queue if int(i["improvementId"]) == p["improvementId"]]
                if len(matches) != 1:
                    raise ValueError("verification item left the selected queue; re-run selection")
                item = matches[0]
                if item["version"] != p["version"]:
                    raise ValueError("verification version changed; re-run reads, no blind retry")
                lane = request["lane"]
                if (lane == "standard" and (item.get("status") != "IN_PROGRESS" or not item.get("handledAt"))) or (
                        lane == "open" and (item.get("status") not in {"ACTIVE", "IN_PROGRESS"} or item.get("handledAt"))):
                    raise ValueError("verification state changed")
            frozen = freeze_request(cfg.state_dir / "outbox" / (request["key"] + ".json"), request)
            # Freeze exact payload before dispatch so interrupted runs cannot rewrite a used key.
            receipt = center.write(frozen["path"], frozen["payload"], frozen["key"])
            report["external_writes"] += 1
            report["receipts"].append({"key": request["key"], "receipt": receipt})
            write_json(cfg.state_dir / "receipts" / (request["key"] + ".json"), receipt)
            write_json(output / "run.json", report)
        except Exception as exc:
            report["errors"].append({"stage": "apply", "key": request["key"], "error_type": type(exc).__name__,
                                      "message": safe_error(exc)})
            report["write_status"] = "STOPPED_NO_RETRY"
            return
    report["write_status"] = "APPLIED"


def write_summary(path: Path, report: dict) -> None:
    lines = ["# ClawInsight Governance / Verification Review", "", f"- 运行结果：{report['status']}", f"- 模式：{report['mode']}",
             f"- 数据日期：{report['watermark']['end_date']}", f"- 外部写入：{report['external_writes']}",
             f"- 分析缓存命中/写入：{report.get('analysis_cache_hits', 0)}/{report.get('analysis_cache_writes', 0)}",
             f"- 窗口缺口：{', '.join(report['watermark']['missing_days']) or '无'}",
             "- 数量仅用于排序；Cron为估算数，证据为实际抽查Task。数据分区存在不证明全链路完整。", "", "## 治理候选"]
    for x in report["governance"]:
        b, r = x["bucket"], x["result"]
        lines += [f"### {r['decision']} — {r.get('title') or b['task_complete_cate']}",
                  f"- Owner/Bot：{b['user_id']} / {b['bot_id']}",
                  f"- 分类数量：{b['weighted_cnt']:.2f}（{b['count_kind']}）；实际样本：{b['raw_sampled_cnt']}",
                  f"- 理由：{r.get('reason', '')}", f"- 证据：{x['evidence_file']}"]
        if r.get("suggested_action"):
            lines += [f"- 方案：{r['suggested_action']}"]
    lines += ["", "## 验收"]
    for r in report["verification"]:
        p = r["payload"]
        lines += [f"- #{p['improvementId']} / {r['lane']}：{p['outcome']}；已检查Session={r.get('examined_sessions', 0)}，已确认相关Session={p['newSessionCount']}；{r['reason']}"]
    lines += ["", "## 限制", "- 历史项没有版本化根因指纹时保留打开，不猜测匹配。",
              "- 离线数据与精确来源NAS的补充日志不能覆盖到当前时间时，不自动确认问题消失；无流量不等于已修复。",
              "- 请求已做客户端契约校验；dry-run没有调用写接口，不证明服务端已接受创建/验收。",
              "- ASSIGN_OWNER保证进入人工审核路径；本入口不调用审批、修复、停用、通知或强制关闭接口。",
              "", "## 错误", json.dumps(report["errors"], ensure_ascii=False, indent=2)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
