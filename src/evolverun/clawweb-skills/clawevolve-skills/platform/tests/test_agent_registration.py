"""Reproduce shared-registry lost updates through real execution entrypoints."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SKILLS = Path(__file__).resolve().parents[2]

# Model the CLI's read/modify/write operation. The first command pauses after
# reading; without the common lock it overwrites the second caller's update.
CLI = r'''
import json, os, sys, time
from pathlib import Path
root = Path(os.environ["REGISTRY_TEST_ROOT"])
args = sys.argv[1:]
if args[0] == "agent":
    if os.environ.get("STAGE_SUCCEEDS"):
        (root / "model-started").touch()
        deadline = time.monotonic() + 10
        while not (root / "release-model").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("model barrier timed out")
            time.sleep(0.01)
        (root / "result.json").write_text('{"summary":"done"}')
        sys.exit(0)
    sys.exit(1)  # Keep the Stage registration for inspection; no model call.
operation, agent = args[1:3]
registry = root / "registry.json"
data = json.loads(registry.read_text())
(root / (agent + ".read")).touch()
if agent == "judge":
    deadline = time.monotonic() + 10
    while not (root / "release").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("test barrier timed out")
        time.sleep(0.01)
if operation == "add":
    data[agent] = {"workspace": args[args.index("--workspace") + 1]}
else:
    data.pop(agent, None)
registry.write_text(json.dumps(data))
'''

CALLER = r'''
import os, sys
from pathlib import Path
skills, root, caller, operation = map(str, sys.argv[1:])
root = Path(root)
sys.path[:0] = [str(Path(skills) / "platform"), str(Path(skills) / "clawevolve-diagnose")]
if caller.startswith("judge"):
    from clawevolve_diagnose.judge.openclaw_subagent_client import _create_agent, _delete_agent
    from clawevolve_diagnose.models import SubagentJudgeConfig
    config = SubagentJudgeConfig(agent_id=caller, openclaw_path=os.environ["OPENCLAW_PATH"])
    if caller != "judge":
        (root / "stage-ready").touch()
    if operation == "add":
        _create_agent(config, root / "judge-work")
    else:
        _delete_agent(config, caller)
elif caller == "adapt":
    import importlib.util
    spec = importlib.util.spec_from_file_location("adapter", Path(skills) / "scripts/adapt_openclaw_environment.py")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    (root / "stage-ready").touch()
    adapter.check_and_adapt(root / "registry.json", root / "marker.json")
else:
    from clawevolve_runtime.executor import _run_openclaw_agent
    context = {"implementationSkill": str(root / "SKILL.md"),
               "inputFile": str(root / "input.json"), "resultFile": str(root / "result.json")}
    (root / "stage-ready").touch()
    result = _run_openclaw_agent(context, "")
    assert result["status"] == ("succeeded" if os.environ.get("STAGE_SUCCEEDS") else "failed"), result
    (root / "stage-id").write_text(result["agent_id"])
'''


class AgentRegistrationTest(unittest.TestCase):
    def wait_for(self, path):
        deadline = time.monotonic() + 10
        while not path.exists():
            if time.monotonic() >= deadline:
                self.fail(f"Caller did not reach barrier: {path.name}")
            time.sleep(0.01)

    def assert_concurrent_mutations(self, operation, peer="stage"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "openclaw"
            executable.write_text(f"#!{sys.executable}\n" + CLI)
            executable.chmod(0o700)
            initial = {"main": {}} if operation == "add" else {"main": {}, "judge": {}}
            if peer == "adapt":
                initial["plugins"] = {"entries": {"agent-guard": {"enabled": True}}}
            (root / "registry.json").write_text(json.dumps(initial))
            (root / "SKILL.md").write_text("Unchanged business Skill")
            (root / "input.json").write_text("{}")
            env = dict(os.environ, OPENCLAW_PATH=str(executable), REGISTRY_TEST_ROOT=str(root),
                       CLAWEVOLVE_PLAN_AGENT_REGISTRATION_LOCK=str(root / "registration.lock"))
            processes = []
            try:
                for caller in ("judge", peer):
                    processes.append(subprocess.Popen(
                        [sys.executable, "-c", CALLER, str(SKILLS), str(root), caller, operation],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    ))
                    self.wait_for(root / ("judge.read" if caller == "judge" else "stage-ready"))
                # Give the second CLI an opportunity to read while the first is
                # paused. With the fix it cannot start until release; execution
                # afterwards remains parallel and no production sleeps change.
                deadline = time.monotonic() + 0.75
                while not list(root.glob("clawevolve-business-*.read")) and time.monotonic() < deadline:
                    time.sleep(0.01)
                (root / "release").touch()
                for process in processes:
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                registry = json.loads((root / "registry.json").read_text())
                self.assertIn("main", registry)
                if peer == "adapt":
                    self.assertFalse(registry["plugins"]["entries"]["agent-guard"]["enabled"])
                else:
                    stage_id = (root / "stage-id").read_text() if peer == "stage" else peer
                    self.assertIn(stage_id, registry, "Judge CLI overwrote concurrent registration")
                self.assertEqual("judge" in registry, operation == "add")
            finally:
                (root / "release").touch()
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()

    def test_native_judge_and_stage_add_preserve_both_agents(self):
        self.assert_concurrent_mutations("add")

    def test_native_judges_preserve_both_registrations(self):
        self.assert_concurrent_mutations("add", peer="judge-other")

    def test_environment_config_write_preserves_concurrent_registration(self):
        self.assert_concurrent_mutations("add", peer="adapt")

    def test_native_judge_delete_does_not_overwrite_stage_add(self):
        self.assert_concurrent_mutations("delete")

    def test_model_execution_does_not_hold_registration_lock_and_cleanup_preserves_judge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "openclaw"
            executable.write_text(f"#!{sys.executable}\n" + CLI)
            executable.chmod(0o700)
            (root / "registry.json").write_text('{"main":{}}')
            (root / "SKILL.md").write_text("Unchanged business Skill")
            (root / "input.json").write_text("{}")
            (root / "release").touch()
            env = dict(os.environ, OPENCLAW_PATH=str(executable), REGISTRY_TEST_ROOT=str(root),
                       STAGE_SUCCEEDS="1", CLAWEVOLVE_PLAN_AGENT_REGISTRATION_LOCK=str(root / "lock"))
            stage = subprocess.Popen(
                [sys.executable, "-c", CALLER, str(SKILLS), str(root), "stage", "add"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.wait_for(root / "model-started")
                judge = subprocess.run(
                    [sys.executable, "-c", CALLER, str(SKILLS), str(root), "judge", "add"],
                    env=env, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(judge.returncode, 0, judge.stdout + judge.stderr)
                (root / "release-model").touch()
                stdout, stderr = stage.communicate(timeout=10)
                self.assertEqual(stage.returncode, 0, stdout + stderr)
                self.assertEqual(set(json.loads((root / "registry.json").read_text())), {"main", "judge"})
            finally:
                (root / "release-model").touch()
                if stage.poll() is None:
                    stage.kill()
                stage.communicate()

    def test_existing_plan_lock_is_respected_and_released_after_error(self):
        import fcntl
        sys.path.insert(0, str(SKILLS / "platform"))
        from clawevolve_runtime.agent_registry import registration_lock
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "plan.lock"
            with patch.dict(os.environ, CLAWEVOLVE_PLAN_AGENT_REGISTRATION_LOCK=str(lock),
                            CLAWEVOLVE_PLAN_AGENT_LOCK_TIMEOUT="0"):
                with lock.open("a+") as original_plan:
                    fcntl.flock(original_plan, fcntl.LOCK_EX)
                    with self.assertRaises(TimeoutError):
                        with registration_lock():
                            self.fail("must not bypass an existing Plan writer")
                    fcntl.flock(original_plan, fcntl.LOCK_UN)
                with self.assertRaisesRegex(ValueError, "CLI failed"):
                    with registration_lock():
                        raise ValueError("CLI failed")
                with registration_lock():
                    self.assertTrue(lock.exists())


if __name__ == "__main__":
    unittest.main()
