"""Shared Plan prompt contract; no Bot/model execution."""
from pathlib import Path
import json
import pytest
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_plan.direct_goal.prompt import build_direct_goal_prompt
from clawevolve_plan.direct_goal.schema import goal_digest
from clawevolve_plan.discovery.prompt import open_skill_layout_instruction, build_discovery_prompt
from clawevolve_plan.direct_goal.service import build_direct_goal_plan
from test_direct_goal import GOAL, TARGET, direct_payload, make_workspace


def test_direct_goal_json_prompt_keeps_version_specific_skill_layout(monkeypatch, tmp_path):
    args = dict(goal=GOAL, task_id="EV-1", workspace_root=tmp_path, output_path=tmp_path/"candidate.json")
    monkeypatch.setenv("CLAWWEB_VERSION", "internalversion")
    internal_prompt = build_direct_goal_prompt(**args)
    internal_example = json.loads(internal_prompt.split("JSON 契约示例：\n", 1)[1])
    assert internal_example["discovery"]["merged_targets"] == ["skills/skills-local"]

    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    prompt = build_direct_goal_prompt(**args)
    assert "workspace/skills 为空是合法状态" in prompt
    assert "确认该目录为空后停止调用工具" in prompt
    assert "0-5 个 workspace 文件" in prompt
    contract = prompt.split("JSON 契约示例：\n", 1)[1]
    example = json.loads(contract)
    assert example["discovery"]["merged_targets"] == ["skills"]
    assert example["discovery"]["reference_files"] == []
    assert example["discovery"]["planned_deliverables"][0]["path"] == "skills/new-skill/SKILL.md"
    assert "必须先构造 Python dict" not in prompt
    assert "不生成或运行Python/shell脚本" in prompt
    assert "few-shot" not in prompt and "早安" not in prompt
    assert "schema_version" not in example
    assert "goal_digest" not in example
    assert "workspace_root" not in example
    assert "original_goal" not in example
    assert "schema_version" not in example["discovery"]
    assert "workspace_root" not in example["discovery"]
    assert "- goal_digest:" not in prompt


@pytest.mark.parametrize("version", [None, "internalversion", "openversion"])
def test_response_only_correction_is_validated_and_saved(monkeypatch, tmp_path, version):
    workspace = make_workspace(tmp_path)
    input_dir = workspace / "clawevolve_results/EV-1/plan/input"
    if version is None:
        monkeypatch.delenv("CLAWWEB_VERSION", raising=False)
    else:
        monkeypatch.setenv("CLAWWEB_VERSION", version)
    monkeypatch.setenv("CLAWEVOLVE_INVOCATION_CWD", str(workspace))
    prompts = []
    def agent(**kwargs):
        prompts.append(kwargs["message"])
        payload = direct_payload(workspace)
        if len(prompts) == 1:
            if version is None:
                for key in ("schema_version", "goal_digest", "workspace_root"):
                    payload.pop(key, None)
                payload["discovery"].pop("schema_version", None)
                payload["discovery"].pop("workspace_root", None)
            else:
                payload["schema_version"] = "model-owned-invalid"
                payload["goal_digest"] = "model-owned-invalid"
                payload["workspace_root"] = "/model-owned-invalid"
                payload["discovery"]["schema_version"] = "model-owned-invalid"
                payload["discovery"]["workspace_root"] = "/model-owned-invalid"
            payload.pop("original_goal", None)
        else:
            assert "Build a Python dict" not in kwargs["message"]
            assert "Do not write files" in kwargs["message"]
            assert "few-shot" not in kwargs["message"]
            assert "validate it with Python" not in kwargs["message"]
        return SimpleNamespace(status="success", elapsed_seconds=0.1,
                               response_text=json.dumps(payload, ensure_ascii=False), stdout_text="", diagnostics={})
    monkeypatch.setattr("clawevolve_plan.direct_goal.service.run_openclaw_agent_message", agent)
    result = build_direct_goal_plan(goal=GOAL, task_id="EV-1", bot_id="bot-direct", input_dir=input_dir)
    assert len(prompts) == 1
    assert result.targets == [TARGET]
    assert not (input_dir / "direct_goal.candidate.json").exists()
    saved = json.loads((input_dir / "direct_goal.json").read_text())
    assert saved["schema_version"] == "clawevolve.plan.direct-goal.v1"
    assert saved["goal_digest"] == goal_digest(GOAL)
    assert saved["workspace_root"] == str(workspace.resolve())
    assert saved["original_goal"] == GOAL
    assert saved["discovery"]["schema_version"] == "clawevolve.plan.discovery.v1"
    assert saved["discovery"]["workspace_root"] == str(workspace.resolve())


def test_correction_schema_omits_program_owned_fields(monkeypatch, tmp_path):
    workspace = make_workspace(tmp_path)
    input_dir = workspace / "clawevolve_results/EV-1/plan/input"
    monkeypatch.setenv("CLAWEVOLVE_INVOCATION_CWD", str(workspace))
    prompts = []

    def agent(**kwargs):
        prompts.append(kwargs["message"])
        payload = direct_payload(workspace)
        if len(prompts) == 1:
            payload["prospective_cases"] = []
        else:
            for key in ("schema_version", "goal_digest", "workspace_root"):
                payload.pop(key, None)
            payload["discovery"].pop("schema_version", None)
            payload["discovery"].pop("workspace_root", None)
        return SimpleNamespace(
            status="success",
            elapsed_seconds=0.1,
            response_text=json.dumps(payload, ensure_ascii=False),
            stdout_text="",
            diagnostics={},
        )

    monkeypatch.setattr(
        "clawevolve_plan.direct_goal.service.run_openclaw_agent_message", agent
    )
    build_direct_goal_plan(
        goal=GOAL, task_id="EV-1", bot_id="bot-direct", input_dir=input_dir
    )

    assert len(prompts) == 2
    schema_text = prompts[1].split("Canonical schema example:\n", 1)[1].rsplit(
        "\n\nReturn the corrected JSON", 1
    )[0]
    schema = json.loads(schema_text)
    assert not {
        "schema_version", "goal_digest", "workspace_root", "original_goal"
    } & schema.keys()
    assert "schema_version" not in schema["discovery"]
    assert "workspace_root" not in schema["discovery"]


def test_plan_layout_note_applies_to_both_sources_only_in_openversion(monkeypatch, tmp_path):
    args = dict(source_path=tmp_path/"source.json", workspace_root=tmp_path, output_path=tmp_path/"out.json",
                source_schema="plan-source/v2", source_size_bytes=10, case_count=1, cluster_count=1, input_mode="diagnose_goal")
    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    note = open_skill_layout_instruction()
    assert "workspace/skills/<skill-name>/SKILL.md" in note
    assert "不创建 skills-local" in note
    assert note in build_discovery_prompt(**args)
    assert note in build_direct_goal_prompt(goal=GOAL, task_id="EV-1", workspace_root=tmp_path, output_path=tmp_path/"out.json")
    monkeypatch.setenv("CLAWWEB_VERSION", "internalversion")
    assert open_skill_layout_instruction() == ""
    assert note not in build_discovery_prompt(**args)
