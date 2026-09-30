from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawweb_batch.config import Config, load_config
from clawweb_batch.core import (API_PREFIX, make_candidate, rank_counts, select_watermark,
                                 validate_analysis, validate_request)
from clawweb_batch.evidence import extract_tasks, redact
from clawweb_batch.verification import plan_verification
from clawweb_batch.pipeline import run
from clawweb_batch.adapters.http import ClawWebHTTP
from clawweb_batch.adapters.nas import NASReader
from clawweb_batch.adapters.odps import PyODPSData
from clawweb_batch.artifacts import freeze_request, run_lock

SOURCE = "arca/arcaagentclaw/prod/prod_staff_u_openclaw_b/.openclaw/agents/main/sessions"
NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def session(sid="s1", day="20260928", *, code="SYSTEM_ERROR", complete=0, source=SOURCE, index=0):
    msgs = [{"role": "user", "content": "Fetch the report and return the actual result."},
            {"role": "assistant", "content": [{"type": "toolCall", "id": "c1", "name": "exec",
                                                "arguments": {"command": "python report.py"}}]},
            {"role": "toolResult", "toolCallId": "c1", "toolName": "exec", "isError": False,
             "content": [{"type": "text", "text": json.dumps({"success": True, "data": {"errorCode": code}})}]},
            {"role": "assistant", "content": "The query did not return the report."}]
    date = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return {"user_id": "u", "bot_id": "b", "session_id": sid, "dt": day,
            "start_time": date + " 08:00:00", "end_time": date + " 08:01:00", "sampling_group_key": "",
            "messages": json.dumps(msgs), "file_path": source + "/" + sid + ".jsonl", "bot_name": "Bot",
            "llm_tasks_json": json.dumps([{"task_index": index, "message_range": [0, 4], "is_complete": complete,
                                          "task_failure_class": "TOOL_FAILURE", "task_description": "Fetch report"}])}


def bucket(lane=0, count=12, owner="u", bot="b", category="TOOL_FAILURE"):
    return {"user_id": owner, "bot_id": bot, "is_cron": lane, "task_complete_cate": category,
            "weighted_cnt": float(count), "raw_sampled_cnt": count}


def bundle():
    tasks, warnings = extract_tasks([session(), session("s2")], 100000)
    sigs = {k: v for t in tasks for k, v in t["signatures"].items()}
    return {"as_of": "20260928", "bucket": {**bucket(), "count_kind": "observed"}, "tasks": tasks,
            "truncated": bool(warnings), "signatures": [{"id": k, "signature": v} for k, v in sigs.items()],
            "existing_actions": []}


def proposal(b):
    return {"decision": "CREATE", "reason": "Two independent business failures", "signature_id": b["signatures"][0]["id"],
            "evidence_ids": [t["id"] for t in b["tasks"]], "title": "Report query fails internally",
            "root_cause": "The report operation returns an internal business error.",
            "suggested_action": "Check the query implementation; verify with the same report operation after repair.",
            "assignment_reason": "Owner can coordinate this report dependency.", "existing_improvement_id": None}


