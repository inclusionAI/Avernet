import importlib.util
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/cleanup_clawevolve_openclaw_runtime.py"
SPEC = importlib.util.spec_from_file_location("cleanup_clawevolve_runtime", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def write_agent_config(openclaw_home: Path, rows) -> None:
    openclaw_home.mkdir(parents=True, exist_ok=True)
    (openclaw_home / "openclaw.json").write_text(
        json.dumps({"agents": {"list": list(rows)}}),
        encoding="utf-8",
    )


def remove_agent(rows, agent_id: str) -> None:
    rows[:] = [row for row in rows if row.get("id") != agent_id]


def test_cleanup_deletes_evolve_task_and_shared_agents_but_keeps_main_and_unrelated(tmp_path):
    agent_rows = [
        {"id": "main", "workspace": "/home/admin/.openclaw/workspace"},
        {"id": "clawevolve-tune-ev-1-r001-a1", "workspace": "/home/admin/.openclaw/workspace"},
        {"id": "clawevolve-review-ev-1-r001-a2", "workspace": "/home/admin/.openclaw/workspace"},
        {"id": "bench-antchat-glm-5-a3", "workspace": "/tmp/pinchbench/clawevolve-validation-EV-1/run/a3"},
        {"id": "bench-judge-antchat-glm-5", "workspace": "/tmp/pinchbench/judge/workspace"},
        {"id": "clawbench-report", "workspace": "/tmp/report"},
        {"id": "clawbench-report-a22c9367", "workspace": "/tmp/report-task"},
        {"id": "bench-user-project", "workspace": "/tmp/other-bench"},
        {"id": "business-agent", "workspace": "/tmp/business"},
    ]
    write_agent_config(tmp_path, agent_rows)
    deleted = []

    def runner(args, **kwargs):
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps({"agents": agent_rows}))
        if args[:3] == ["ps", "-eo", "args="]:
            return completed(args)
        if args[:3] == ["openclaw", "agents", "delete"]:
            deleted.append(args[3])
            remove_agent(agent_rows, args[3])
            write_agent_config(tmp_path, agent_rows)
            return completed(args, stdout="{}")
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw", openclaw_home=tmp_path, run=runner
    )

    assert result["status"] == "ok"
    assert set(deleted) == {
        "clawevolve-tune-ev-1-r001-a1",
        "clawevolve-review-ev-1-r001-a2",
        "bench-antchat-glm-5-a3",
        "bench-judge-antchat-glm-5",
        "clawbench-report",
        "clawbench-report-a22c9367",
        "bench-user-project",
    }
    assert "main" not in deleted
    assert "business-agent" not in deleted


def test_cleanup_skips_active_agent_without_blocking_task(tmp_path):
    agents = {"agents": [{"id": "clawevolve-diagnose-ev-1", "workspace": "/tmp/x"}]}
    write_agent_config(tmp_path, agents["agents"])

    def runner(args, **kwargs):
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps(agents))
        if args[:3] == ["ps", "-eo", "args="]:
            return completed(
                args,
                stdout="openclaw agent --agent clawevolve-diagnose-ev-1 --message x\n",
            )
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw", openclaw_home=tmp_path, run=runner
    )

    assert result["status"] == "degraded"
    assert result["skipped_active_agents"] == ["clawevolve-diagnose-ev-1"]
    assert result["deleted_agent_count"] == 0


def test_cleanup_continues_after_backend_failure_and_returns_degraded(tmp_path):
    agent_rows = [
        {"id": "clawevolve-tune-ev-1", "workspace": "/tmp/x"},
        {"id": "clawevolve-review-ev-1", "workspace": "/tmp/y"},
    ]
    write_agent_config(tmp_path, agent_rows)
    attempted = []

    def runner(args, **kwargs):
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps({"agents": agent_rows}))
        if args[:3] == ["ps", "-eo", "args="]:
            return completed(args)
        if args[:3] == ["openclaw", "agents", "delete"]:
            attempted.append(args[3])
            if args[3] == "clawevolve-tune-ev-1":
                return completed(args, returncode=1, stderr="permission denied")
            remove_agent(agent_rows, args[3])
            write_agent_config(tmp_path, agent_rows)
            return completed(args, stdout="{}")
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw", openclaw_home=tmp_path, run=runner
    )

    assert result["status"] == "degraded"
    assert attempted == ["clawevolve-tune-ev-1", "clawevolve-review-ev-1"]
    assert result["failures"][0]["agent_id"] == "clawevolve-tune-ev-1"
    assert result["deleted_agents"] == ["clawevolve-review-ev-1"]
    assert result["deferred_agents"] == []


