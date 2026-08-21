from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET_BRANCH = "dev_refactory_collaboration"
WORKFLOW_PATHS = (
    REPO_ROOT / ".github/workflows/unit-tests.yml",
    REPO_ROOT / ".github/workflows/singlebox-coverage.yml",
    REPO_ROOT / ".github/workflows/architecture-checks.yml",
    REPO_ROOT / ".github/workflows/e2e-tests.yml",
)


def _event_branches(workflow_path: Path, event: str) -> list[str]:
    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    event_index = lines.index(f"  {event}:")
    branches_index = lines.index("    branches:", event_index + 1)

    branches: list[str] = []
    for line in lines[branches_index + 1 :]:
        if not line.startswith("      - "):
            break
        branches.append(line.removeprefix("      - ").strip("'\""))
    return branches


def test_refactory_collaboration_branch_triggers_required_workflows() -> None:
    for workflow_path in WORKFLOW_PATHS:
        for event in ("pull_request", "push"):
            assert TARGET_BRANCH in _event_branches(workflow_path, event), (
                f"{workflow_path.name} must run on {event} for {TARGET_BRANCH}"
            )
