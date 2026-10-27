"""Import isolation and dependency direction protect both consumers."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "search_core"


def test_core_and_contracts_import_only_the_standard_library_and_contracts():
    allowed = sys.stdlib_module_names | {"search_core"}
    for folder in ("api", "core"):
        for path in (ROOT / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    assert node.level == 0, path
                    names = [node.module or ""]
                for name in names:
                    assert name.split(".")[0] in allowed, (path, name)
                    assert not name.startswith("search_core.adapters.http"), (path, name)
                if isinstance(node, ast.Attribute):
                    assert not (
                        isinstance(node.value, ast.Name)
                        and node.value.id == "os"
                        and node.attr in {"getenv", "environ"}
                    ), path


def test_import_does_not_load_tc_framework_or_model_clients():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from search_core.api.models import SearchCommand
from search_core.core.ranking import reciprocal_rank_fusion
assert not any(name.split('.')[0] in {'src', 'app', 'bcsfuse_internal', 'zbcsfuse_internal', 'fastapi', 'qdrant_client', 'numpy'} for name in sys.modules)
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_shared_providers_do_not_import_applications_or_read_execution_environment():
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0] not in {"app", "bcsfuse_internal", "zbcsfuse_internal"}, (path, name)
            if "config" not in path.relative_to(ROOT).parts and isinstance(node, ast.Attribute):
                assert not (isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr in {"environ", "getenv"}), path