def test_cleanup_treats_agent_not_found_as_idempotent_success_and_continues(tmp_path):
    agent_rows = [
        {"id": "clawevolve-tune-ev-1", "workspace": "/tmp/x"},
        {"id": "clawevolve-review-ev-1", "workspace": "/tmp/y"},
    ]
    write_agent_config(tmp_path, agent_rows)
    attempted = []

    def runner(args, **kwargs):
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps({"agents": agent_rows}))
        if args[:3] == ["ps", "-eo", "args="]:
            return completed(args)
        if args[:3] == ["openclaw", "agents", "delete"]:
            attempted.append(args[3])
            if args[3] == "clawevolve-tune-ev-1":
                remove_agent(agent_rows, args[3])
                write_agent_config(tmp_path, agent_rows)
                return completed(args, returncode=1, stderr="agent not found")
            remove_agent(agent_rows, args[3])
            write_agent_config(tmp_path, agent_rows)
            return completed(args, stdout="{}")
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw", openclaw_home=tmp_path, run=runner
    )

    assert result["status"] == "ok"
    assert attempted == ["clawevolve-tune-ev-1", "clawevolve-review-ev-1"]
    assert result["deleted_agent_count"] == 2
    assert result["failures"] == []


def test_cleanup_does_not_report_not_found_as_success_when_agent_remains_registered(tmp_path):
    agent_rows = [{"id": "clawevolve-tune-ev-1", "workspace": "/tmp/x"}]
    write_agent_config(tmp_path, agent_rows)

    def runner(args, **kwargs):
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps({"agents": agent_rows}))
        if args[:3] == ["ps", "-eo", "args="]:
            return completed(args)
        if args[:3] == ["openclaw", "agents", "delete"]:
            return completed(args, returncode=1, stderr="agent not found")
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw", openclaw_home=tmp_path, run=runner
    )

    assert result["status"] == "degraded"
    assert result["deleted_agent_count"] == 0
    assert result["failures"] == [{
        "stage": "post-agent-verify",
        "agent_id": "clawevolve-tune-ev-1",
        "error": "agent remains registered after delete: agent not found",
    }]


def test_cleanup_requires_agent_to_disappear_from_persisted_config(tmp_path):
    persisted_rows = [{"id": "clawevolve-tune-ev-1", "workspace": "/tmp/x"}]
    cli_rows = list(persisted_rows)
    write_agent_config(tmp_path, persisted_rows)

    def runner(args, **kwargs):
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps({"agents": cli_rows}))
        if args[:3] == ["ps", "-eo", "args="]:
            return completed(args)
        if args[:3] == ["openclaw", "agents", "delete"]:
            remove_agent(cli_rows, args[3])
            return completed(args, stdout="{}")
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw", openclaw_home=tmp_path, run=runner
    )

    assert result["status"] == "degraded"
    assert result["deleted_agent_count"] == 0
    assert result["failures"] == [{
        "stage": "post-agent-verify",
        "agent_id": "clawevolve-tune-ev-1",
        "error": "agent remains registered after delete",
    }]


def test_cleanup_list_failure_returns_degraded(tmp_path):
    def runner(args, **kwargs):
        return completed(args, returncode=1, stderr="gateway unavailable")

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw", openclaw_home=tmp_path, run=runner
    )

    assert result["status"] == "degraded"
    assert result["deleted_agent_count"] == 0
    assert result["failures"][0]["stage"] == "list"


def test_cleanup_skips_deletion_when_active_process_check_fails(tmp_path):
    agents = {"agents": [{"id": "clawevolve-tune-ev-1", "workspace": "/tmp/x"}]}

    def runner(args, **kwargs):
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps(agents))
        if args[:3] == ["ps", "-eo", "args="]:
            return completed(args, returncode=1, stderr="ps unavailable")
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw", openclaw_home=tmp_path, run=runner
    )

    assert result["status"] == "degraded"
    assert result["deferred_agents"] == ["clawevolve-tune-ev-1"]
    assert result["failures"][0]["stage"] == "active-process-check"


