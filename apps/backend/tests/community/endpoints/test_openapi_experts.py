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


def seed_creation(world):
    from agentclaw.community.core.expert_market.creation import ExpertCreationConfig
    from tests.community.endpoints.test_openapi_create_with_manifest import _seed_verifier
    seed_expert(world)
    _seed_verifier(world)
    world.injector.binder.bind(ExpertCreationConfig, to=ExpertCreationConfig("https://example.com/experts.git"))


def creation_body():
    return {"bot_name": "Expert created Bot", "bot_desc": "My expert", "engine": "openclaw", "cluster_name": "ACRA", "bot_type": "personal"}


from tests.community.endpoints.test_openapi_create_with_manifest import _HEADERS as CREATE_HEADERS, _QUERY as CREATE_QUERY


def assert_bound_creation(response, world):
    from agentclaw.community.core.bot_management.manifest_seam import ManifestCreationSeam
    from agentclaw.community.api.bot_config_manifest_service import BotConfigManifestServiceProtocol
    from agentclaw.community.core.bot_config_manifest.binding import manifest_digest
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    data = response.json()["data"]
    assert set(data) == {"bot_id", "iframe_url", "redirect_url"}
    owner = CREATE_QUERY["user_id"]
    job = world.get(ManifestCreationSeam).find_job(entity_id=owner, bot_id=data["bot_id"])
    spec = job.payload["spec"]
    binding = spec["manifest_binding"]
    assert binding["entity_id"] == owner
    assert binding["bot_id"] == data["bot_id"]
    record = world.get(BotConfigManifestServiceProtocol).get(entity_id=owner, bot_id=data["bot_id"])
    assert manifest_digest(record.document) == binding["manifest_digest"]
    assert spec["expert_origin"]["creation_task_key"] == job.idempotency_key
    assert len(spec["expert_origin"]["source_commit"]) == 40
    assert spec["expert_origin"]["expert_name"] == "写作专家"
    assert world.get(BotRepository).get_by_id_and_entity(data["bot_id"], owner) is None


@endpoint_test(
    method="POST", path="/openapi/v1/experts/{expert_id}/bots", scenario="identity_template_accepted",
    seed=seed_creation,
    input=CaseInput(path_params={"expert_id": "writer"}, headers=CREATE_HEADERS, query_params=CREATE_QUERY, json_body=creation_body()),
    expect=ExpectSuccess(status=202, json_contains={"code": 202000}),
    extra_assertions=(assert_bound_creation,),
)
def expert_creation_accepted():
    pass


@endpoint_test(
    method="POST", path="/openapi/v1/experts/{expert_id}/bots", scenario="not_discoverable",
    seed=seed_creation,
    input=CaseInput(path_params={"expert_id": "missing"}, headers=CREATE_HEADERS, query_params=CREATE_QUERY, json_body=creation_body()),
    expect=ExpectError(status=404),
)
def expert_creation_missing():
    pass
