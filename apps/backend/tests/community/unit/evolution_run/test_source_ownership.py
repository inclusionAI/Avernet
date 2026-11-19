"""RSI ownership: Backend ingress only, one canonical runtime implementation."""

import ast
import importlib
from pathlib import Path

import pytest

from tests.community.framework.fixtures import app_with_testing_modules  # noqa: F401, F811


COMMUNITY = Path(__file__).resolve().parents[4] / "src/agentclaw/community"


@pytest.mark.usefixtures("app_with_testing_modules")
def test_default_backend_keeps_bot_ingress_but_not_worker(request):
    app = request.getfixturevalue("app_with_testing_modules")
    from tests.community.framework.coverage_gate import _live_routes

    routes = _live_routes(app)
    prefix = "/openapi/v1/bots/{bot_id}/evolution"
    expected = {
        ("GET", "/strategies"),
        ("GET", "/bindings"),
        ("PUT", "/bindings"),
        ("POST", "/runs"),
        ("GET", "/runs"),
        ("GET", "/runs/{run_id}"),
        ("POST", "/runs/{run_id}:cancel"),
        ("GET", "/runs/{run_id}/candidates"),
        ("GET", "/runs/{run_id}/evidence"),
    }
    assert {(method, prefix + path) for method, path in expected} <= routes
    assert not any(path.startswith("/openapi/v1/evolution/jobs") for _, path in routes)


def test_default_composition_does_not_import_acceptance_runtime():
    for relative in ("di/container.py", "adapters/http/app.py"):
        tree = ast.parse((COMMUNITY / relative).read_text())
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert not any(
            "evolution_acceptance" in name
            or name.endswith(("evolution_runtime_module", "evolution_jobs_module"))
            or ".http.evolution_jobs" in name
            for name in imports
        ), relative


def test_legacy_modules_are_aliases_not_second_implementations():
    for domain, modules in {
        "evolution_run": (
            "store",
            "worker",
            "bindings_service",
            "job_service",
            "events",
        ),
        "evolution_strategy": ("models", "registration"),
        "evolution_ledger": ("ledger", "models"),
        "evolution_verify": ("service", "models"),
    }.items():
        for module in modules:
            old = importlib.import_module(f"agentclaw.community.core.{domain}.{module}")
            canonical = importlib.import_module(
                f"avernet_evolution.core.{domain}.{module}"
            )
            assert old is canonical, (domain, module)


def test_legacy_builder_points_to_explicit_acceptance_composition():
    from agentclaw.community.core.evolution_run import build_evolution_runtime
    from agentclaw.community.core.evolution_run.app import (
        build_evolution_runtime as legacy,
    )
    from agentclaw.community.di.evolution_acceptance.runtime import (
        build_evolution_runtime as explicit,
    )

    assert build_evolution_runtime is legacy is explicit
