from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/ci/task_design_guard.py"
SPEC = importlib.util.spec_from_file_location("task_design_guard", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GUARD
SPEC.loader.exec_module(GUARD)

QUALIFIED_CLASS = (
    "agentclaw.community.core.task.task_runner.task_runner.TaskRunner"
)
SOURCE_PATH = (
    "src/backend/src/agentclaw/community/core/task/task_runner/task_runner.py"
)
PROTECTED = GUARD.ProtectedClass(
    package="agentclaw.community.core.task.task_runner",
    qualified_name=QUALIFIED_CLASS,
    module="agentclaw.community.core.task.task_runner.task_runner",
    name="TaskRunner",
    methods=("__init__", "set_delivery", "start_run"),
    source_path=SOURCE_PATH,
)

BASE_SOURCE = '''\
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
'''


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

    assert result.violations == ()
    assert result.warnings == ()


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


def test_protected_symbol_missing_from_base_warns_and_passes() -> None:
    result = GUARD.compare_class_sources(
        "class SomethingElse:\n    pass\n", BASE_SOURCE, PROTECTED
    )

    assert result.violations == ()
    assert "absent from the base revision" in result.warnings[0]


def test_repository_manifest_resolves_current_task_runner() -> None:
    protected_classes = GUARD.load_manifest(
        (REPO_ROOT / "scripts/ci/task_design_guard.json").read_text(encoding="utf-8")
    )
    protected = protected_classes[0]
    source = (REPO_ROOT / protected.source_path).read_text(encoding="utf-8")

    result = GUARD.compare_class_sources(source, source, protected)

    assert protected.qualified_name == QUALIFIED_CLASS
    assert protected.methods == ("__init__", "set_delivery", "start_run")
    assert result == GUARD.Comparison((), ())


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch", "dev")
    _git(repository, "config", "user.name", "Guard Test")
    _git(repository, "config", "user.email", "guard-test@example.com")
    _write(repository, SOURCE_PATH, BASE_SOURCE)
    _write(repository, "scripts/ci/task_design_guard.json", _manifest())
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "baseline")
    return repository, _git(repository, "rev-parse", "HEAD")


def _run_guard(repository: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--base",
            base,
            "--head",
            "HEAD",
            "--manifest",
            "scripts/ci/task_design_guard.json",
        ],
        cwd=repository,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_command_rejects_confirmed_structural_change(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _write(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "change protected parameter")

    result = _run_guard(repository, base)

    assert result.returncode == 1
    assert "TaskRunner design guard failed" in result.stderr
    assert "TRG104" in result.stderr


def test_command_owner_email_bypasses_silently(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _write(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "change protected parameter")
    _git(repository, "config", "user.email", "regrecall@gmail.com")

    result = _run_guard(repository, base)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_command_invalid_manifest_warns_and_fails_open(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _write(repository, "scripts/ci/task_design_guard.json", "not-json\n")
    _write(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "break manifest and protected parameter")

    result = _run_guard(repository, base)

    assert result.returncode == 0
    assert "warning: TaskRunner design guard skipped" in result.stderr
    assert "not valid JSON" in result.stderr


def test_command_ignores_uncommitted_structural_change(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _write(repository, "README.md", "committed unrelated change\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "unrelated change")
    _write(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
    )

    result = _run_guard(repository, base)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_pre_push_dispatcher_runs_guard_in_lint_only_mode(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    dispatcher = repository / "scripts/ci/pre_push.sh"
    dispatcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "scripts/ci/pre_push.sh", dispatcher)
    dispatcher.chmod(0o755)
    _write(repository, "src/bcs/feature.rs", "// unrelated BCS change\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "change BCS")

    result = subprocess.run(
        [str(dispatcher), "--base", base, "--head", "HEAD", "--dry-run"],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert "mode: lint-only" in result.stdout
    assert "scripts/ci/task_design_guard.py" in result.stdout


def test_owner_bypass_keeps_backend_sast_gate_active(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    dispatcher = repository / "scripts/ci/pre_push.sh"
    guard = repository / "scripts/ci/task_design_guard.py"
    sast = repository / "scripts/ci/python_sast_local.sh"
    dispatcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "scripts/ci/pre_push.sh", dispatcher)
    shutil.copy2(SCRIPT, guard)
    dispatcher.chmod(0o755)
    sast.write_text("#!/usr/bin/env bash\necho BACKEND_SAST_RAN\n", encoding="utf-8")
    sast.chmod(0o755)
    _write(
        repository,
        SOURCE_PATH,
        BASE_SOURCE.replace("nodes: list[TaskNode]", "tasks: list[TaskNode]"),
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "change protected parameter")
    _git(repository, "config", "user.email", "regrecall@gmail.com")

    result = subprocess.run(
        [str(dispatcher), "--base", base, "--head", "HEAD"],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert "BACKEND_SAST_RAN" in result.stdout
    assert "TaskRunner design guard passed" not in result.stdout
    assert "TaskRunner design guard" not in result.stderr
