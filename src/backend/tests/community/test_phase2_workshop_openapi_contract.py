"""Contract checks for explicit Phase 2 Space Skill OpenAPI handlers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agentclaw.community.adapters.http.openapi_v1.contracts import ErrorEnvelope
from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.space_skills.router import (
    CONTRACT_ONLY_MESSAGE,
    CONTRACT_STATUS,
    router,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    require_user_id,
    refuse_app_only_caller,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[refuse_app_only_caller] = lambda: None
    app.dependency_overrides[require_user_id] = lambda: "user-1"
    app.include_router(router)
    return app


def test_phase2_contract_covers_the_workshop_surface():
    paths = _app().openapi()["paths"]
    operations = [
        operation
        for path, item in paths.items()
        if path.startswith("/openapi/v1/bots/spaces/{space_id}/skills")
        for operation in item.values()
    ]

    assert len(operations) == 30
    assert all(
        operation["x-contract-status"] == CONTRACT_STATUS for operation in operations
    )
    assert len(router.routes) == len(operations)
    assert all(
        "Idempotency-Key"
        in {parameter["name"] for parameter in operation["parameters"]}
        for operation in operations
        if operation["operationId"].startswith(
            (
                "create_space_skill",
                "import_space_skill_from_git",
                "create_upgrade_draft",
                "create_publication",
                "retry_materialization",
            )
        )
    )
    assert "Idempotency-Key" not in {
        parameter["name"]
        for parameter in paths[
            "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/replace"
        ]["post"]["parameters"]
    }


def test_every_placeholder_is_declared_as_a_path_parameter():
    paths = _app().openapi()["paths"]
    for path, item in paths.items():
        if not path.startswith("/openapi/v1/bots/spaces/{space_id}/skills"):
            continue
        placeholders = {part[1:-1] for part in path.split("/") if part.startswith("{")}
        for operation in item.values():
            path_parameters = {
                parameter["name"]
                for parameter in operation["parameters"]
                if parameter["in"] == "path"
            }
            assert placeholders <= path_parameters


def test_folder_upload_commands_use_multipart_schema_and_contract_only_response():
    app = _app()
    schema = app.openapi()
    paths = schema["paths"]
    for path in (
        "/openapi/v1/bots/spaces/{space_id}/skills",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/replace",
    ):
        operation = paths[path]["post"]
        assert set(operation["requestBody"]["content"]) == {"multipart/form-data"}
        assert operation["responses"]["501"]["headers"]["x-contract-status"]
        payload_schema = operation["requestBody"]["content"]["multipart/form-data"][
            "schema"
        ]
        payload_component = schema["components"]["schemas"][
            payload_schema["$ref"].rsplit("/", maxsplit=1)[-1]
        ]
        assert set(payload_component["properties"]) == {"files", "file_paths"}

    git_import_schema = paths[
        "/openapi/v1/bots/spaces/{space_id}/skills/import-from-git"
    ]["post"]["requestBody"]["content"]["application/json"]["schema"]
    git_import_component = schema["components"]["schemas"][
        git_import_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    ]
    assert set(git_import_component["properties"]) == {
        "repository_url",
        "branch",
        "subdir",
    }

    client = TestClient(app)
    response = client.get(
        "/openapi/v1/bots/spaces/1/skills/example/draft",
        params={"user_id": "user-1"},
    )
    assert response.status_code == 501
    assert response.headers["x-contract-status"] == CONTRACT_STATUS
    assert response.json() == ErrorEnvelope(
        code=501000,
        message=CONTRACT_ONLY_MESSAGE,
        data=None,
        request_id="",
    ).model_dump()


def test_phase2_router_is_registered_without_replacing_existing_skill_list():
    app = FastAPI()
    app.include_router(build_public_router())
    skill_paths = app.openapi()["paths"]
    collection = "/openapi/v1/bots/spaces/{space_id}/skills"

    assert skill_paths[collection]["get"].get("x-contract-status") is None
    assert skill_paths[collection]["post"]["x-contract-status"] == CONTRACT_STATUS


_CONTRACT_REQUESTS = (
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills",
        {
            "data": {"file_paths": '["SKILL.md"]'},
            "files": [("files", ("SKILL.md", b"---\nname: demo\n---", "text/markdown"))],
            "headers": {"Idempotency-Key": "create"},
        },
        id="create-from-folder",
    ),
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills/import-from-git",
        {"json": {"repository_url": "https://example.test/skill.git"}, "headers": {"Idempotency-Key": "import"}},
        id="import-from-git",
    ),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1", {}, id="detail"),
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills/skill-1/draft/upgrade",
        {"headers": {"Idempotency-Key": "upgrade"}},
        id="upgrade-draft",
    ),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/draft", {}, id="draft"),
    pytest.param("delete", "/openapi/v1/bots/spaces/1/skills/skill-1/draft", {}, id="abandon-draft"),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/draft/files", {}, id="draft-files"),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/draft/files/a.txt", {}, id="draft-file"),
    pytest.param(
        "put",
        "/openapi/v1/bots/spaces/1/skills/skill-1/draft/files/a.txt",
        {"json": {"content": "mock"}},
        id="write-draft-file",
    ),
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills/skill-1/draft/replace",
        {
            "data": {"file_paths": '["SKILL.md"]'},
            "files": [("files", ("SKILL.md", b"---\nname: demo\n---", "text/markdown"))],
        },
        id="replace-draft-from-folder",
    ),
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills/skill-1/draft/refresh-from-git",
        {"json": {"confirm_overwrite": True}},
        id="refresh-draft",
    ),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/grants", {}, id="grants"),
    pytest.param("put", "/openapi/v1/bots/spaces/1/skills/skill-1/managers/user-2", {}, id="grant-manager"),
    pytest.param("delete", "/openapi/v1/bots/spaces/1/skills/skill-1/managers/user-2", {}, id="revoke-manager"),
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills/skill-1/owner-transfer",
        {"json": {"target_user_id": "user-2", "reason": "handover"}},
        id="transfer-owner",
    ),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/draft/lease", {}, id="lease"),
    pytest.param("put", "/openapi/v1/bots/spaces/1/skills/skill-1/draft/lease", {}, id="acquire-lease"),
    pytest.param("delete", "/openapi/v1/bots/spaces/1/skills/skill-1/draft/lease", {}, id="release-lease"),
    pytest.param("post", "/openapi/v1/bots/spaces/1/skills/skill-1/draft/lease/takeover", {}, id="takeover-lease"),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/versions", {}, id="versions"),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/versions/1", {}, id="version"),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/versions/1/files", {}, id="version-files"),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/versions/1/files/a.txt", {}, id="version-file"),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/upgrade-impact", {}, id="upgrade-impact"),
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills/skill-1/publications",
        {"json": {}, "headers": {"Idempotency-Key": "publish"}},
        id="publish",
    ),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/publications", {}, id="publications"),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/publications/attempt-1", {}, id="publication"),
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills/skill-1/versions/1/materialization-retry",
        {"headers": {"Idempotency-Key": "retry"}},
        id="retry-materialization",
    ),
    pytest.param("get", "/openapi/v1/bots/spaces/1/skills/skill-1/retirement-impact", {}, id="retirement-impact"),
    pytest.param(
        "post",
        "/openapi/v1/bots/spaces/1/skills/skill-1/retirement",
        {"json": {"reason": "retire"}},
        id="retire",
    ),
)


@pytest.mark.parametrize("method,path,request_kwargs", _CONTRACT_REQUESTS)
def test_every_contract_only_handler_returns_the_501_envelope(
    method: str, path: str, request_kwargs: dict
):
    response = TestClient(_app()).request(
        method,
        path,
        params={"user_id": "user-1"},
        **request_kwargs,
    )

    assert response.status_code == 501
    assert response.headers["x-contract-status"] == CONTRACT_STATUS
    assert response.json()["code"] == 501000
    assert response.json()["data"] is None
