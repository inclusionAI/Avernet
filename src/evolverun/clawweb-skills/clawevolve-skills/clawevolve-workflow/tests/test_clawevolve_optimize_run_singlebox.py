"""Local Optimize prerequisites; no real Bot, model or process is touched."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("optimize_singlebox", SCRIPT)
handler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handler)


def test_openversion_does_not_scan_or_kill_host_processes(monkeypatch):
    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    with patch.object(handler.subprocess, "run") as run, patch.object(handler.os, "kill") as kill:
        assert handler._cleanup_orphan_openclaw_agents() == 0
        run.assert_not_called()
        kill.assert_not_called()


@pytest.mark.parametrize("version", ["internalversion", ""])
def test_internal_cleanup_is_unchanged(monkeypatch, version):
    monkeypatch.setenv("CLAWWEB_VERSION", version)
    result = SimpleNamespace(stdout="PID PPID RSS COMM\n123 1 60000 openclaw-agent\n124 2 60000 openclaw-agent\n")
    with patch.object(handler.subprocess, "run", return_value=result) as run, patch.object(handler.os, "kill") as kill:
        assert handler._cleanup_orphan_openclaw_agents() == 1
        run.assert_called_once()
        kill.assert_called_once_with(123, handler.signal.SIGKILL)


def test_openversion_workspace_is_bound_to_selected_bot(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    assert handler._resolve_workspace() == tmp_path.resolve()
    assert handler._resolve_workspace(SimpleNamespace(workspace=str(tmp_path))) == tmp_path.resolve()
    other = tmp_path / "another-bot"
    other.mkdir()
    with pytest.raises(ValueError, match="selected Bot"):
        handler._resolve_workspace(SimpleNamespace(workspace=str(other)))


def test_openversion_missing_binding_fails_closed(monkeypatch):
    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    monkeypatch.delenv("OPENCLAW_WORKSPACE", raising=False)
    with pytest.raises(ValueError, match="OPENCLAW_WORKSPACE"):
        handler._resolve_workspace()


def test_internal_workspace_defaults_remain_unchanged(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAWWEB_VERSION", raising=False)
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    assert handler._resolve_workspace() == handler.FIXED_WORKSPACE
    assert handler._resolve_workspace(SimpleNamespace(workspace=str(tmp_path))) == tmp_path


def test_frozen_primary_plan_is_copied_without_rerunning_or_overwriting(monkeypatch, tmp_path):
    import io, json, urllib.request
    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    args = SimpleNamespace(task_id="EV-new", step_id="STEP-new", owner_id="owner", round=1, clawweb_url="http://127.0.0.1:5173")
    source = tmp_path / "clawevolve_results/EV-source/plan/output"
    source.mkdir(parents=True)
    (source / "spec-v0.md").write_text("original spec")
    frozen = {"task": {"taskId": args.task_id, "taskType": "optimize"}, "target": {"userId": "owner"}, "step": {"stepId": args.step_id},
              "inputs": {"diagnoses": [{"role": "primary", "taskId": "EV-source", "plan": {"stepId": "STEP-source"}}]}}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(frozen).encode()))
    handler._prepare_openversion_source(args, tmp_path)
    target = tmp_path / "clawevolve_results/EV-new/plan/output/spec-v0.md"
    assert target.read_text() == "original spec"
    target.write_text("retry must preserve")
    handler._prepare_openversion_source(args, tmp_path)
    assert target.read_text() == "retry must preserve"
    assert (source / "spec-v0.md").read_text() == "original spec"
    link = source / "linked-secret"
    link.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="Symlinked Plan input"):
        handler._prepare_openversion_source(args, tmp_path)
    link.unlink()
    frozen["inputs"]["diagnoses"][0]["taskId"] = "../other-bot"
    with pytest.raises(ValueError, match="source Task"):
        handler._prepare_openversion_source(args, tmp_path)


def test_internal_prepare_never_uses_local_source_adapter(monkeypatch, tmp_path):
    import urllib.request
    monkeypatch.delenv("CLAWWEB_VERSION", raising=False)
    with patch.object(urllib.request, "urlopen") as request:
        handler._prepare_openversion_source(SimpleNamespace(round=1), tmp_path)
        request.assert_not_called()


@pytest.mark.parametrize("version,root", [("openversion", "skills"), ("internalversion", "skills/skills-local")])
def test_created_skill_layout_and_discovery_entry(monkeypatch, tmp_path, version, root):
    monkeypatch.setenv("CLAWWEB_VERSION", version)
    workspace = tmp_path / "workspace"
    skill = workspace / root / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: Handle a narrow sample request.\n---\nFollow user instructions.\n")
    if version == "internalversion":
        (workspace / "skills/sample").symlink_to("skills-local/sample")
    target = f"{root}/sample/SKILL.md"
    manifest = {"changes": [{"change_type": "CREATE_SKILL", "target_file": target, "created_skill": {
        "name": "sample", "entrypoint": target, "trigger_scope": "a narrow sample request",
        "negative_trigger_examples": ["weather query", "calendar query"],
        "why_existing_skills_insufficient": "No existing sample capability", "rollback_condition": "remove sample"}}]}
    paths = {"workspace": workspace, "tune_dir": tmp_path / "tune"}
    changes = [{"path": target, "change": "added"}]
    assert handler._created_skill_entrypoints_from_changes(paths, changes) == [target]
    result = handler._validate_created_skills(SimpleNamespace(), paths, manifest, changes)
    assert result["valid"], result["errors"]
    assert not handler._validate_created_skills(SimpleNamespace(), paths, {}, changes)["valid"]
    if version == "openversion":
        assert not (workspace / "skills/skills-local").exists()
        skill.unlink()
        outside = tmp_path / "outside.md"
        outside.write_text("do not read or change")
        skill.symlink_to(outside)
        assert not handler._validate_created_skills(SimpleNamespace(), paths, manifest, changes)["valid"]
        assert outside.read_text() == "do not read or change"


def test_openversion_tune_prompt_uses_flat_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    monkeypatch.setattr(handler, "_load_input_spec_contract", lambda *a: ({}, "fixture"))
    monkeypatch.setattr(handler, "_known_validation_task_ids", lambda *a: set())
    for name in ("_baseline_optimization_prompt_text", "_build_evolution_history_text", "_optimization_failure_profile_text", "_optimization_scene_playbook_text"):
        monkeypatch.setattr(handler, name, lambda *a: "")
    paths = {"skill_base": str(SCRIPT.parents[3]), "optimize_input_dir": tmp_path, "workspace": tmp_path, "tune_dir": tmp_path}
    prompt = handler._build_tune_prompt(SimpleNamespace(round=1), paths)
    assert f"可写范围仅限 `{tmp_path}/skills/**`" in prompt
    assert "不创建 skills-local、active 或激活软链" in prompt
    assert "实际写入都必须位于 `skills/**`" in prompt
    monkeypatch.setenv("CLAWWEB_VERSION", "internalversion")
    prompt = handler._build_tune_prompt(SimpleNamespace(round=1), paths)
    assert f"可写范围仅限 `{tmp_path}/skills/skills-local/**`" in prompt