def test_strict_cleanup_deletes_all_bench_agents_and_marked_main_sessions(tmp_path):
    agent_rows = [
        {"id": "main", "workspace": "/home/admin/.openclaw/workspace"},
        {"id": "clawevolve-tune-ev-20260828-a-r001-x", "workspace": "/tmp/a"},
        {"id": "bench-antchat-glm-5-1-ev-20260828-a-deadbeef", "workspace": "/tmp/b"},
        {"id": "bench-antchat-glm-5-1-deadbeef", "workspace": "/tmp/legacy"},
        {"id": "bench-antchat-glm-5-1-d0fe24e0", "workspace": "/tmp/pinchbench/clawevolve-optimization-EV-OLD/run"},
        {"id": "bench-antchat-glm-5-d536661a", "workspace": "/tmp/pinchbench/claw-evolve-bench/20260810-191916-ed324660/d536661a/agent_workspace"},
        {"id": "bench-antchat-glm-5-a0a5a15a", "workspace": "/tmp/pinchbench/claw-evolve-bench-optimize-train/20260812-145141-ff632232/a0a5a15a/agent_workspace"},
        {"id": "bench-antchat-glm-5-0496f559", "workspace": "/tmp/pinchbench/claw-evolve-bench-optimize-test/20260812-145745-e341c5b9/0496f559/agent_workspace"},
        {"id": "bench-judge-antchat-glm-5-1", "workspace": "/tmp/pinchbench/judge/workspace"},
        {"id": "clawbench-report", "workspace": "/tmp/pinchbench/report/workspace"},
        {"id": "bench-user-project", "workspace": "/tmp/other-bench"},
        {"id": "business-ev-20260828-a", "workspace": "/tmp/business"},
    ]
    write_agent_config(tmp_path, agent_rows)
    sessions = {"sessions": [
        {"key": "agent:main:session:one", "agentId": "main", "label": "Claw进化 plan · EV-20260828-A"},
        {"key": "agent:main:session:cleanup", "agentId": "main", "label": "Claw进化 cleanup · EV-CLEAN"},
        {"key": "agent:main:session:business", "agentId": "main", "label": "业务会话 EV-20260828-A"},
    ]}
    deleted_agents = []
    deleted_sessions = []
    session_dir = tmp_path / "agents" / "main" / "sessions"
    session_dir.mkdir(parents=True)
    (session_dir / "sessions.json").write_text(json.dumps({
        row["key"]: {"label": row["label"]} for row in sessions["sessions"]
    }), encoding="utf-8")

    def runner(args, **kwargs):
        if args[0] == "openclaw":
            assert kwargs["env"]["HOME"] == str(tmp_path.parent)
            assert kwargs["env"]["OPENCLAW_STATE_DIR"] == str(tmp_path)
            assert kwargs["env"]["OPENCLAW_CONFIG_PATH"] == str(tmp_path / "openclaw.json")
            assert "OPENCLAW_PROFILE" not in kwargs["env"]
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps({"agents": agent_rows}))
        if args[:3] == ["openclaw", "agents", "delete"]:
            deleted_agents.append(args[3])
            remove_agent(agent_rows, args[3])
            write_agent_config(tmp_path, agent_rows)
            return completed(args, stdout="{}")
        if args[:4] == ["openclaw", "gateway", "call", "sessions.delete"]:
            params = json.loads(args[5])
            assert set(params) == {"key"}
            deleted_sessions.append(params["key"])
            stored = json.loads((session_dir / "sessions.json").read_text(encoding="utf-8"))
            stored.pop(params["key"], None)
            (session_dir / "sessions.json").write_text(json.dumps(stored), encoding="utf-8")
            return completed(args, stdout="{}")
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw",
        openclaw_home=tmp_path,
        run=runner,
        strict_markers=True,
        skip_active_check=True,
        excluded_session_markers=("EV-CLEAN",),
    )

    assert result["status"] == "ok"
    assert set(deleted_agents) == {
        "clawevolve-tune-ev-20260828-a-r001-x",
        "bench-antchat-glm-5-1-ev-20260828-a-deadbeef",
        "bench-antchat-glm-5-1-d0fe24e0",
        "bench-antchat-glm-5-d536661a",
        "bench-antchat-glm-5-a0a5a15a",
        "bench-antchat-glm-5-0496f559",
        "bench-judge-antchat-glm-5-1",
        "clawbench-report",
        "bench-antchat-glm-5-1-deadbeef",
        "bench-user-project",
    }
    assert deleted_sessions == ["agent:main:session:one"]
    assert result["skipped_current_session_count"] == 1


def test_strict_cleanup_continues_after_one_session_delete_failure(tmp_path):
    session_dir = tmp_path / "agents" / "main" / "sessions"
    session_dir.mkdir(parents=True)
    sessions = {
        "agent:main:session:first": {"label": "Claw进化 plan · EV-FIRST"},
        "agent:main:session:second": {"label": "Claw进化 plan · EV-SECOND"},
    }
    (session_dir / "sessions.json").write_text(json.dumps(sessions), encoding="utf-8")
    attempted = []

    def runner(args, **kwargs):
        if args[:4] == ["openclaw", "agents", "list", "--json"]:
            return completed(args, stdout=json.dumps({"agents": [{"id": "main"}]}))
        if args[:4] == ["openclaw", "gateway", "call", "sessions.delete"]:
            key = json.loads(args[5])["key"]
            attempted.append(key)
            if key.endswith("first"):
                return completed(args, returncode=1, stderr="permission denied")
            stored = json.loads((session_dir / "sessions.json").read_text(encoding="utf-8"))
            stored.pop(key, None)
            (session_dir / "sessions.json").write_text(json.dumps(stored), encoding="utf-8")
            return completed(args, stdout="{}")
        raise AssertionError(args)

    result = MODULE.cleanup_runtime(
        openclaw_path="openclaw",
        openclaw_home=tmp_path,
        run=runner,
        strict_markers=True,
        skip_active_check=True,
    )

    assert attempted == [
        "agent:main:session:first",
        "agent:main:session:second",
    ]
    assert result["status"] == "degraded"
    assert result["deleted_sessions"] == ["agent:main:session:second"]
    assert result["failures"][0]["session_key"] == "agent:main:session:first"