class PurePolicyTests(unittest.TestCase):
    def test_rank_counts_not_scores_and_lanes_separate(self):
        rows = [bucket(0, 10), bucket(1, 100), bucket(0, 20, bot="c"), bucket(1, 2, bot="d")]
        self.assertEqual([(x["is_cron"], x["bot_id"]) for x in rank_counts(rows, 1)], [(0, "c"), (1, "b")])
        rows.append(bucket(0, 11))
        self.assertEqual(rank_counts(rows, 1)[0]["raw_sampled_cnt"], 21)

    def test_rank_rejects_nonfinite_count(self):
        value = bucket(); value["weighted_cnt"] = float("nan")
        with self.assertRaises(ValueError): rank_counts([value], 5)

    def test_watermark_needs_both_lanes_and_not_stale(self):
        rows = [{"dt": "20260928", "is_cron": 0, "raw_sampled_cnt": 3},
                {"dt": "20260927", "is_cron": 0, "raw_sampled_cnt": 3},
                {"dt": "20260927", "is_cron": 1, "raw_sampled_cnt": 3}]
        self.assertEqual(select_watermark(rows, "20260929", 3)["end_date"], "20260927")
        with self.assertRaises(ValueError): select_watermark(rows, "20261001", 3)

    def test_nested_business_failure_overrides_outer_transport_success(self):
        tasks, warnings = extract_tasks([session()], 100000)
        self.assertFalse(warnings)
        self.assertIn("SYSTEM_ERROR", [s["error_code"] for s in tasks[0]["signatures"].values()])

    def test_reading_skill_error_example_not_runtime_failure(self):
        row = session(); messages = json.loads(row["messages"])
        messages[1]["content"][0]["name"] = "read"
        messages[2]["toolName"] = "read"
        row["messages"] = json.dumps(messages)
        self.assertFalse(extract_tasks([row], 100000)[0][0]["signature_ids"])

    def test_unpaired_tool_return_not_root_proof(self):
        row = session(); messages = json.loads(row["messages"]); messages[2]["toolCallId"] = "missing"
        row["messages"] = json.dumps(messages)
        self.assertFalse(extract_tasks([row], 100000)[0][0]["signature_ids"])

    def test_known_odps_normalization_single_adjacent_call(self):
        row = session(); messages = json.loads(row["messages"])
        call = messages[1]["content"][0]
        call.pop("id"); call.pop("type"); messages[2].pop("toolCallId")
        row["messages"] = json.dumps(messages)
        task = extract_tasks([row], 100000)[0][0]
        self.assertTrue(task["signature_ids"])
        result = next(x for x in task["evidence"] if x["role"] == "toolResult")
        self.assertEqual(result["pairing"], "single_adjacent_normalized_call")

    def test_normalized_parallel_calls_are_not_guessed(self):
        row = session(); messages = json.loads(row["messages"])
        call = messages[1]["content"][0]
        call.pop("id"); call.pop("type"); messages[2].pop("toolCallId")
        messages[1]["content"].append(copy.deepcopy(call))
        row["messages"] = json.dumps(messages)
        self.assertFalse(extract_tasks([row], 100000)[0][0]["signature_ids"])

    def test_no_positional_guess_for_broken_task_boundaries(self):
        row = session(); row["llm_tasks_json"] = '[{"task_index":0}]'
        tasks, warnings = extract_tasks([row], 100000)
        self.assertFalse(tasks); self.assertTrue(warnings)

    def test_preserves_nonzero_task_index(self):
        tasks, _ = extract_tasks([session(index=3)], 100000)
        self.assertEqual(tasks[0]["task_index"], 3)

    def test_partial_session_not_evidence_success(self):
        tasks, warnings = extract_tasks([session()], 10)
        self.assertEqual(tasks, []); self.assertTrue(warnings)

    def test_create_exact_root_and_required_two_sessions(self):
        b = bundle(); p = proposal(b)
        request = make_candidate(p, b, [])
        self.assertEqual(request["payload"]["actionType"], "ASSIGN_OWNER")
        self.assertIn("clawinsight-v1:", request["payload"]["rootCauseSummary"])
        p["evidence_ids"] = p["evidence_ids"][:1]
        with self.assertRaises(ValueError): make_candidate(p, b, [])

    def test_rejected_root_uses_cooldown_and_legacy_marker_compatibility(self):
        b = bundle(); p = proposal(b)
        prior = make_candidate(p, b, [])
        existing = {**prior["payload"], "improvementId": 8, "adminReviewStatus": "REJECTED",
                    "rejectedAt": "2026-09-25T00:00:00+08:00"}
        with self.assertRaisesRegex(ValueError, "cooldown"):
            make_candidate(p, b, [existing], rejection_cooldown_days=15)
        legacy = dict(existing)
        legacy["rootCauseSummary"] = legacy["rootCauseSummary"].replace("clawinsight-v1:", "nightly-v1:")
        new_request = make_candidate(p, b, [legacy], rejection_cooldown_days=2)
        self.assertTrue(new_request["key"].startswith("clawinsight-"))

    def test_foreign_or_hallucinated_evidence_rejected(self):
        b = bundle(); p = proposal(b); p["evidence_ids"][0] = "invented:1"
        with self.assertRaises(ValueError): validate_analysis(p, b)

    def test_model_cannot_enable_automatic_execution(self):
        b = bundle(); p = proposal(b); p["actionType"] = "DIRECT_EVOLUTION"
        with self.assertRaises(ValueError): validate_analysis(p, b)
        request = make_candidate(proposal(b), b, []); request["payload"]["actionType"] = "DIRECT_EVOLUTION"
        with self.assertRaises(ValueError): validate_request(request)

    def test_same_owner_other_root_not_suppressed(self):
        b = bundle(); p = proposal(b)
        other = [{"improvementId": 1, "status": "ACTIVE", "rootCauseSummary": "other problem"}]
        make_candidate(p, b, other)
        existing = {"improvementId": 1, "status": "ACTIVE", "rootCauseSummary": make_candidate(p, b, [])["payload"]["rootCauseSummary"]}
        with self.assertRaises(ValueError): make_candidate(p, b, [existing])

    def test_stable_key_ignores_wording_and_sample_order(self):
        b = bundle(); p = proposal(b); first = make_candidate(p, b, [])
        p["title"] = "Different wording"; p["evidence_ids"].reverse()
        second = make_candidate(p, b, [])
        self.assertEqual(first["key"], second["key"])

    def test_sources_not_merged(self):
        b = bundle(); p = proposal(b)
        desktop, _ = extract_tasks([session("d", source="aidesktop/user/b/agents/main/sessions")], 100000)
        b["tasks"].append(desktop[0]); p["evidence_ids"][1] = desktop[0]["id"]
        with self.assertRaises(ValueError): validate_analysis(p, b)

    def test_newer_comparable_success_blocks_creation(self):
        b = bundle(); p = proposal(b)
        success, _ = extract_tasks([session("later", "20260929", code="SUCCESS", complete=1)], 100000)
        b["tasks"].extend(success)
        with self.assertRaises(ValueError): validate_analysis(p, b)

    def test_redacts_credentials_and_identifiers(self):
        text = 'Authorization: Bearer abc-secret\napi_key="x"\nhttps://example.com?a=secret\n13812345678 a@example.com'
        clean = redact(text)
        for secret in ("abc-secret", '"x"', "a=secret", "13812345678", "a@example.com"):
            self.assertNotIn(secret, clean)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        b = bundle(); p = make_candidate(proposal(b), b, [])["payload"]
        self.item = {**p, "improvementId": 9, "version": 2, "status": "IN_PROGRESS", "handledAt": "2026-09-20T00:00:00Z"}
        self.tasks = b["tasks"]

    def plan(self, tasks, **kw):
        return plan_verification(self.item, "standard", tasks, complete=kw.get("complete", True), now=NOW,
                                 current_scope_verified=kw.get("scope", True))

    def test_recurrence_is_still_present_not_auto_closed(self):
        p = self.plan(self.tasks)["payload"]
        self.assertEqual(p["outcome"], "STILL_PRESENT"); self.assertEqual(p["newSessionCount"], 2)

    def test_no_traffic_never_closes(self):
        self.assertEqual(self.plan([])["payload"]["outcome"], "INSUFFICIENT_DATA")

    def test_complete_scoped_success_and_observation_can_pass(self):
        tasks, _ = extract_tasks([session("fixed", code="SUCCESS", complete=1)], 100000)
        self.assertEqual(self.plan(tasks)["payload"]["outcome"], "DISAPPEARED")
        self.assertEqual(self.plan(tasks, complete=False)["payload"]["outcome"], "INSUFFICIENT_DATA")
        self.assertEqual(self.plan(tasks, scope=False)["payload"]["outcome"], "INSUFFICIENT_DATA")

    def test_running_repair_stays_open(self):
        self.item["latestEvolveTaskStatus"] = "RUNNING"
        self.assertEqual(self.plan(self.tasks)["payload"]["outcome"], "INSUFFICIENT_DATA")

    def test_legacy_item_not_guessed(self):
        self.item["userGuidance"] = "legacy prose"
        p = self.plan(self.tasks)["payload"]
        self.assertEqual(p["outcome"], "INSUFFICIENT_DATA"); self.assertEqual(p["newSessionCount"], 0)

    def test_server_guidance_wrapper_preserves_metadata(self):
        self.item["userGuidance"] = "根因：report fails\n补充说明：" + self.item["userGuidance"]
        self.assertEqual(self.plan(self.tasks)["payload"]["outcome"], "STILL_PRESENT")

    def test_cross_boundary_session_not_counted(self):
        self.item["handledAt"] = "2026-09-28T00:00:30Z"
        self.assertEqual(self.plan(self.tasks)["payload"]["newSessionCount"], 0)

    def test_open_requires_seven_days(self):
        self.item.update(status="ACTIVE", handledAt=None, gmtModified="2026-09-25T00:00:00Z")
        tasks, _ = extract_tasks([session("fixed", code="SUCCESS", complete=1)], 100000)
        p = plan_verification(self.item, "open", tasks, complete=True, now=NOW, current_scope_verified=True)
        self.assertEqual(p["payload"]["outcome"], "INSUFFICIENT_DATA")


