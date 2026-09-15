#!/usr/bin/env python3
"""Golden-scenario calibration for ClawEvolve's three candidate gates.

Reports reject/pass precision, recall, balanced accuracy and false-reject rate.
The corpus combines controlled adversarial scenarios with selected production
artifact replays whose expected decisions are manually evidence-labeled.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from argparse import Namespace
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
REPLAY_PATH = ROOT / "scripts/replay_candidate_gate.py"


def load(path: Path, name: str):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

MOD = load(HANDLER, "gate_calibration_target")
REPLAY = load(REPLAY_PATH, "gate_calibration_replay")


def report(path: Path, tasks: dict[str, dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tasks": []}
    for tid, data in tasks.items():
        payload["tasks"].append({"task_id": tid, "grading": {"runs": [{"score": data.get("score", 0), "breakdown": data.get("breakdown", {})}]}})
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def score_cases(name: str, rows: list[dict]) -> dict:
    tp = sum(1 for r in rows if r["expected_reject"] and r["predicted_reject"])
    fp = sum(1 for r in rows if not r["expected_reject"] and r["predicted_reject"])
    tn = sum(1 for r in rows if not r["expected_reject"] and not r["predicted_reject"])
    fn = sum(1 for r in rows if r["expected_reject"] and not r["predicted_reject"])
    reject_precision = tp / (tp + fp) if tp + fp else 1.0
    reject_recall = tp / (tp + fn) if tp + fn else 1.0
    pass_precision = tn / (tn + fn) if tn + fn else 1.0
    pass_recall = tn / (tn + fp) if tn + fp else 1.0
    balanced = (reject_recall + pass_recall) / 2
    accuracy = (tp + tn) / len(rows) if rows else 1.0
    return {"gate": name, "count": len(rows), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "reject_precision": reject_precision, "reject_recall": reject_recall,
            "pass_precision": pass_precision, "pass_recall": pass_recall,
            "false_reject_rate": fp / (fp + tn) if fp + tn else 0.0,
            "balanced_accuracy": balanced, "accuracy": accuracy, "cases": rows}


def static_cases() -> list[dict]:
    configs = [
        ("safe_candidate", False, {}),
        ("noop", True, {"is_noop": True}),
        ("untrusted_diff", True, {"trusted_diff": False}),
        ("agent_diff_mismatch", True, {"report_matches": False}),
        ("unreachable", True, {"reachability": False}),
        ("forbidden_scope", True, {"scope": False}),
        ("unsafe_manifest", True, {"manifest": False}),
        ("missing_protected_binding", True, {"binding": False}),
        ("invalid_created_skill", True, {"skill": False}),
        ("benchmark_hardcode", True, {"hardcode": False}),
        ("research_warning_only", False, {"research_warning": True}),
    ]
    rows = []
    for label, expected_reject, cfg in configs:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); tune = base / "tune"; round_dir = base / "round"; tune.mkdir(); round_dir.mkdir()
            (round_dir / "round_state.json").write_text(json.dumps({"input_spec_contract": {}, "baseline_optimization": {}}))
            summary = {"is_noop": cfg.get("is_noop", False), "trusted": cfg.get("trusted_diff", True), "agent_report_consistency": {"matches": cfg.get("report_matches", True)}, "changed_files": [], "touched_paths": []}
            quality = {"valid": cfg.get("manifest", True), "errors": [], "research_quality": {"passed": not cfg.get("research_warning", False)}}
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(MOD, "_candidate_reachability_report", return_value={"activated": cfg.get("reachability", True)}))
                stack.enter_context(mock.patch.object(MOD, "_candidate_scope_report", return_value={"valid": cfg.get("scope", True)}))
                stack.enter_context(mock.patch.object(MOD, "_load_change_manifest", return_value={"protected_signals": []}))
                stack.enter_context(mock.patch.object(MOD, "_apply_runner_protected_binding_defaults", side_effect=lambda _s, m, _b: m))
                stack.enter_context(mock.patch.object(MOD, "_validate_change_manifest_quality", return_value=quality))
                stack.enter_context(mock.patch.object(MOD, "_protected_signal_binding_report", return_value={"valid": cfg.get("binding", True)}))
                stack.enter_context(mock.patch.object(MOD, "_validate_created_skills", return_value={"valid": cfg.get("skill", True)}))
                stack.enter_context(mock.patch.object(MOD, "_candidate_hardcode_report", return_value={"valid": cfg.get("hardcode", True)}))
                result = MOD._candidate_prevalidation_gate(Namespace(), {"tune_dir": tune, "round_dir": round_dir}, summary)
            rows.append({"case": label, "expected_reject": expected_reject, "predicted_reject": not result["valid"], "decision": result.get("reasons")})
    return rows


def effect_case(label, expected_reject, base_tasks, cand_tasks, plan):
    with tempfile.TemporaryDirectory() as root:
        b=Path(root)/"b.json"; c=Path(root)/"c.json"; report(b,base_tasks); report(c,cand_tasks)
        state={"baseline_optimization":{"result_path":str(b),"task_scores":{k:v.get("score") for k,v in base_tasks.items()}},
               "bench":{"candidate_optimization_targeted":{"status":"succeeded","resultPath":str(c),"startedAt":20,"signalPlanId":plan.get("plan_id")}},
               "steps":{"ensure-tune":{"updated_at":"1970-01-01T00:00:10+00:00"}}}
        gate=MOD._candidate_opt_effect_report(state,plan)
        return {"case":label,"expected_reject":expected_reject,"predicted_reject":not gate["valid"],"decision":gate.get("decision"),"reasons":gate.get("reasons")}


def effect_cases() -> list[dict]:
    def p(exp, prot=None): return {"valid":True,"plan_id":"p","schema_version":"evolution.candidate_opt_signal_plan.v2","expected_signals":exp,"protected_signals":prot or []}
    B={"t":{"score":0.4,"breakdown":{"fix":0,"safe":1,"llm_judge.风险识别准确度（含原文证据）":0,"llm_judge.输出格式合规":1}}}
    rows=[]
    rows.append(effect_case("required_improves",False,B,{"t":{"score":0.8,"breakdown":{"fix":1,"safe":1}}},p([{"task_id":"t","metric":"fix","baseline":0,"direction":"boolean_flip","expected_value":1}])))
    rows.append(effect_case("rubric_alias_drift",False,B,{"t":{"score":0.8,"breakdown":{"llm_judge.风险识别准确度":0.8}}},p([{"task_id":"t","metric":"llm_judge.风险识别准确度（含原文证据）","baseline":0,"direction":"increase","min_delta":0.2}])))
    rows.append(effect_case("abstract_output_contract",False,B,{"t":{"score":0.8,"breakdown":{"llm_judge.输出格式合规":1}}},p([{"task_id":"t","metric":"score","baseline":0.4,"direction":"increase","min_delta":0.1}],[{"behavior_id":"PB-out","task_id":"t","metric":"output_contract_complete","baseline":1,"min_value":1,"max_drop":0,"gate":"hard"}])))
    rows.append(effect_case("supporting_missing_does_not_block",False,B,{"t":{"score":0.8,"breakdown":{"fix":1}}},p([{"task_id":"t","metric":"fix","baseline":0,"direction":"boolean_flip","expected_value":1},{"task_id":"t","metric":"missing","baseline":0,"direction":"increase","min_delta":1,"role":"supporting"}])))
    rows.append(effect_case("budget_regression_does_not_block_targeted",False,B,{"t":{"score":0.2,"breakdown":{"fix":1}}},p([{"task_id":"t","metric":"fix","baseline":0,"direction":"boolean_flip","expected_value":1}],[{"task_id":"t","metric":"score","baseline":0.4,"max_drop":0.1,"gate":"budget"}])))
    rows.append(effect_case("partial_continuous_gain_reaches_full_opt",False,B,{"t":{"score":0.426,"breakdown":{}}},p([{"task_id":"t","metric":"score","baseline":0.4,"direction":"increase","min_delta":0.05}])))
    rows.append(effect_case("sub_noise_continuous_gain_rejected",True,B,{"t":{"score":0.405,"breakdown":{}}},p([{"task_id":"t","metric":"score","baseline":0.4,"direction":"increase","min_delta":0.05}])))
    rows.append(effect_case("required_no_change",True,B,{"t":{"score":0.4,"breakdown":{"fix":0}}},p([{"task_id":"t","metric":"fix","baseline":0,"direction":"boolean_flip","expected_value":1}])))
    rows.append(effect_case("required_missing",True,B,{"t":{"score":0.8,"breakdown":{}}},p([{"task_id":"t","metric":"missing","baseline":0,"direction":"increase","min_delta":1}])))
    rows.append(effect_case("hard_protected_regression",True,B,{"t":{"score":0.8,"breakdown":{"fix":1,"safe":0}}},p([{"task_id":"t","metric":"fix","baseline":0,"direction":"boolean_flip","expected_value":1}],[{"behavior_id":"PB-safe","task_id":"t","metric":"safe","baseline":1,"min_value":1,"max_drop":0,"gate":"hard"}])))
    rows.append(effect_case("baseline_spoof",True,B,{"t":{"score":0.8,"breakdown":{"fix":1}}},p([{"task_id":"t","metric":"fix","baseline":1,"direction":"increase","min_delta":0.1}])))
    return rows


def full_case(label, expected_reject, baseline_scores, candidate_scores, protected=None, args_patch=None):
    with tempfile.TemporaryDirectory() as root:
        base=Path(root); rd=base/"round"; tune=base/"tune"; rd.mkdir(); tune.mkdir(); b=base/"b.json"; c=base/"c.json"
        report(b,{k:{"score":v,"breakdown":{}} for k,v in baseline_scores.items()}); report(c,{k:{"score":v,"breakdown":{}} for k,v in candidate_scores.items()})
        state={"candidate_opt_gate":{"valid":True},"baseline_optimization":{"result_path":str(b),"task_scores":baseline_scores},"candidate_opt_signal_plan":{"schema_version":"evolution.candidate_opt_signal_plan.v2","protected_signals":protected or []},"bench":{"candidate_optimization_full":{"status":"succeeded","resultPath":str(c),"summary":{"score":sum(candidate_scores.values())/len(candidate_scores)}}}}
        (rd/"round_state.json").write_text(json.dumps(state))
        kw={"bench_mode":"local","protected_max_drop":0.1,"full_opt_max_regressed_count":2,"full_opt_max_regressed_ratio":0.34,"full_opt_max_negative_delta":0.25,"full_opt_max_single_drop":0.20,"full_opt_min_mean_delta":-0.02}; kw.update(args_patch or {})
        with mock.patch.object(MOD,"resolve_paths",return_value={"round_dir":rd,"tune_dir":tune}),mock.patch.object(MOD,"action_bench_local",return_value={"status":"succeeded"}),mock.patch.object(MOD,"_evaluation_identity",return_value={}),mock.patch.object(MOD,"_mark_step"),mock.patch.object(MOD,"_print_json"):
            MOD.action_bench_candidate_opt_full(Namespace(**kw))
        gate=json.loads((rd/"round_state.json").read_text())["full_opt_gate"]
        return {"case":label,"expected_reject":expected_reject,"predicted_reject":not gate["valid"],"decision":gate.get("budget_failures") or gate.get("hard_protected_regressions") or gate.get("missing_task_ids")}


def full_cases():
    return [
      full_case("all_improve",False,{"a":.5,"b":.5,"c":.5},{"a":.7,"b":.6,"c":.8}),
      full_case("one_small_regression_with_gain",False,{"a":.7,"b":.7,"c":.4},{"a":.59,"b":.72,"c":.8},args_patch={"full_opt_max_negative_delta":.2,"full_opt_min_mean_delta":-.05}),
      full_case("missing_task",True,{"a":.5,"b":.5},{"a":.7}),
      full_case("worst_drop",True,{"a":.8,"b":.5},{"a":.4,"b":.8}),
      full_case("negative_mass",True,{"a":.8,"b":.8,"c":.8},{"a":.65,"b":.65,"c":.8}),
      full_case("hard_protected",True,{"a":.8,"b":.5},{"a":.65,"b":.8},protected=[{"behavior_id":"PB-a","task_id":"a","metric":"score","baseline":.8,"max_drop":0,"gate":"hard"}],args_patch={"full_opt_max_negative_delta":1,"full_opt_max_single_drop":1,"full_opt_min_mean_delta":-1,"full_opt_max_regressed_ratio":1}),
      full_case("mean_negative",True,{"a":.6,"b":.6,"c":.6},{"a":.57,"b":.57,"c":.57},args_patch={"full_opt_max_negative_delta":1,"full_opt_max_single_drop":1,"full_opt_max_regressed_ratio":1,"full_opt_max_regressed_count":3}),
    ]


def historical_effect_cases(root: Path):
    labels=[("EV-202608172307-4472F8CA",1,True),("EV-202608172307-4472F8CA",2,True),("EV-202608181130-868DC7E6",1,False),("EV-202608181130-868DC7E6",2,False),("EV-202608181130-868DC7E6",3,True),("EV-202608181423-2284ABCA",1,False)]
    rows=[]
    for ev,rid,expected_reject in labels:
        run=root/"temp"/ev
        if not run.is_dir(): continue
        result=REPLAY.replay_round(MOD,run,rid)
        gate=result.get("replayed_gate") or {}
        rows.append({"case":f"historical:{ev}:round-{rid}","expected_reject":expected_reject,"predicted_reject":not gate.get("valid"),"decision":gate.get("decision"),"reasons":gate.get("reasons")})
    return rows


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--threshold",type=float,default=.90); ap.add_argument("--json",action="store_true"); args=ap.parse_args()
    effect=effect_cases()+historical_effect_cases(ROOT)
    results=[score_cases("static_candidate_gate",static_cases()),score_cases("candidate_effect_gate",effect),score_cases("full_opt_gate",full_cases())]
    keys=("reject_precision","reject_recall","pass_precision","pass_recall","balanced_accuracy","accuracy")
    passed=all(all(r[k]>=args.threshold for k in keys) for r in results)
    payload={"threshold":args.threshold,"passed":passed,"results":results}
    if args.json: print(json.dumps(payload,ensure_ascii=False,indent=2))
    else:
        for r in results:
            print(f"{r['gate']}: n={r['count']} accuracy={r['accuracy']:.3f} balanced={r['balanced_accuracy']:.3f} reject_precision={r['reject_precision']:.3f} reject_recall={r['reject_recall']:.3f} pass_precision={r['pass_precision']:.3f} pass_recall={r['pass_recall']:.3f} false_reject={r['false_reject_rate']:.3f}")
            for c in r['cases']:
                if c['expected_reject'] != c['predicted_reject']: print('  MISCLASSIFIED',c)
        print('CALIBRATION', 'PASS' if passed else 'FAIL', f"threshold={args.threshold:.2f}")
    return 0 if passed else 1

if __name__=='__main__': raise SystemExit(main())
