from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/ci/task_design_guard.py"
SPEC = importlib.util.spec_from_file_location("task_design_guard", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GUARD
SPEC.loader.exec_module(GUARD)

QUALIFIED_CLASS = "agentclaw.community.core.task.task_runner.task_runner.TaskRunner"
SOURCE_PATH = "src/backend/src/agentclaw/community/core/task/task_runner/task_runner.py"
MANIFEST_PATH = "scripts/ci/task_design_guard.json"
SUBMITTERS_PATH = "docs/arch/task-design-guard-submitters.json"
PROTECTED = GUARD.ProtectedClass(
    package="agentclaw.community.core.task.task_runner",
    qualified_name=QUALIFIED_CLASS,
    module="agentclaw.community.core.task.task_runner.task_runner",
    name="TaskRunner",
    methods=("__init__", "set_delivery", "start_run"),
    source_path=SOURCE_PATH,
)

BASE_SOURCE = """\
@class_decorator
class TaskRunner(BaseRunner):
    _DELIVER_CONCURRENCY: int = 8

    def __init__(self, graph, execution_backend=None) -> None:
        self._graph = graph

    def set_delivery(self, mode: str, port: DeliveryPort) -> None:
        self._deliveries[mode] = port

    async def start_run(self, nodes: list[TaskNode]) -> list[bool]:
        return [True for _node in nodes]

    def query_status(self, task_id: str) -> Status:
        return self._graph.status(task_id)
"""


def _rules(head_source: str) -> set[str]:
    result = GUARD.compare_class_sources(BASE_SOURCE, head_source, PROTECTED)
    return {violation.rule for violation in result.violations}


def test_method_body_and_docstring_changes_are_allowed() -> None:
    head = BASE_SOURCE.replace(
        "        self._graph = graph\n",
        '        """Updated constructor docs."""\n        self._graph = normalize(graph)\n',
    ).replace(
        "        return [True for _node in nodes]\n",
        "        results = await deliver(nodes)\n        return list(results)\n",
    )

    result = GUARD.compare_class_sources(BASE_SOURCE, head, PROTECTED)

    assert result == GUARD.Comparison((), ())


@pytest.mark.parametrize(
    ("head_source", "expected_rule"),
    [
        (BASE_SOURCE.replace("BaseRunner", "OtherBase"), "TRG002"),
        (BASE_SOURCE.replace("@class_decorator", "@replacement_decorator"), "TRG003"),
        (BASE_SOURCE.replace("= 8", "= 16"), "TRG004"),
        (
            BASE_SOURCE.replace(
                "    def query_status",
                "    def _new_helper(self) -> None:\n        pass\n\n    def query_status",
            ),
            "TRG005",
        ),
        (
            BASE_SOURCE.replace(
                "    def query_status",
                "    def __new_hook__(self) -> None:\n        pass\n\n    def query_status",
            ),
            "TRG005",
        ),
        (BASE_SOURCE.replace("class TaskRunner", "class RenamedTaskRunner"), "TRG001"),
    ],
)
def test_protected_class_structure_changes_are_rejected(
    head_source: str, expected_rule: str
) -> None:
    assert expected_rule in _rules(head_source)


@pytest.mark.parametrize(
    ("old", "new", "expected_rule"),
    [
        ("async def start_run", "def start_run", "TRG102"),
        (
            "async def start_run(self, nodes: list[TaskNode])",
            "async def start_run(self, tasks: list[TaskNode])",
            "TRG104",
        ),
        (
            "async def start_run(self, nodes: list[TaskNode])",
            "async def start_run(self, nodes: tuple[TaskNode, ...])",
            "TRG104",
        ),
        (
            "async def start_run(self, nodes: list[TaskNode])",
            "async def start_run(self, nodes: list[TaskNode] = [])",
            "TRG104",
        ),
        ("-> list[bool]", "-> tuple[bool, ...]", "TRG105"),
        (
            "    async def start_run",
            "    @protected_method\n    async def start_run",
            "TRG103",
        ),
    ],
)
def test_protected_method_interface_changes_are_rejected(
    old: str, new: str, expected_rule: str
) -> None:
    assert expected_rule in _rules(BASE_SOURCE.replace(old, new))


def test_removed_protected_method_is_rejected() -> None:
    start = BASE_SOURCE.index("    def set_delivery")
    end = BASE_SOURCE.index("    async def start_run")
    head = BASE_SOURCE[:start] + BASE_SOURCE[end:]

    assert "TRG101" in _rules(head)


def test_protected_symbol_missing_from_base_is_reported() -> None:
    result = GUARD.compare_class_sources(
        "class SomethingElse:\n    pass\n", BASE_SOURCE, PROTECTED
    )

    assert result.violations == ()
    assert "absent from the base revision" in result.warnings[0]


def test_repository_manifest_resolves_current_task_runner() -> None:
    protected_classes = GUARD.load_manifest(
        (REPO_ROOT / MANIFEST_PATH).read_text(encoding="utf-8")
    )
    protected = protected_classes[0]
    source = (REPO_ROOT / protected.source_path).read_text(encoding="utf-8")

    result = GUARD.compare_class_sources(source, source, protected)

    assert protected.qualified_name == QUALIFIED_CLASS
    assert protected.methods == ("__init__", "set_delivery", "start_run")
    assert result == GUARD.Comparison((), ())


def test_repository_submitter_policy_is_valid() -> None:
    submitters = GUARD.load_guarded_submitters(
        (REPO_ROOT / SUBMITTERS_PATH).read_text(encoding="utf-8")
    )

    assert submitters == {
        "jiangj0627",
        "msjbear",
        "wen6lev57q4",
        "guok974-dot",
    }


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"version": 1, "guarded_submitters": []}),
        json.dumps({"version": 1, "guarded_submitters": ["bad_login!"]}),
        json.dumps({"version": 1, "guarded_submitters": ["Same", "same"]}),
        json.dumps({"version": 1, "guarded_submitters": ["msjbear"], "extra": True}),
    ],
)
def test_submitter_policy_rejects_invalid_content(payload: str) -> None:
    with pytest.raises(GUARD.GuardFailure):
        GUARD.load_guarded_submitters(payload)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repository: Path, relative_path: str, content: str) -> None:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest() -> str:
    return json.dumps(
        {
            "version": 1,
            "packages": [
                {
                    "package": "agentclaw.community.core.task.task_runner",
                    "classes": [
                        {
                            "class": QUALIFIED_CLASS,
                            "methods": ["__init__", "set_delivery", "start_run"],
                        }
                    ],
                }
            ],
        }
    )


