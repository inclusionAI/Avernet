"""Tune exhaustion rejects the round without evaluating or promoting a candidate."""
import json
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_clawevolve_evolution_guards import MOD


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def run_case(tmp_path, monkeypatch):
    args = Namespace(
        task_id="EV-test", step_id="STEP-test", round=6, workspace=str(tmp_path),
        skill_base=str(tmp_path), skip_oss=False, skip_clawweb=False,
        resume=False, force=False, restore_precheck=False, artifact_path="",
        clawweb_url="https://example.test", clawweb_url_camel="", max_rounds=5,
        owner_id="owner", train_bench_domain_id="train", test_bench_domain_id="test",
    )
    paths = MOD.resolve_paths(args)
    for name, path in paths.items():
        if name.endswith("_dir"):
            path.mkdir(parents=True, exist_ok=True)
    snapshot = paths["rollback_dir"] / "snapshot.zip"
    snapshot.write_bytes(b"original workspace")
    baseline = {
        role: {"producer_step_id": "STEP-baseline", "domain_owner_id": "owner",
               "domain_id": role, "bench_run_id": f"bench-{role}",
               "summary": {"score": score}}
        for role, score in (("train", 0.905), ("test", 0.35625))
    }
    state = {
        "step_id": args.step_id, "candidate_mutation_state": "possibly_applied",
        "rollback_snapshot": {"status": "ready", "path": str(snapshot), "sha256": MOD._sha256(snapshot)},
        "baseline_optimization": baseline["train"], "baseline_validation": baseline["test"],
    }
    write_json(paths["round_dir"] / "round_state.json", state)
    manifest = {
        "last_accepted_round": 1, "last_accepted_validation_score": 0.35625,
        "last_accepted_artifact": {"ref": "oss://example/accepted.zip"},
        "accepted_baseline_optimization": baseline["train"],
        "accepted_baseline_validation": baseline["test"],
        "baseline_registry": {"round_id": 1},
    }
    manifest_path = paths["optimize_output_dir"] / "optimize_manifest.json"
    write_json(manifest_path, manifest)
    spec = {"schema_version": "evolution.spec.v1", "spec_version": "v5",
            "objective_contract": {"objective_summary": ["Keep the current objective"]}}
    write_json(paths["input_dir"] / "spec-v5.json", spec)
    (paths["input_dir"] / "spec-v5.md").write_text(MOD._render_spec_v1_markdown(spec))
    write_json(paths["optimize_input_dir"] / "objective.json", {
        "primary_metric": {"name": "task_success_rate", "display_name": "Success rate",
                           "operator": ">=", "target": 0.9, "unit": "ratio"},
    })
    monkeypatch.setattr(MOD, "resolve_paths", lambda _: paths)
    for name in ("select_business_core", "_handle_startup_interrupt", "_cleanup_orphan_openclaw_agents",
                 "_report_step_failure_to_clawweb_best_effort", "_report_round_failure", "_print_json"):
        monkeypatch.setattr(MOD, name, Mock())
    monkeypatch.setattr(MOD, "_foreach_orphan_cleanup", lambda: 0)
    monkeypatch.setattr(MOD, "_check_disk_space", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(MOD.time, "sleep", Mock())
    for name in ("action_prepare", "action_baseline_pack", "action_load_baseline_opt"):
        monkeypatch.setattr(MOD, name, Mock())
    original_done = MOD._step_done
    monkeypatch.setattr(MOD, "_step_done", lambda a, n: True if n in {
        "prepare", "baseline-pack", "load-baseline-opt"} else original_done(a, n))
    tune = Mock(side_effect=RuntimeError("Tune failed: outputs missing; stopReason=length"))
    monkeypatch.setattr(MOD, "action_ensure_tune", tune)
    candidates = {}
    for name in ("action_bench_candidate_opt_full", "action_bench_val", "action_accept", "action_pack", "action_ensure_review"):
        candidates[name] = Mock(side_effect=AssertionError(f"unexpected {name}"))
        monkeypatch.setattr(MOD, name, candidates[name])
    monkeypatch.setattr(MOD, "find_script", lambda *a: tmp_path / "deploy.sh")
    monkeypatch.setattr(MOD, "_assert_restore_runtime_compatible", lambda *a: {"compatible": True})
    monkeypatch.setattr(MOD, "resolve_runner_environment", lambda: SimpleNamespace(deployment_workspace=lambda *a: str(tmp_path)))
    requests, restores, uploads = [], [], []

    def run(cmd, **kw):
        if cmd[0] == "curl":
            requests.append(json.loads(cmd[cmd.index("--data-raw") + 1]))
            return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
        restores.append(cmd)
        return SimpleNamespace(returncode=0, stdout="restored", stderr="")

    monkeypatch.setattr(MOD.subprocess, "run", run)

    def upload(kind, path, content_type, round_id):
        uploads.append(kind)
        return {"ref": f"oss://example/{kind}", "kind": kind}

    monkeypatch.setattr(MOD, "_artifact_client", lambda _: SimpleNamespace(upload=upload))
    return SimpleNamespace(args=args, paths=paths, tune=tune, candidates=candidates,
                           requests=requests, restores=restores, uploads=uploads,
                           manifest=manifest, manifest_path=manifest_path)


def test_exhausted_tune_uses_rejected_round_lifecycle(run_case):
    c = run_case
    result = MOD.action_watchdog_run_round(c.args)
    assert c.tune.call_count == 3
    assert [call.args[0] for call in MOD.time.sleep.call_args_list] == [30, 60]
    assert result["accepted"] is False
    for operation in c.candidates.values():
        operation.assert_not_called()
    assert len(c.restores) == 1
    assert "round-pack" not in c.uploads
    assert "round-acceptance" in c.uploads
    manifest = json.loads(c.manifest_path.read_text())
    for key, value in c.manifest.items():
        assert manifest[key] == value
    record = manifest["rounds"][-1]
    assert record["accepted"] is False
    assert record["validation_score"] is None
    assert record["artifact"] == ""
    carried = json.loads((c.paths["spec_dir"] / "spec-v6.json").read_text())
    assert carried["objective_contract"]["objective_summary"] == ["Keep the current objective"]
    payload = c.requests[-1]
    assert payload["status"] == "succeeded"
    assert payload["output"]["accepted"] is False
    assert payload["output"]["roundDecision"]["stop"] is False
    assert payload["output"]["metrics"] == []
    assert payload["output"]["scoreComparison"]["candidate"] is None
    assert payload["output"]["pack"]["status"] == "unchanged"
    assert "Tune 执行失败" in payload["summary"]
    assert "最后一轮" not in payload["summary"]
    assert MOD._validate_clawweb_baseline_output(payload["output"]["baseline"]) == ""
    # Restarting the same Step keeps the rejection; no evaluation or duplicate round.
    c.args.resume = True
    MOD.action_watchdog_run_round(c.args)
    assert c.tune.call_count == 3
    assert len(c.restores) == 1
    assert len(json.loads(c.manifest_path.read_text())["rounds"]) == 1


def test_restore_failure_uses_existing_fatal_policy(run_case, monkeypatch):
    c = run_case
    restore = Mock(side_effect=RuntimeError("restore failed"))
    monkeypatch.setattr(MOD, "action_restore", restore)
    with pytest.raises(SystemExit):
        MOD.action_watchdog_run_round(c.args)
    assert restore.call_count == 3
    assert c.requests == []
    assert json.loads(c.manifest_path.read_text()) == c.manifest
    MOD._report_round_failure.assert_called_once()


@pytest.mark.parametrize("round_id", [1, 12])
def test_rejection_is_independent_of_round_limit(run_case, round_id):
    c = run_case
    c.args.round = round_id
    spec = c.paths["input_dir"] / f"spec-v{round_id - 1}"
    spec.with_suffix(".md").write_text("# Previous strategy")
    write_json(spec.with_suffix(".json"), {"spec_version": f"v{round_id - 1}"})
    if round_id == 1:
        write_json(c.manifest_path, {})
    MOD.action_watchdog_run_round(c.args)
    assert c.requests[-1]["output"]["roundDecision"]["stop"] is False
    assert c.requests[-1]["output"]["accepted"] is False
    assert "最后一轮" not in c.requests[-1]["summary"]


def test_tune_retry_success_keeps_normal_sequence(run_case, monkeypatch):
    c = run_case
    calls = []

    def tune_ok(args):
        calls.append("tune")
        if len(calls) == 1:
            raise RuntimeError("retryable Tune error")
        for path in MOD.STEP_OUTPUTS["ensure-tune"](c.paths, args):
            path.write_text("{}")

    c.tune.side_effect = tune_ok
    # Stop at the first real candidate evaluation: it must be reached after retry.
    bench = Mock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(MOD, "action_bench_candidate_opt_full", bench)
    with pytest.raises(KeyboardInterrupt):
        MOD.action_watchdog_run_round(c.args)
    assert c.tune.call_count == 2
    bench.assert_called_once()
    assert not (c.paths["accept_dir"] / "acceptance_report.json").exists()


@pytest.mark.parametrize("accepted", [False, True])
def test_regular_report_keeps_decision_without_assuming_last_round(run_case, accepted):
    c = run_case
    state_path = c.paths["round_dir"] / "round_state.json"
    state = json.loads(state_path.read_text())
    state["bench"] = {kind: {"summary": {"score": 0.5}, "benchRunId": f"candidate-{kind}"}
                      for kind in ("optimization", "validation")}
    write_json(state_path, state)
    write_json(c.paths["accept_dir"] / "acceptance_report.json", {
        "accepted": accepted, "decision": "passed" if accepted else "not_improved",
        "bench_decision": "passed" if accepted else "not_improved",
        "promotion_status": "succeeded" if accepted else "not_started",
    })
    MOD.action_upload_clawweb(c.args)
    payload = c.requests[-1]
    assert payload["output"]["accepted"] is accepted
    assert payload["output"]["roundDecision"]["stop"] is False
    assert len(payload["output"]["metrics"]) == 2
    assert "最后一轮" not in payload["summary"]


def test_consecutive_rejections_keep_accepted_baseline(run_case):
    c = run_case
    MOD.action_watchdog_run_round(c.args)
    carried_spec = json.loads((c.paths["spec_dir"] / "spec-v6.json").read_text())
    previous_state = json.loads((c.paths["round_dir"] / "round_state.json").read_text())
    c.args.round = 7
    c.args.step_id = "STEP-next"
    next_round = c.paths["optimize_output_dir"] / "round-007"
    for name in ("input_dir", "tune_dir", "spec_dir", "accept_dir", "artifacts_dir", "rollback_dir", "upload_dir"):
        c.paths[name] = next_round / c.paths[name].name
        c.paths[name].mkdir(parents=True, exist_ok=True)
    c.paths["round_dir"] = next_round
    write_json(c.paths["input_dir"] / "spec-v6.json", carried_spec)
    (c.paths["input_dir"] / "spec-v6.md").write_text(MOD._render_spec_v1_markdown(carried_spec))
    snapshot = c.paths["rollback_dir"] / "snapshot.zip"
    snapshot.write_bytes(b"original workspace")
    write_json(next_round / "round_state.json", {
        "step_id": c.args.step_id, "candidate_mutation_state": "possibly_applied",
        "rollback_snapshot": {"status": "ready", "path": str(snapshot), "sha256": MOD._sha256(snapshot)},
        "baseline_optimization": previous_state["baseline_optimization"],
        "baseline_validation": previous_state["baseline_validation"],
    })
    MOD.action_watchdog_run_round(c.args)
    manifest = json.loads(c.manifest_path.read_text())
    assert [record["round_id"] for record in manifest["rounds"]] == [6, 7]
    for key, value in c.manifest.items():
        assert manifest[key] == value
    assert len(c.restores) == 2
    assert c.tune.call_count == 6
    assert c.requests[-1]["output"]["roundDecision"]["stop"] is False
    for operation in c.candidates.values():
        operation.assert_not_called()


def test_failure_to_record_rejection_cannot_fall_through_to_bench(run_case, monkeypatch):
    c = run_case
    monkeypatch.setattr(MOD, "_fallback_reject_round", Mock(side_effect=OSError("disk full")))
    with pytest.raises(SystemExit):
        MOD.action_watchdog_run_round(c.args)
    for operation in c.candidates.values():
        operation.assert_not_called()
    assert c.requests == []