class FakeData:
    def daily_counts(self, start, end):
        return [{"dt": "20260928", "is_cron": lane, "raw_sampled_cnt": 10} for lane in (0, 1)]
    def ranking(self, start, end): return [bucket()]
    def sessions(self, owner, bot, start, end, limit): return [session(), session("s2")]


class FakeCenter:
    def __init__(self): self.writes = []; self.reads = []
    def actions(self, owner, bot): self.reads.append((owner, bot)); return []
    def verification_candidates(self, lane, limit):
        return [self.detail(7)] if lane == "standard" else []
    def detail(self, item_id):
        b = bundle(); payload = make_candidate(proposal(b), b, [])["payload"]
        return {**payload, "improvementId": 7, "version": 2, "status": "IN_PROGRESS",
                "handledAt": "2026-09-20T00:00:00Z"}
    def write(self, path, payload, key):
        self.writes.append((path, payload, key)); return {"improvementId": 11, "status": "PENDING_ADMIN"}


class FakeAnalyst:
    def __init__(self): self.calls = 0
    def review(self, evidence):
        self.calls += 1
        return proposal(evidence)


class FakeNAS:
    def available(self): return [{"available": True}]
    def inspect(self, tasks, owner, bot): return []
    def scope_root(self, *args): return None
    def scan_since(self, *args): return {"tasks": [], "complete": False, "warnings": []}


