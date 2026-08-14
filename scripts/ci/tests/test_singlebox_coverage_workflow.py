from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/singlebox-coverage.yml"


def test_workflow_triggers_bcs_and_runs_canonical_gate():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count('      - "src/bcs/**"') == 2
    assert "bash scripts/ci/singlebox_coverage.sh" in workflow
    assert "python3 scripts/ci/verify_singlebox_coverage_artifacts.py" in workflow
    assert 'SINGLEBOX_COVERAGE_BCS_LINE_MIN: "40"' in workflow
    assert 'SINGLEBOX_COVERAGE_BCS_METHOD_MIN: "36"' in workflow
    assert "--bcs-router-min 100" in workflow
    assert "--bcs-cli-min 100" in workflow
