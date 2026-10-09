"""The acceptance entrypoint must never implicitly target a running service."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(tmp_path):
    python = tmp_path / "python-probe"
    python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'external': "
        "os.getenv('BCSFUSE_RUN_EXTERNAL_ACCEPTANCE'), 'mysql': "
        "os.getenv('BCSFUSE_RUN_MYSQL_INTEGRATION')}))\n"
    )
    python.chmod(0o755)

    def run(*args, **extra_env):
        env = dict(os.environ, PYTHON=str(python))
        for key in ("BCSFUSE_RUN_EXTERNAL_ACCEPTANCE", "BCSFUSE_RUN_MYSQL_INTEGRATION",
                    "BCSFUSE_ACCEPTANCE_URL", "BCSFUSE_AUTH_TOKEN"):
            env.pop(key, None)
        env.update(extra_env)
        return subprocess.run(
            ["bash", str(ROOT / "scripts/run_targeted_tests.sh"), *args],
            env=env, capture_output=True, text=True, check=False,
        )

    return run


@pytest.mark.parametrize("args,mysql", [((), "0"), (("--mysql",), "1")])
def test_default_and_mysql_run_only_isolated_acceptance(runner, args, mysql):
    result = runner(*args)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "tests/integration/test_isolated_runtime_acceptance.py" in payload["args"]
    assert payload["mysql"] == mysql
    assert payload["external"] == "0"


def test_external_target_requires_url_and_token(runner):
    result = runner("--external")
    assert result.returncode != 0
    assert "BCSFUSE_ACCEPTANCE_URL" in result.stderr


def test_external_mode_uses_explicit_target_without_printing_credentials(runner):
    secret = "runner-probe-secret"
    result = runner("--external", BCSFUSE_ACCEPTANCE_URL="https://example.test",
                    BCSFUSE_AUTH_TOKEN=secret)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["external"] == "1"
    assert "tests/integration/test_opencore_runtime_real_services_e2e_core.py" in payload["args"]
    assert secret not in result.stdout + result.stderr


def test_unknown_option_fails_without_running_tests(runner):
    assert runner("--typo").returncode != 0


@pytest.mark.parametrize("option,mysql", [("--core", "0"), ("--core-mysql", "1")])
def test_core_selection_covers_search_fusion_and_lifecycle_not_historical_tools(runner, option, mysql):
    result = runner(option)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    paths = payload["args"]
    assert "tests/integration/test_isolated_runtime_acceptance.py" in paths
    assert "tests/integration/test_group_fusion_flow.py" in paths
    assert "tests/integration/test_g9_core_acceptance.py" in paths
    assert "tests/unit/application/test_worker_candidate_recommendation_impl.py" in paths
    assert "tests/contract/test_fusion_profile_store_wiring.py" in paths
    assert payload["mysql"] == mysql
    assert payload["external"] == "0"
    assert all((ROOT / path).is_file() for path in paths if path.startswith("tests/"))
    assert not any("openclaw" in path or "backup_restore" in path or "performance_smoke" in path for path in paths)
    assert "tests/smoke/test_g9_fusion.py" not in paths  # old live script swallows failures