class RuntimeTests(unittest.TestCase):
    def config(self, root, allow=False):
        return Config("project", "https://example.invalid", root / "out", root / "state", (root,), root / "llm.json", allow_writes=allow)

    def test_dry_run_reaches_both_request_boundaries_without_writes(self):
        with tempfile.TemporaryDirectory() as d:
            center = FakeCenter()
            result = run(self.config(Path(d)), FakeData(), center, FakeAnalyst(), FakeNAS(), now=NOW, apply=False)
            self.assertEqual(result["status"], "SUCCEEDED", result["errors"])
            self.assertEqual({r["kind"] for r in result["requests"]}, {"create", "verify"})
            self.assertFalse(center.writes); self.assertEqual(result["external_writes"], 0)
            self.assertTrue((Path(result["output_dir"]) / "requests.json").is_file())

    def test_unchanged_evidence_uses_cross_run_analysis_cache(self):
        with tempfile.TemporaryDirectory() as d:
            analyst = FakeAnalyst()
            cfg = self.config(Path(d))
            first = run(cfg, FakeData(), FakeCenter(), analyst, FakeNAS(), now=NOW, apply=False)
            second = run(cfg, FakeData(), FakeCenter(), analyst, FakeNAS(), now=NOW, apply=False)
            self.assertEqual(first["analysis_cache_hits"], 0)
            self.assertEqual(second["analysis_cache_hits"], 1)
            self.assertEqual(analyst.calls, 1)

    def test_apply_requires_two_independent_opt_ins(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PermissionError):
                run(self.config(Path(d)), FakeData(), FakeCenter(), FakeAnalyst(), FakeNAS(), now=NOW, apply=True)

    def test_failure_does_not_become_empty_success_or_write(self):
        class Failed(FakeCenter):
            def actions(self, owner, bot): raise RuntimeError("connection failed")
        with tempfile.TemporaryDirectory() as d:
            center = Failed()
            result = run(self.config(Path(d), True), FakeData(), center, FakeAnalyst(), FakeNAS(), now=NOW, apply=True)
            self.assertEqual(result["status"], "FAILED"); self.assertFalse(center.writes)

    def test_live_mock_calls_only_allowlisted_final_endpoints(self):
        with tempfile.TemporaryDirectory() as d:
            center = FakeCenter()
            result = run(self.config(Path(d), True), FakeData(), center, FakeAnalyst(), FakeNAS(), now=NOW, apply=True)
            self.assertEqual(result["status"], "SUCCEEDED", result["errors"])
            self.assertEqual(len(center.writes), 2)
            self.assertEqual({x[0] for x in center.writes}, {API_PREFIX + "/actions", API_PREFIX + "/verification-results"})

    def test_insufficient_data_is_audited_without_refreshing_open_clock(self):
        class Legacy(FakeCenter):
            def detail(self, item_id):
                value = super().detail(item_id); value["userGuidance"] = "legacy prose"; return value
        with tempfile.TemporaryDirectory() as d:
            center = Legacy()
            result = run(self.config(Path(d), True), FakeData(), center, FakeAnalyst(), FakeNAS(), now=NOW, apply=True)
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(len(center.writes), 1)
            self.assertEqual(result["verification"][0]["payload"]["outcome"], "INSUFFICIENT_DATA")
            self.assertTrue(any(x.get("skipped") == "insufficient-data-observation-only" for x in result["receipts"]))

    def test_outbox_freezes_exact_body_and_lock_refuses_concurrent_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path = root / "request.json"
            first = {"key": "k", "payload": {"a": 1}}
            freeze_request(path, first)
            self.assertEqual(freeze_request(path, {"key": "k", "payload": {"a": 2}}), first)
            with run_lock(root):
                with self.assertRaises(FileExistsError):
                    with run_lock(root): pass

    def test_http_dry_run_gate_blocks_before_transport(self):
        class HTTP:
            def request(self, *a, **kw): raise AssertionError("must not be called")
        with self.assertRaises(PermissionError):
            ClawWebHTTP("https://example.invalid", HTTP(), False).write(API_PREFIX + "/actions", {}, "k")

    def test_http_pagination_checks_scope(self):
        class HTTP:
            def request(self, *a, **kw): return {"items": [{"ownerUserId": "other", "botId": "b"}]}
        with self.assertRaises(ValueError): ClawWebHTTP("https://example.invalid", HTTP(), False).actions("u", "b")

    def test_sql_only_read_and_bound_parameters(self):
        obj = PyODPSData(None, "project")
        with self.assertRaises(ValueError): obj._read("DELETE FROM example")
        with self.assertRaises(ValueError): obj.sessions("u';DROP TABLE x;--", "b", "20260901", "20260902", 3)

    def test_nas_does_not_substitute_server_config_for_desktop(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "prod_staff_u_openclaw_b").mkdir()
            reader = NASReader((root,))
            self.assertIsNone(reader.scope_root("aidesktop/prod_staff_u_openclaw_b/agents/main", "u", "b"))
            self.assertIsNotNone(reader.scope_root(SOURCE, "u", "b"))
            self.assertIsNone(reader.scope_root(SOURCE, "other", "b"))

    def test_config_unknown_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text())
            example["silently_execute"] = True; path.write_text(json.dumps(example))
            with self.assertRaises(ValueError): load_config(path)


if __name__ == "__main__":
    unittest.main()
