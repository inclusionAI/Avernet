import os
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
    assert "singlebox/ci/tests/test_singlebox_coverage_workflow.py" in workflow
    assert 'SINGLEBOX_COVERAGE_BCS_LINE_MIN: "54"' in workflow
    assert 'SINGLEBOX_COVERAGE_BCS_METHOD_MIN: "48"' in workflow
    assert "--bcs-router-min 100" in workflow
    assert "--bcs-cli-min 100" in workflow


@pytest.mark.parametrize(
    "changed_path", ["apps/bcsfuse/src/app/tc/providers.py", "rust-toolchain.toml"]
)
def test_relevant_change_runs_singlebox_coverage(tmp_path, changed_path):
    import yaml

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "--initial-branch=dev")
    git("config", "user.name", "CI Test")
    git("config", "user.email", "ci-test@example.com")
    git("commit", "--allow-empty", "-m", "baseline")
    base = git("rev-parse", "HEAD")
    changed = tmp_path / changed_path
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("# Singlebox-relevant change\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "change Singlebox input")

    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["singlebox-coverage"]
    detect = next(step["run"] for step in job["steps"] if step.get("id") == "changes")
    output = tmp_path / "output"
    subprocess.run(
        ["bash", "-e", "-c", detect],
        cwd=tmp_path,
        check=True,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_SHA": "HEAD",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_ENV": str(tmp_path / "env"),
            "SINGLEBOX_BASE_REF": base,
        },
    )
    assert output.read_text().strip() == "skip=false"


def test_workflow_installs_pinned_protoc_without_apt():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    install_step = workflow.split("- name: Install protoc", 1)[1].split(
        "- name: Show protoc version", 1
    )[0]

    assert "uses: arduino/setup-protoc@v3" in install_step
    assert 'version: "21.12"' in install_step
    assert "repo-token: ${{ secrets.GITHUB_TOKEN }}" in install_step
    assert "apt-get" not in install_step
    assert "run: protoc --version" in workflow


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


def test_native_bm25_has_a_ready_qdrant_server():
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = document["jobs"]["singlebox-coverage"]
    qdrant = job.get("services", {}).get("qdrant", {})
    assert qdrant.get("image") == "qdrant/qdrant:v1.20.0"
    assert set(qdrant.get("ports", [])) == {"6333:6333", "6334:6334"}
    env = job.get("env", {})
    assert env.get("QDRANT_MODE") == "server"
    assert env.get("QDRANT_URL") == "http://127.0.0.1:6333"
    assert env.get("QDRANT_GRPC_PORT") == "6334"
    steps = WORKFLOW.read_text(encoding="utf-8")
    assert "curl --retry 20 --retry-connrefused --retry-delay 1 --fail http://127.0.0.1:6333/readyz" in steps
    template = (REPO_ROOT / "apps/bcsfuse/.env.example").read_text(encoding="utf-8")
    assert 'export QDRANT_MODE="server"' in template
    assert 'export QDRANT_URL="http://127.0.0.1:6333"' in template
    assert 'export QDRANT_GRPC_PORT="6334"' in template


def test_bcsfuse_changes_trigger_singlebox_coverage():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    detection = workflow.split("- name: Detect singlebox-relevant changes vs base", 1)[1]
    detection = detection.split("- name: Mark singlebox coverage as passed", 1)[0]
    assert "            apps/bcsfuse \\\n" in detection


def test_singlebox_has_one_cargo_cache_and_excludes_only_rebuildable_dependencies():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    upload = workflow.split("- name: Upload singlebox artifacts", 1)[1]

    assert "~/.cargo/registry" not in workflow
    assert "~/.cargo/git" not in workflow
    assert workflow.count("uses: Swatinem/rust-cache@") == 1
    assert "cache-workspace-crates: false" in workflow
    assert "cache-bin: false" in workflow
    assert "save-if: ${{ github.event_name == 'push' }}" in workflow

    assert "!**/node_modules/**" in upload
    for preserved in (
        "singlebox/.dependencies/logs/**",
        "singlebox/.dependencies/coverage/singlebox/**",
        "singlebox/.dependencies/standalone/**",
        "singlebox/.dependencies/coverage/singlebox/reports/phase-timings.tsv",
        "!singlebox/.dependencies/coverage/**/collaboration-repair-provider.json",
        "!singlebox/.dependencies/coverage/**/.repair-provider-*",
    ):
        assert preserved in upload

    assert "singlebox/ci/tests/test_bcs_panel_asset.py" in workflow