def _submitters() -> str:
    return json.dumps({"version": 1, "guarded_submitters": ["msjbear", "WEN6Lev57q4"]})


def _repository(
    tmp_path: Path,
    *,
    base_source: str = BASE_SOURCE,
    submitters: str | None = None,
) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch", "dev")
    _git(repository, "config", "user.name", "Guard Test")
    _git(repository, "config", "user.email", "guard-test@example.com")
    _write(repository, SOURCE_PATH, base_source)
    _write(repository, MANIFEST_PATH, _manifest())
    _write(repository, SUBMITTERS_PATH, submitters or _submitters())
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "baseline")
    return repository, _git(repository, "rev-parse", "HEAD")


def _commit(repository: Path, path: str, content: str, message: str) -> None:
    _write(repository, path, content)
    _git(repository, "add", path)
    _git(repository, "commit", "-m", message)


def _run_guard(
    repository: Path,
    base: str,
    *,
    actor: str = "msjbear",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--base",
            base,
            "--head",
            "HEAD",
            "--actor",
            actor,
            "--manifest",
            MANIFEST_PATH,
            "--submitters",
            SUBMITTERS_PATH,
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )


def test_guarded_pr_author_is_blocked_on_structural_change(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _commit(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
        "change protected parameter",
    )

    result = _run_guard(repository, base)

    assert result.returncode == 1
    assert "TaskRunner design guard failed" in result.stderr
    assert "TRG104" in result.stderr


def test_owner_login_bypasses_silently_before_control_file_check(
    tmp_path: Path,
) -> None:
    repository, base = _repository(tmp_path)
    _commit(repository, MANIFEST_PATH, "not-json\n", "owner changes policy")

    result = _run_guard(repository, base, actor="RegRecall")

    assert result.returncode == 0
    assert "owner @RegRecall bypass" in result.stdout
    assert result.stderr == ""


def test_unlisted_pr_author_skips_structural_check(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _commit(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
        "change protected parameter",
    )

    result = _run_guard(repository, base, actor="unlisted-user")

    assert result.returncode == 0
    assert "not in the guarded submitter policy" in result.stdout


def test_guarded_submitter_matching_is_case_insensitive(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _commit(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
        "change protected parameter",
    )

    result = _run_guard(repository, base, actor="wen6LEV57Q4")

    assert result.returncode == 1
    assert "TRG104" in result.stderr


@pytest.mark.parametrize("actor", ["msjbear", "unlisted-user"])
def test_non_owner_cannot_change_guard_control_files(
    tmp_path: Path, actor: str
) -> None:
    repository, base = _repository(tmp_path)
    _commit(repository, MANIFEST_PATH, "not-json\n", "tamper with guard policy")

    result = _run_guard(repository, base, actor=actor)

    assert result.returncode == 1
    assert "TRG900" in result.stderr
    assert MANIFEST_PATH in result.stderr


def test_head_syntax_error_is_a_blocking_policy_failure(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _commit(repository, SOURCE_PATH, "class TaskRunner(:\n", "break protected source")

    result = _run_guard(repository, base)

    assert result.returncode == 1
    assert "TRG901" in result.stderr


def test_trusted_submitter_policy_failure_returns_degraded_status(
    tmp_path: Path,
) -> None:
    repository, base = _repository(tmp_path, submitters="not-json\n")
    _commit(repository, "README.md", "unrelated\n", "unrelated change")

    result = _run_guard(repository, base)

    assert result.returncode == 2
    assert "design guard degraded" in result.stderr
    assert "not valid JSON" in result.stderr


def test_trusted_base_syntax_error_returns_degraded_status(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path, base_source="class TaskRunner(:\n")
    _commit(repository, SOURCE_PATH, "class TaskRunner(:\n# still invalid\n", "change")

    result = _run_guard(repository, base)

    assert result.returncode == 2
    assert "design guard degraded" in result.stderr


def test_guard_ignores_uncommitted_structural_change(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _commit(repository, "README.md", "committed unrelated change\n", "unrelated")
    _write(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
    )

    result = _run_guard(repository, base)

    assert result.returncode == 0
    assert "no protected source changes" in result.stdout


def test_pre_push_dispatcher_does_not_run_task_design_guard() -> None:
    dispatcher = (REPO_ROOT / "scripts/ci/pre_push.sh").read_text(encoding="utf-8")

    assert "task_design_guard.py" not in dispatcher


def test_workflow_only_targets_pull_requests_to_dev() -> None:
    workflow = (REPO_ROOT / ".github/workflows/task-design-guard.yml").read_text(
        encoding="utf-8"
    )

    assert "pull_request_target:" in workflow
    assert "      - dev" in workflow
    assert "  push:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "paths:" not in workflow
    assert "contents: read" in workflow
    assert "statuses: write" in workflow
    assert "persist-credentials: false" in workflow
    assert "continue-on-error: true" in workflow
    assert "CHECKOUT_OUTCOME" in workflow
    assert "name: Publish TaskRunner design guard" in workflow
    assert workflow.count('context: "TaskRunner design guard"') == 2
    assert "github.event.pull_request.head.sha" in workflow
    assert "github.rest.repos.createCommitStatus" in workflow
    assert 'state: "pending"' in workflow
    assert "steps.evaluate.outputs.state" in workflow
    assert '--actor "$PR_AUTHOR"' in workflow
