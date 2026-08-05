"""OpenAPI contract tests for the ratified Bot-owned Local Skill surface."""

from fastapi import FastAPI

from agentclaw.community.adapters.http.openapi_v1.skills.router import router


def _schema() -> dict:
    app = FastAPI()
    app.include_router(router)
    return app.openapi()


def test_openapi_exposes_exactly_the_six_ratified_skills_operations() -> None:
    schema = _schema()
    skill_paths = {
        path: set(operations)
        for path, operations in schema["paths"].items()
        if path.startswith("/openapi/v1/bots/skills")
        or path.startswith("/openapi/v1/bots/{bot_id}/skills")
    }

    assert skill_paths == {
        "/openapi/v1/bots/skills": {"get"},
        "/openapi/v1/bots/skills/upload": {"post"},
        "/openapi/v1/bots/skills/{skill_id}": {"get", "delete"},
        "/openapi/v1/bots/skills/{skill_id}/activate": {"post"},
        "/openapi/v1/bots/skills/{skill_id}/deactivate": {"post"},
    }


def test_collection_and_upload_are_bot_scoped_query_contracts() -> None:
    paths = _schema()["paths"]

    list_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/openapi/v1/bots/skills"]["get"]["parameters"]
    }
    assert list_parameters["bot_id"]["required"] is True
    assert list_parameters["owner_entity_id"]["required"] is False
    assert list_parameters["active"]["required"] is False
    assert list_parameters["keyword"]["required"] is False

    upload = paths["/openapi/v1/bots/skills/upload"]["post"]
    upload_parameters = {
        parameter["name"]: parameter for parameter in upload["parameters"]
    }
    assert upload_parameters["bot_id"]["required"] is True
    assert upload_parameters["owner_entity_id"]["required"] is False
    assert set(upload["requestBody"]["content"]) == {"application/zip"}


def test_operation_responses_use_the_ratified_local_skill_models() -> None:
    schema = _schema()
    paths = schema["paths"]

    list_schema = paths["/openapi/v1/bots/skills"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert list_schema["$ref"].endswith("Envelope_Page_Skill__")

    detail = paths["/openapi/v1/bots/skills/{skill_id}"]
    assert detail["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("Envelope_Skill_")
    assert detail["delete"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("Envelope_Deleted_")

    upload = paths["/openapi/v1/bots/skills/upload"]["post"]
    assert {"201", "413"} <= set(upload["responses"])
    assert upload["responses"]["201"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("Envelope_SkillUpload_")

    for action in ("activate", "deactivate"):
        operation = paths[f"/openapi/v1/bots/skills/{{skill_id}}/{action}"]["post"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("Envelope_SkillState_")

    assert schema["components"]["schemas"]["SkillUpload"]["properties"]["operation"][
        "enum"
    ] == ["created", "updated"]
