import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/singlebox-coverage.yml"


@pytest.mark.parametrize(
    ("coverage", "model_mode", "expected"),
    [("1", "mock", "dev_smoke"), ("1", "manual", "dev"), ("0", "mock", "dev")],
)
def test_bcsfuse_selects_offline_providers_for_mock_coverage(
    tmp_path, coverage, model_mode, expected
):
    env_file = tmp_path / "env/.env.local"
    env_file.parent.mkdir()
    env_file.write_text("export BCSFUSE_PROVIDER_MODE=dev\n", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-ec",
            """
load_repo_env_file() { if [ -f "$1" ]; then . "$1"; fi; }
source "$1"
bcsfuse_load_env
printenv BCSFUSE_PROVIDER_MODE
""",
            "test",
            str(REPO_ROOT / "singlebox/modules/bcsfuse.sh"),
        ],
        env={
            "PROJECT_ROOT": str(tmp_path),
            "BCSFUSE_RUNTIME_DIR": str(tmp_path),
            "SINGLEBOX_COVERAGE": coverage,
            "SINGLEBOX_MODEL_CONFIG_MODE": model_mode,
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == expected


def test_workflow_triggers_bcs_and_runs_canonical_gate():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("apps/bcs") >= 1
    assert "bash singlebox/ci/singlebox_coverage.sh" in workflow
    assert "python3 singlebox/ci/verify_singlebox_coverage_artifacts.py" in workflow
    assert 'SINGLEBOX_COVERAGE_BCS_LINE_MIN: "54"' in workflow
    assert 'SINGLEBOX_COVERAGE_BCS_METHOD_MIN: "48"' in workflow
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


def test_pushes_use_event_before_and_upload_phase_timings():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "github.event.before" in workflow
    assert "SINGLEBOX_BASE_REF: ${{ github.event_name == 'pull_request' && 'HEAD^1' || (github.event_name == 'push' && github.event.before || 'origin/dev') }}" in workflow
    assert "phase-timings.tsv" in workflow
    assert "git diff --name-only -z \"${base_ref}\" \"${GITHUB_SHA}\"" in workflow
    assert "git diff --name-only \"${SINGLEBOX_BASE_REF}\" --" not in workflow


def test_workflow_excludes_provider_credentials_from_artifacts():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    upload = workflow.split("- name: Upload singlebox artifacts", 1)[1]
    assert "!singlebox/.dependencies/coverage/**/collaboration-repair-provider.json" in upload
    assert "!singlebox/.dependencies/coverage/**/.repair-provider-*" in upload
