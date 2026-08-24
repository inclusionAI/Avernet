"""Contract and endpoint tests for the Skill Workbench publish-status API."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.skills.router import (
    publish_status_router,
)
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)


class _SkillCenter:
    def __init__(self, response: object) -> None:
        self.response = response
        self.skill_code: str | None = None

    def query_publish_status(self, skill_code: str) -> dict:
        self.skill_code = skill_code
        return self.response  # type: ignore[return-value]


def _app(client: _SkillCenter) -> TestClient:
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillCenterClient, to=client)

    app = FastAPI()
    app.include_router(publish_status_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "user-1"}
    attach_injector(app, Injector([Bindings()]))
    mount_public_error_handlers(app)
    return TestClient(app)


def test_publish_status_contract_is_open_and_does_not_expose_sc_credentials():
    app = FastAPI()
    app.include_router(publish_status_router)
    operation = app.openapi()["paths"][
        "/openapi/v1/bots/skills/{skill_code}/publish/status"
    ]["get"]

    parameters = operation["parameters"]
    assert {item["name"] for item in parameters} == {"skill_code"}
    assert parameters[0]["in"] == "path"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("Envelope_SkillPublishStatus_")

    properties = app.openapi()["components"]["schemas"]["SkillPublishStatus"][
        "properties"
    ]
    assert {
        "skillCode",
        "name",
        "status",
        "statusDesc",
        "source",
        "version",
        "isCompleted",
        "isSuccess",
        "errorMsg",
        "releaseTime",
        "standardCheckResult",
        "securityCheckReport",
    } <= set(properties)
    assert not {"appKey", "code", "token"} & set(properties)


def test_publish_status_wraps_sc_response_and_preserves_reports():
    sc = _SkillCenter(
        {
            "success": True,
            "data": {
                "skillCode": "skill-42",
                "name": "Demo",
                "status": "CHECKING",
                "statusDesc": "检查中",
                "source": "teamclaw",
                "version": "1.2.0",
                "isCompleted": False,
                "isSuccess": False,
                "standardCheckResult": {"passed": True},
                "securityCheckReport": {"risk": "none"},
            },
        }
    )

    response = _app(sc).get("/openapi/v1/bots/skills/skill-42/publish/status")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "skillCode": "skill-42",
        "name": "Demo",
        "status": "CHECKING",
        "statusDesc": "检查中",
        "source": "teamclaw",
        "version": "1.2.0",
        "isCompleted": False,
        "isSuccess": False,
        "errorMsg": None,
        "releaseTime": None,
        "standardCheckResult": {"passed": True},
        "securityCheckReport": {"risk": "none"},
    }
    assert sc.skill_code == "skill-42"


def test_publish_status_rejects_failed_or_malformed_sc_envelopes():
    for upstream in (
        {"success": False, "data": {}},
        {"success": True, "data": None},
        [],
    ):
        response = _app(_SkillCenter(upstream)).get(
            "/openapi/v1/bots/skills/skill-42/publish/status"
        )
        assert response.status_code == 502
        assert response.json()["message"] == "Skill Center publish status unavailable"
