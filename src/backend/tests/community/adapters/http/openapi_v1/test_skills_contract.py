"""OpenAPI contract tests for the ratified Bot-owned Local Skill surface."""

from fastapi import FastAPI

from agentclaw.community.adapters.http.openapi_v1.skills.router import readme_router, router


def _schema() -> dict:
    app = FastAPI()
    app.include_router(router)
    app.include_router(readme_router)
    return app.openapi()


def test_skill_readme_is_skill_addressed_and_uses_openapi_envelope() -> None:
    operation = _schema()["paths"]["/openapi/v1/bots/skills/{skill_id}/readme"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}

    assert parameters["skill_id"]["in"] == "path"
    assert set(parameters) == {"skill_id"}
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["$ref"].endswith("Envelope_SkillContent_")
    components = _schema()["components"]["schemas"]
    envelope_properties = components["Envelope_SkillContent_"]["properties"]
    assert set(envelope_properties) == {"code", "message", "data", "request_id"}
    data_schema = envelope_properties["data"]
    assert any(
        item.get("$ref", "").endswith("SkillContent")
        for item in data_schema.get("anyOf", [])
    )
    assert set(components["SkillContent"]["properties"]) == {"content"}


def test_openapi_exposes_local_compatibility_and_skill_asset_operations() -> None:
    schema = _schema()
    skill_paths = {
        path: set(operations)
        for path, operations in schema["paths"].items()
        if "/skills" in path and path.startswith("/openapi/v1/bots/{bot_id}")
        or path.startswith("/openapi/v1/bots/{bot_id}/skills")
    }

    assert skill_paths == {
        "/openapi/v1/bots/{bot_id}/skills": {"get", "post"},
        "/openapi/v1/bots/{bot_id}/skills/upload-folder": {"post"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}": {"get", "delete"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate": {"post"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate": {"post"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/content": {"get"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters": {"get", "put"},
    }


def test_collection_and_upload_are_bot_addressed_contracts() -> None:
    paths = _schema()["paths"]

    list_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/openapi/v1/bots/{bot_id}/skills"]["get"]["parameters"]
    }
    assert list_parameters["bot_id"]["in"] == "path"
    assert list_parameters["owner_id"]["required"] is False
    assert list_parameters["active"]["required"] is False
    assert list_parameters["keyword"]["required"] is False
    assert list_parameters["source"]["required"] is False
    assert {item.get("const") for item in list_parameters["source"]["schema"]["anyOf"]} == {
        "LOCAL",
        None,
    }

    upload = paths["/openapi/v1/bots/{bot_id}/skills"]["post"]
    upload_parameters = {
        parameter["name"]: parameter for parameter in upload["parameters"]
    }
    assert upload_parameters["bot_id"]["in"] == "path"
    assert upload_parameters["owner_id"]["required"] is False
    assert set(upload["requestBody"]["content"]) == {"application/zip"}

    folder_upload = paths["/openapi/v1/bots/{bot_id}/skills/upload-folder"]["post"]
    assert folder_upload["parameters"][0]["name"] == "bot_id"
    assert set(folder_upload["requestBody"]["content"]) == {"multipart/form-data"}


def test_operation_responses_use_the_ratified_local_skill_models() -> None:
    schema = _schema()
    paths = schema["paths"]

    list_schema = paths["/openapi/v1/bots/{bot_id}/skills"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert list_schema["$ref"].endswith("Envelope_Page_Skill__")

    detail = paths["/openapi/v1/bots/{bot_id}/skills/{skill_id}"]
    assert detail["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("Envelope_Skill_")
    assert detail["delete"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("Envelope_Deleted_")

    upload = paths["/openapi/v1/bots/{bot_id}/skills"]["post"]
    assert {"200", "201", "413"} <= set(upload["responses"])
    for status in ("200", "201"):
        assert upload["responses"][status]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("Envelope_SkillUpload_")

    for action in ("activate", "deactivate"):
        operation = paths[f"/openapi/v1/bots/{{bot_id}}/skills/{{skill_id}}/{action}"]["post"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("Envelope_SkillState_")

    assert schema["components"]["schemas"]["SkillUpload"]["properties"]["operation"][
        "enum"
    ] == ["created", "updated"]
