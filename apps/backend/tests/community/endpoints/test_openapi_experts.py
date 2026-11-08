"""Framework-driven happy/error coverage for expert discovery."""

from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.core.expert_market.scanner import ExpertScanner
from tests.community.core.expert_market.test_catalog import write_expert
from tests.community.endpoints.test_openapi_bot_public_catalog import (
    _boot_verifier,
    _principal,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


def seed_expert(world):
    _boot_verifier()
    with TemporaryDirectory() as folder:
        root = Path(folder)
        write_expert(root)

        def git(*args):
            return subprocess.run(
                ["git", *args], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip()

        git("init")
        git("add", "agents")
        git(
            "-c",
            "user.name=Expert fixture",
            "-c",
            "user.email=expert@example.test",
            "commit",
            "-m",
            "Expert fixture",
        )
        world.get(ExpertScanner).scan_git(
            root,
            source_commit=git("rev-parse", "HEAD"),
            scope=ExpertScope("test", "dev", "aiworkbench"),
        )


@endpoint_test(
    method="GET",
    path="/openapi/v1/experts",
    scenario="persisted_expert",
    seed=seed_expert,
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()}, query_params={"keyword": "PRD"}
    ),
    expect=ExpectSuccess(status=200, json_contains={"data": {"total": 1}}),
)
def list_experts_ok():
    pass


@endpoint_test(
    method="GET",
    path="/openapi/v1/experts",
    scenario="invalid_page",
    seed=lambda world: _boot_verifier(),
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()}, query_params={"page_size": 101}
    ),
    expect=ExpectError(status=422),
)
def list_experts_error():
    pass


@endpoint_test(
    method="GET",
    path="/openapi/v1/experts/categories",
    scenario="persisted_categories",
    seed=seed_expert,
    input=CaseInput(headers={PRINCIPAL_HEADER: _principal()}),
    expect=ExpectSuccess(status=200, json_contains={"data": ["内容"]}),
)
def expert_categories_ok():
    pass


@endpoint_test(
    method="GET",
    path="/openapi/v1/experts/categories",
    scenario="missing_principal",
    expect=ExpectError(status=401),
)
def expert_categories_error():
    pass


@endpoint_test(
    method="GET",
    path="/openapi/v1/experts/{expert_id}",
    scenario="persisted_detail",
    seed=seed_expert,
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()}, path_params={"expert_id": "writer"}
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "expert_id": "writer",
                "identity": [{"type": "AGENTS.md", "content": "原文正文"}],
            }
        },
    ),
)
def expert_detail_ok():
    pass


@endpoint_test(
    method="GET",
    path="/openapi/v1/experts/{expert_id}",
    scenario="missing_expert",
    seed=lambda world: _boot_verifier(),
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()}, path_params={"expert_id": "absent"}
    ),
    expect=ExpectError(status=404),
)
def expert_detail_error():
    pass
