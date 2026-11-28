"""RSI ownership: Backend ingress only, one canonical runtime implementation."""

import ast
from pathlib import Path

import pytest
from tests.community.framework.fixtures import (
    app_with_testing_modules,  # noqa: F401, F811
)

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
        ("GET", "/runs/{run_id}/strategy-output"),
        ("GET", "/candidates/{candidate_id}"),
        ("GET", "/review-queue"),
        ("POST", "/candidates/{candidate_id}:approve"),
        ("POST", "/candidates/{candidate_id}:reject"),
        ("POST", "/candidates/{candidate_id}:authorize-promotion"),
        ("POST", "/candidates/{candidate_id}:apply"),
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


RETIRED_DOMAINS = (
    "evolution_run",
    "evolution_strategy",
    "evolution_ledger",
    "evolution_verify",
    "evolution_promote",
)


def test_backend_no_longer_contains_retired_domains_or_composite_builder():
    for domain in RETIRED_DOMAINS:
        assert not list((COMMUNITY / "core" / domain).rglob("*.py")), domain
    assert not (COMMUNITY / "di/evolution_acceptance/runtime.py").exists()


def test_sources_and_tests_do_not_import_retired_backend_domains():
    backend = COMMUNITY.parents[2]
    repository = backend.parents[1]
    roots = (
        backend / "src",
        backend / "tests",
        backend / "scripts",
        repository / "apps/evolution/src",
        repository / "apps/evolution/tests",
    )
    retired = tuple(f"agentclaw.community.core.{name}" for name in RETIRED_DOMAINS) + (
        "agentclaw.community.di.evolution_acceptance.runtime",
    )
    violations = []
    for root in roots:
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [f"{node.module}.{alias.name}" for alias in node.names]
                for name in imports:
                    if any(
                        name == old or name.startswith(old + ".") for old in retired
                    ):
                        violations.append(
                            f"{path.relative_to(repository)}:{node.lineno}: {name}"
                        )
    assert not violations, "\n".join(violations)
