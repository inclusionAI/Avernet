from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/singlebox-coverage.yml"


def test_workflow_triggers_bcs_and_runs_canonical_gate():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("apps/bcs") >= 1
    assert "bash singlebox/ci/singlebox_coverage.sh" in workflow
    assert "python3 singlebox/ci/verify_singlebox_coverage_artifacts.py" in workflow
    assert 'SINGLEBOX_COVERAGE_BCS_LINE_MIN: "54"' in workflow
    assert 'SINGLEBOX_COVERAGE_BCS_METHOD_MIN: "48"' in workflow
    assert 'BCS_E2E_CHANGED_LINE_MIN: "65"' in workflow
    assert "--bcs-line-min 54" in workflow
    assert "--bcs-method-min 48" in workflow
    assert "--bcs-router-min 100" in workflow
    assert "--bcs-cli-min 100" in workflow


def test_bcs_changed_line_threshold_has_no_branch_waiver():
    import yaml

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    thresholds = [
        job["env"]["BCS_E2E_CHANGED_LINE_MIN"]
        for job in workflow["jobs"].values()
        if "BCS_E2E_CHANGED_LINE_MIN" in job.get("env", {})
    ]
    assert thresholds == ["65"]


def test_oauth_production_paths_are_not_excluded_from_changed_line_coverage():
    runner = (REPO_ROOT / "apps/bcs/scripts/e2e_coverage.sh").read_text(encoding="utf-8")
    assert "e2e_unreachable_oauth" not in runner
    assert "oauth_skip_regex" not in runner
