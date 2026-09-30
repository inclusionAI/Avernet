from pathlib import Path


def test_environment_specific_workflow_yaml_is_not_public() -> None:
    workflow_dir = Path(__file__).resolve().parents[2] / "clawbench-workflow" / "clawbench-pack" / "workflows"
    assert not list(workflow_dir.glob("*.yaml"))
