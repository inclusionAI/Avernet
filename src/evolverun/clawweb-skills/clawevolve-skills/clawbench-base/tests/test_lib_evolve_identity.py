import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "lib_evolve_identity.py"
SPEC = importlib.util.spec_from_file_location("lib_evolve_identity", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_non_evolve_agent_id_keeps_legacy_name():
    assert MODULE.task_scoped_agent_id("bench-antchat-glm-5-1", "a1b2c3d4", "") \
        == "bench-antchat-glm-5-1-a1b2c3d4"
    assert MODULE.task_scoped_agent_id("bench-judge-antchat-glm-5-1", task_id="") \
        == "bench-judge-antchat-glm-5-1"


def test_evolve_agent_ids_share_task_marker_and_fit_openclaw_limit():
    task_id = "EV-20260828-ABCDEF12"
    candidate = MODULE.task_scoped_agent_id("bench-antchat-glm-5-1", "a1b2c3d4", task_id)
    judge = MODULE.task_scoped_agent_id("bench-judge-antchat-glm-5-1", task_id=task_id)

    assert "ev-20260828-abcdef12" in candidate
    assert "ev-20260828-abcdef12" in judge
    assert len(candidate) <= 64
    assert len(judge) <= 64


def test_long_identity_is_stable_and_bounded():
    first = MODULE.task_scoped_agent_id("bench-" + "model-" * 20, "deadbeef", "EV-" + "x" * 100)
    second = MODULE.task_scoped_agent_id("bench-" + "model-" * 20, "deadbeef", "EV-" + "x" * 100)

    assert first == second
    assert len(first) <= 64
    assert "deadbeef" in first
