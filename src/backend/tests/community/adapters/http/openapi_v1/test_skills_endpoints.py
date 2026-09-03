"""HTTP contract tests for #722's Bot-scoped Local Skill read routes."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tests.community.skill_version_fakes import PassthroughSkillVersionResolver

from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1.contracts import EXAMPLE_TRACE_ID
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.skills.router import router
from agentclaw.community.api.direct_activation_service import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.skill_query_service import (
    SkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.models.mcp import (
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillActiveError,
    LocalSkillNotFoundError,
    LocalSkillOwnerAmbiguousError,
)
from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.skill_query_service import (
    SkillQueryService,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    bind_bot_access_seam,
    mount_public_error_handlers,
    user_scoped_client,
)


class _Query:
    """A ``SkillQueryServiceProtocol`` double that records every call."""

    def __init__(self) -> None:
        self.list_args = None
        self.get_args = None
        self.content_args = None
        self.parameter_args = None
        self.replace_args = None

    def list_bot_skills(self, **kwargs):
        self.list_args = kwargs
        return 1, [
            {
                "id": "7",
                "name": "weather",
                "description": "Forecast",
                "category": "tools",
                "tags": '["daily"]',
                "active": False,
                "gmt_created": "2026-08-04T00:00:00",
                "gmt_modified": "2026-08-04T01:00:00",
                "git_path": "local://weather",
                "user_id": "owner",
                "bolt_id": "bot-1",
            }
        ]

    def get_skill(self, **kwargs):
        self.get_args = kwargs
        if kwargs["skill_id"] == "hidden":
            raise LocalSkillNotFoundError()
        if kwargs["skill_id"] == "ambiguous":
            raise LocalSkillOwnerAmbiguousError()
        return self.list_bot_skills()[1][0]

    async def get_content(self, **kwargs):
        self.content_args = kwargs
        return "---\nname: weather\ndescription: Forecast\n---\n# Weather"

    async def get_parameters(self, **kwargs):
        self.parameter_args = kwargs
        return {"region": "cn"}

    async def replace_parameters(self, **kwargs):
        self.replace_args = kwargs
        return kwargs["parameters"]


class _Upload:
    operation = "created"

    async def upload_local_skill(self, **kwargs):
        self.args = kwargs
        return {
            "operation": self.operation,
            "skill": {
                "id": "8",
                "name": "new-skill",
                "description": "Useful",
                "category": "general",
                "tags": "[]",
                "active": False,
                "gmt_created": "2026-08-04T00:00:00",
                "gmt_modified": "2026-08-04T00:00:00",
            },
        }

    async def upload_local_skill_files(self, **kwargs):
        self.folder_args = kwargs
        return await self.upload_local_skill(**kwargs)


class _DirectActivation:
    """A ``DirectActivationServiceProtocol`` double that records commands."""

    def __init__(self) -> None:
        self.args = None
        self.command = None

    def _state(self, active: bool):
        return {
            "id": "8",
            "name": "new-skill",
            "description": "Useful",
            "category": "general",
            "tags": "[]",
            "active": active,
            "changed": False,
            "gmt_created": "2026-08-04T00:00:00",
            "gmt_modified": "2026-08-04T00:00:00",
        }

    async def activate_skill(self, **kwargs):
        self.command, self.args = "activate", kwargs
        return self._state(True)

    async def deactivate_skill(self, **kwargs):
        self.command, self.args = "deactivate", kwargs
        return self._state(False)


class _Delete:
    def __init__(self) -> None:
        self.args = None

    async def delete_local_skill(self, **kwargs):
        self.args = kwargs


def _client(
    query: _Query,
    state: _DirectActivation | None = None,
    delete: _Delete | None = None,
) -> TestClient:
    direct_activation = state or _DirectActivation()

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillQueryServiceProtocol, to=query)
            binder.bind(LocalSkillUploadServiceProtocol, to=_Upload())
            binder.bind(LocalSkillDeleteServiceProtocol, to=delete or _Delete())
            binder.bind(DirectActivationServiceProtocol, to=direct_activation)
            # The seven ``{skill_id}`` operations declare ``Check(MEMBER)``
            # now and the gate fails closed against an unwired app. ``actor``
            # owns ``bot-1`` in these tests, so the level is OWNER and the
            # questions below stay the handler's own.
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    return user_scoped_client(app, "actor")


def test_upload_accepts_only_raw_zip_and_returns_created_inactive_skill():
    client = _client(_Query())
    response = client.post(
        "/openapi/v1/bots/bot-1/skills",
        content=b"PK\x03\x04",
        headers={"content-type": "application/zip"},
    )
    assert response.status_code == 201
    assert response.json()["code"] == 201000
    assert response.json()["data"] == {
        "operation": "created",
        "skill": {
            "skill_id": "8",
            "name": "new-skill",
            "description": "Useful",
            "category": "general",
            "tags": [],
            "active": False,
            "created_at": "2026-08-04T00:00:00",
            "updated_at": "2026-08-04T00:00:00",
        },
    }


def test_upload_replacement_returns_200_and_updated_operation():
    class _UpdatedUpload(_Upload):
        operation = "updated"

        async def upload_local_skill(self, **kwargs):
            result = await super().upload_local_skill(**kwargs)
            return {
                **result,
                "runtime_projection": {
                    "status": "PENDING",
                    "components": {"skills": "PENDING"},
                    "pending_count": 1,
                    "degraded_count": 0,
                    "issues": [
                        {
                            "resource_type": "RUNTIME",
                            "code": "SKILL_RUNTIME_UNAVAILABLE",
                            "reason": "Skill 运行环境当前不可连接",
                            "status": "PENDING",
                            "retryable": True,
                        }
                    ],
                },
            }

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillQueryServiceProtocol, to=_Query())
            binder.bind(LocalSkillUploadServiceProtocol, to=_UpdatedUpload())
            binder.bind(LocalSkillDeleteServiceProtocol, to=_Delete())
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    client = user_scoped_client(app, "actor")
    response = client.post(
        "/openapi/v1/bots/bot-1/skills",
        content=b"PK\x03\x04",
        headers={"content-type": "application/zip"},
    )
    assert response.status_code == 200
    assert response.json()["code"] == 200000
    assert response.json()["data"]["operation"] == "updated"
    assert response.json()["data"]["desired_state"] == {
        "changed": True,
        "status": "COMMITTED",
    }
    assert response.json()["data"]["runtime_projection"]["status"] == "PENDING"


def test_upload_rejects_multipart_and_other_content_types_before_service_call():
    client = _client(_Query())
    for content_type in ("multipart/form-data; boundary=x", "application/octet-stream"):
        response = client.post(
            "/openapi/v1/bots/bot-1/skills",
            content=b"not-a-zip",
            headers={"content-type": content_type},
        )
        assert response.status_code == 400
        assert response.json()["code"] == 400101


def test_upload_folder_preserves_legacy_file_paths_and_returns_created_skill():
    query = _Query()
    upload = _Upload()

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillQueryServiceProtocol, to=query)
            binder.bind(LocalSkillUploadServiceProtocol, to=upload)
            binder.bind(LocalSkillDeleteServiceProtocol, to=_Delete())
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    client = user_scoped_client(app, "actor")

    response = client.post(
        "/openapi/v1/bots/bot-1/skills/upload-folder",
        data={"file_paths": json.dumps(["folder/SKILL.md", "folder/scripts/check.py"])},
        files=[
            ("files", ("SKILL.md", b"---\nname: folder\n---", "text/markdown")),
            ("files", ("check.py", b"print('ok')", "text/plain")),
        ],
    )

    assert response.status_code == 201
    assert upload.folder_args == {
        "bot_id": "bot-1",
        "owner_id": "actor",
        "actor_id": "actor",
        "files": [
            ("folder/SKILL.md", b"---\nname: folder\n---"),
            ("folder/scripts/check.py", b"print('ok')"),
        ],
    }


def test_upload_folder_rejects_misaligned_paths_before_service_call():
    query = _Query()
    upload = _Upload()

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillQueryServiceProtocol, to=query)
            binder.bind(LocalSkillUploadServiceProtocol, to=upload)
            binder.bind(LocalSkillDeleteServiceProtocol, to=_Delete())
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    client = user_scoped_client(app, "actor")

    response = client.post(
        "/openapi/v1/bots/bot-1/skills/upload-folder",
        data={"file_paths": json.dumps(["folder/SKILL.md"])},
        files=[
            ("files", ("SKILL.md", b"manifest", "text/markdown")),
            ("files", ("check.py", b"print('ok')", "text/plain")),
        ],
    )

    assert response.status_code == 400
    assert not hasattr(upload, "folder_args")


def test_activate_and_deactivate_derive_scope_from_id_and_return_desired_state():
    state = _DirectActivation()
    client = _client(_Query(), state)

    activated = client.post("/openapi/v1/bots/bot-1/skills/8/activate")
    assert activated.status_code == 200
    assert activated.json()["data"] == {
        "skill": {
            "skill_id": "8",
            "name": "new-skill",
            "description": "Useful",
            "category": "general",
            "tags": [],
            "active": True,
            "created_at": "2026-08-04T00:00:00",
            "updated_at": "2026-08-04T00:00:00",
        },
        "changed": False,
        "desired_state": {"changed": False, "status": "UNCHANGED"},
        "runtime_projection": {
            "status": "SKIPPED",
            "components": {},
            "pending_count": 0,
            "degraded_count": 0,
            "issues": [],
            "reason": "RUNTIME_RESULT_NOT_AVAILABLE",
        },
    }
    assert state.command == "activate"
    assert state.args == {
        "skill_id": "8",
        "bot_id": "bot-1",
        "owner_id": "actor",
        "actor_id": "actor",
    }

    deactivated = client.post("/openapi/v1/bots/bot-1/skills/8/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["data"]["skill"]["active"] is False
    assert state.command == "deactivate"
    assert state.args == {
        "skill_id": "8",
        "bot_id": "bot-1",
        "owner_id": "actor",
        "actor_id": "actor",
    }


def test_delete_derives_scope_from_skill_id_and_returns_standard_deleted_payload():
    delete = _Delete()
    response = _client(_Query(), delete=delete).delete(
        "/openapi/v1/bots/bot-1/skills/8"
    )

    assert response.status_code == 200
    assert response.json()["code"] == 200000
    assert response.json()["data"] == {"deleted": True}
    assert delete.args == {
        "skill_id": "8",
        "owner_id": "actor",
        "user_id": "actor",
    }


def test_delete_active_error_uses_the_fixed_public_conflict_envelope():
    class _ActiveDelete(_Delete):
        async def delete_local_skill(self, **kwargs):
            self.args = kwargs
            raise LocalSkillActiveError()

    response = _client(_Query(), delete=_ActiveDelete()).delete(
        "/openapi/v1/bots/bot-1/skills/8"
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": 409102,
        "message": "Skill is active",
        "data": None,
        "request_id": "",
    }


def test_list_uses_verified_actor_and_exposes_only_public_metadata():
    query = _Query()
    response = _client(query).get(
        "/openapi/v1/bots/bot-1/skills?owner_id=owner&active=false&keyword=cast&page=2&page_size=7"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "total": 1,
        "items": [
            {
                "skill_id": "7",
                "name": "weather",
                "description": "Forecast",
                "category": "tools",
                "tags": ["daily"],
                "active": False,
                "created_at": "2026-08-04T00:00:00",
                "updated_at": "2026-08-04T01:00:00",
            }
        ],
    }
    assert query.list_args == {
        "bot_id": "bot-1",
        "owner_id": "owner",
        "actor_id": "actor",
        "page": 2,
        "page_size": 7,
        "active": False,
        "keyword": "cast",
        "source": None,
    }


def test_list_forwards_local_source_filter_and_rejects_other_sources():
    query = _Query()
    client = _client(query)

    response = client.get("/openapi/v1/bots/bot-1/skills?owner_id=owner&source=LOCAL")

    assert response.status_code == 200
    assert query.list_args is not None
    assert query.list_args["source"] == "LOCAL"

    invalid = client.get("/openapi/v1/bots/bot-1/skills?owner_id=owner&source=REPO")

    assert invalid.status_code == 422


def test_detail_derives_scope_from_skill_id_and_masks_invisible_rows():
    query = _Query()
    client = _client(query)

    visible = client.get("/openapi/v1/bots/bot-1/skills/7")
    assert visible.status_code == 200
    assert visible.json()["data"]["skill_id"] == "7"

    hidden = client.get("/openapi/v1/bots/bot-1/skills/hidden")
    assert hidden.status_code == 404
    assert hidden.json()["code"] == 404000

    ambiguous = client.get("/openapi/v1/bots/bot-1/skills/ambiguous")
    assert ambiguous.status_code == 409
    assert ambiguous.json()["code"] == 409104


def test_content_and_parameters_use_the_type_resolved_query_service():
    query = _Query()
    client = _client(query)

    content = client.get("/openapi/v1/bots/bot-1/skills/7/content")
    parameters = client.get("/openapi/v1/bots/bot-1/skills/7/parameters")
    replacement = client.put(
        "/openapi/v1/bots/bot-1/skills/7/parameters",
        json={"parameters": {"region": "us"}},
    )

    assert content.json()["data"]["content"].endswith("# Weather")
    assert parameters.json()["data"] == {"parameters": {"region": "cn"}}
    assert replacement.json()["data"] == {"parameters": {"region": "us"}}
    assert query.content_args == {
        "skill_id": "7",
        "bot_id": "bot-1",
        "owner_id": "actor",
        "user_id": "actor",
    }
    assert query.parameter_args == {
        "skill_id": "7",
        "bot_id": "bot-1",
        "owner_id": "actor",
        "user_id": "actor",
    }
    assert query.replace_args == {
        "skill_id": "7",
        "bot_id": "bot-1",
        "owner_id": "actor",
        "user_id": "actor",
        "parameters": {"region": "us"},
    }


def test_list_requires_bot_id_and_shared_page_limits():
    client = _client(_Query())
    # No bot in the address means no such route, not a missing parameter.
    assert client.get("/openapi/v1/bots/skills").status_code == 404
    assert client.get("/openapi/v1/bots/bot/skills?page_size=101").status_code == 422


def test_openapi_declares_local_compatibility_and_skill_asset_operations():
    schema = _client(_Query()).app.openapi()
    skill_paths = {
        path: operations
        for path, operations in schema["paths"].items()
        if "/skills" in path
        and path.startswith("/openapi/v1/bots/{bot_id}")
        or path.startswith("/openapi/v1/bots/{bot_id}/skills")
    }
    assert {path: set(operations) for path, operations in skill_paths.items()} == {
        "/openapi/v1/bots/{bot_id}/skills": {"get", "post"},
        "/openapi/v1/bots/{bot_id}/skills/upload-folder": {"post"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}": {"get", "delete"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate": {"post"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate": {"post"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/content": {"get"},
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters": {"get", "put"},
    }
    for path in (
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate",
    ):
        assert set(schema["paths"][path]) == {"post"}
        response_schema = schema["paths"][path]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"].endswith("Envelope_SkillState_")
    delete = schema["paths"]["/openapi/v1/bots/{bot_id}/skills/{skill_id}"]
    assert set(delete) == {"get", "delete"}
    assert delete["delete"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("Envelope_Deleted_")
    state_schema = schema["components"]["schemas"]["SkillState"]
    assert state_schema["required"] == ["skill", "changed"]
    upload = schema["paths"]["/openapi/v1/bots/{bot_id}/skills"]["post"]
    assert {"200", "201", "413"} <= set(upload["responses"])
    assert set(upload["requestBody"]["content"]) == {"application/zip"}
    folder_upload = schema["paths"]["/openapi/v1/bots/{bot_id}/skills/upload-folder"][
        "post"
    ]
    assert {"200", "201", "413"} <= set(folder_upload["responses"])
    assert set(folder_upload["requestBody"]["content"]) == {"multipart/form-data"}
    error_example = upload["responses"]["413"]["content"]["application/json"]["example"]
    # request_id carries the surface-wide illustrative trace id, so rendered
    # samples show a realistic value instead of an empty placeholder.
    assert {**error_example, "data": error_example.get("data")} == {
        "code": 413101,
        "message": "Skill package is too large",
        "request_id": EXAMPLE_TRACE_ID,
        "data": None,
    }
    error_schema = upload["responses"]["413"]["content"]["application/json"]["schema"]
    assert error_schema["$ref"].endswith("ErrorEnvelope")
    assert schema["components"]["schemas"]["ErrorEnvelope"]["properties"]["data"] == {
        "type": "null",
        "title": "Data",
        "description": "Always null on an error response.",
    }
    for path, methods in skill_paths.items():
        for method, operation in methods.items():
            if (
                path
                not in {
                    "/openapi/v1/bots/{bot_id}/skills",
                    "/openapi/v1/bots/{bot_id}/skills/upload-folder",
                }
                or method != "post"
            ):
                assert "413" not in operation.get("responses", {})
    for status in ("200", "201"):
        assert upload["responses"][status]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("Envelope_SkillUpload_")
    assert schema["components"]["schemas"]["SkillUpload"]["properties"]["operation"][
        "enum"
    ] == ["created", "updated"]


class _Database:
    def __init__(self, engine) -> None:
        self._session = sessionmaker(bind=engine)

    @contextmanager
    def transactional_orm_session(self):
        with self.orm_session() as session:
            yield session

    @contextmanager
    def orm_session(self):
        session = self._session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _real_query_service(db, bots, skills) -> SkillQueryService:
    """The real service over the SQLite fixtures, reads only.

    The asset factories and the device resolver are inert stand-ins because
    these tests stop at listing and metadata detail — neither content nor
    parameters is read, so nothing ever calls them.
    """
    reader = BotCapabilityStateReader(
        CapabilityDesiredStateRepository(db),
        bots,
        skills,
        PassthroughSkillVersionResolver(),
    )
    return SkillQueryService(
        skills, bots, object(), reader, object(), object(), lambda: object()
    )


def test_a_skillset_bridged_skill_is_listed_and_gains_its_installation(tmp_path):
    """The whole point: a Skill only a SkillSet ties to the Bot, listed active.

    The Skill row names another owner and another Bot, so nothing but the
    SkillSet reaches it, and it holds no Installation row until this listing
    writes one. Tenant scoping is deliberately left at the default here — the
    guard is what the neighbouring tenant test exercises.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'bridged.db'}")
    for model in (
        BotModel,
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        BotSkillInstallation,
        BotMCPInstallation,
        DefaultSkillsetSkillExclusion,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    bots, skills = BotRepository(db), SkillRepository(db)

    bots.insert(
        {
            "bot_id": "bot",
            "entity_id": "owner",
            "entity_type": "staff",
            "creator_id": "owner",
            "owner_id": "owner",
            "active_engine": "openclaw",
        }
    )
    skills.create(
        {
            "name": "mine",
            "git_path": "local://mine",
            "user_id": "owner",
            "bolt_id": "bot",
        }
    )
    bridged = skills.create(
        {
            "name": "from-the-market",
            "git_path": "git://market/from-the-market",
            "user_id": "someone-else",
            "bolt_id": "another-bot",
        }
    )
    with db.orm_session() as session:
        skill_set = SkillSet(
            name="mine",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env=get_current_env(),
        )
        session.add(skill_set)
        session.flush()
        session.add(
            SkillSetSkill(
                skill_set_id=skill_set.id,
                skill_id=int(bridged["id"]),
                env=get_current_env(),
            )
        )

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(
                SkillQueryServiceProtocol,
                to=_real_query_service(db, bots, skills),
            )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner"}
    attach_injector(app, Injector([Bindings()]))
    client = user_scoped_client(app, "owner")
    listed = client.get("/openapi/v1/bots/bot/skills?owner_id=owner")
    active_only = client.get("/openapi/v1/bots/bot/skills?owner_id=owner&active=true")

    assert listed.status_code == 200
    body = listed.json()["data"]
    assert body["total"] == 2
    # The Set is active, so the repair installed its member and `active` says so.
    assert {item["name"]: item["active"] for item in body["items"]} == {
        "mine": False,
        "from-the-market": True,
    }
    # And the filter agrees, because it reads the row the repair wrote.
    filtered = active_only.json()["data"]
    assert filtered["total"] == 1
    assert [item["name"] for item in filtered["items"]] == ["from-the-market"]


def test_router_uses_verified_principal_and_real_tenant_guard(tmp_path):
    """A verified HTTP request reaches the Track A-guarded ORM query."""
    key = "skills-router-test-key-at-least-32-bytes"

    class _Secret:
        secret_user = "gateway"
        secret_value = key

    class _Resolver(SecretResolver):
        def get_secret(self, secret_name: str):
            return _Secret()

    init_principal_verifier_config(
        _Resolver(), "gateway_principal_signing_key", strict=False
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'skills-router.db'}")
    for model in (
        BotModel,
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        BotSkillInstallation,
        BotMCPInstallation,
        DefaultSkillsetSkillExclusion,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    bots, skills = BotRepository(db), SkillRepository(db)
    with avernet_tenant_scope("tenant-a"):
        bots.insert(
            {
                "bot_id": "bot",
                "entity_id": "owner",
                "entity_type": "staff",
                "creator_id": "owner",
                "owner_id": "owner",
            }
        )
        skills.create(
            {
                "name": "tenant-a",
                "git_path": "local://tenant-a",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        git_default = skills.create(
            {
                "name": "git-default",
                "git_path": "git://market/git-default",
                "user_id": "owner",
                "bolt_id": "default",
            }
        )
        # The ``default`` Bot has to exist for the third request below to
        # test what it says. That address is ``Check(MEMBER)`` now, so the
        # seam resolves ``(default, owner)`` before the handler runs; without
        # a row it refuses with the same 404 the handler would have produced,
        # and the git-market masking underneath would never be exercised.
        bots.insert(
            {
                "bot_id": "default",
                "entity_id": "owner",
                "entity_type": "staff",
                "creator_id": "owner",
                "owner_id": "owner",
            }
        )

        class Bindings(Module):
            def configure(self, binder):
                binder.bind(
                    SkillQueryServiceProtocol,
                    to=_real_query_service(db, bots, skills),
                )
                # The real repository, not ``SeamBots``: this test is about a
                # tenant-guarded ORM query, and a double that answers "the bot
                # exists" would put a fake in the middle of the one path it
                # exists to drive. ``owner`` owns both bots here, so the level
                # is OWNER and the seam admits.
                bind_bot_access_seam(binder, bots=bots)

    now = int(time.time())

    def token(tenant: str) -> str:
        # The tenant rides on the ``app`` principal: a ``user`` principal carries
        # none, because nothing in a user credential proves which tenant the
        # person acts for. A user-only token would scope to the internal
        # default and this pair of requests would stop testing isolation at all.
        return jwt.encode(
            {
                "iss": "gateway",
                "aud": "backend",
                "iat": now,
                "exp": now + 60,
                "principals": [
                    {
                        "type": "user",
                        "subject": {"id": "owner", "username": "owner@example.com"},
                    },
                    {
                        "type": "app",
                        "tenant": tenant,
                        "app": {
                            "app_id": 1,
                            "app_name": "Partner App",
                            "owners": "partner-org",
                            "tenant": tenant,
                        },
                    },
                ],
            },
            key,
            algorithm="HS256",
        )

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.include_router(router)
    attach_injector(app, Injector([Bindings()]))
    client = user_scoped_client(app, "owner")
    try:
        visible = client.get(
            "/openapi/v1/bots/bot/skills",
            headers={PRINCIPAL_HEADER: token("tenant-a")},
        )
        hidden = client.get(
            "/openapi/v1/bots/bot/skills",
            headers={PRINCIPAL_HEADER: token("tenant-b")},
        )
        invisible_git = client.get(
            f"/openapi/v1/bots/default/skills/{git_default['id']}",
            headers={PRINCIPAL_HEADER: token("tenant-a")},
        )
    finally:
        reset_principal_verifier_config_cache()
    assert visible.status_code == 200 and visible.json()["data"]["total"] == 1
    assert hidden.status_code == 404 and hidden.json()["code"] == 404000
    assert invisible_git.status_code == 404 and invisible_git.json()["code"] == 404000


def test_default_bot_scope_is_owner_distinguished(tmp_path):
    """Two valid legacy ``default`` Bots must not make either owner ambiguous."""
    engine = create_engine(f"sqlite:///{tmp_path / 'default-owner.db'}")
    for model in (
        BotModel,
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        BotSkillInstallation,
        BotMCPInstallation,
        DefaultSkillsetSkillExclusion,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    bots, skills = BotRepository(db), SkillRepository(db)
    with avernet_tenant_scope("tenant"):
        for owner in ("owner-a", "owner-b"):
            bots.insert(
                {
                    "bot_id": "default",
                    "entity_id": owner,
                    "entity_type": "staff",
                    "creator_id": owner,
                    "owner_id": owner,
                }
            )
            skills.create(
                {
                    "name": owner,
                    "git_path": f"local://{owner}",
                    "user_id": owner,
                    "bolt_id": "default",
                }
            )
        service = _real_query_service(db, bots, skills)
        _, a_skills = service.list_bot_skills(
            bot_id="default",
            owner_id="owner-a",
            actor_id="owner-a",
            page=1,
            page_size=20,
            active=None,
            keyword=None,
        )
        _, b_skills = service.list_bot_skills(
            bot_id="default",
            owner_id="owner-b",
            actor_id="owner-b",
            page=1,
            page_size=20,
            active=None,
            keyword=None,
        )
    assert [skill["name"] for skill in a_skills] == ["owner-a"]
    assert [skill["name"] for skill in b_skills] == ["owner-b"]


@pytest.mark.asyncio
async def test_state_command_cannot_cross_the_real_tenant_guard(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'state-tenant.db'}")
    from agentclaw.community.core.models import SkillSet, SkillSetSkill

    for model in (
        BotModel,
        Skill,
        SkillSet,
        SkillSetSkill,
        DefaultSkillsetSkillExclusion,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    bots, skills, sets = BotRepository(db), SkillRepository(db), SkillSetRepository(db)
    with avernet_tenant_scope("tenant-a"):
        bots.insert(
            {
                "bot_id": "bot",
                "entity_id": "owner",
                "entity_type": "staff",
                "creator_id": "owner",
                "owner_id": "owner",
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        default_set = sets.create(
            {
                "name": "Default",
                "user_id": "owner",
                "bolt_id": "bot",
                "is_default": True,
                "is_builtin": False,
                "is_active": False,
                "engine_type": "openclaw",
            }
        )
        skill = skills.create(
            {
                "name": "private",
                "git_path": "local://private",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        sets.add_skill_to_set(default_set["id"], skill["id"], user_id="owner")
        sets.add_default_skill_exclusion(
            "owner", "bot", int(default_set["id"]), int(skill["id"])
        )

    class _Runtime:
        calls = 0

        def project_skills(self):
            self.calls += 1
            return True

    class _Factory:
        def __init__(self):
            self.runtime = _Runtime()

        def create(self, **_kwargs):
            return self.runtime

    factory = _Factory()
    service = DirectActivationService(
        object(),
        bots,
        skills,
        factory.runtime,
        object(),
        object(),
        object(),
        object(),
        PlatformDefaultMcpPolicy(lambda _bot_id: None),
    )
    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(LocalSkillNotFoundError):
            await service.activate_skill(
                skill_id=skill["id"], bot_id="bot", owner_id="owner",
                actor_id="owner",
            )
    assert factory.runtime.calls == 0


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("get", "/openapi/v1/bots/bot-1/skills/{skill}"),
        ("delete", "/openapi/v1/bots/bot-1/skills/{skill}"),
        ("get", "/openapi/v1/bots/bot-1/skills/{skill}/content"),
        ("get", "/openapi/v1/bots/bot-1/skills/{skill}/parameters"),
        ("post", "/openapi/v1/bots/bot-1/skills/{skill}/activate"),
        ("post", "/openapi/v1/bots/bot-1/skills/{skill}/deactivate"),
    ],
)
def test_a_caller_with_no_relation_is_refused_before_the_query_service_runs(
    method, template
):
    """The seven ``{skill_id}`` rows are ``Check(MEMBER)``; this is what that buys.

    The query and activation services still perform their own MEMBER checks —
    they have to, because ``/api/skills`` and the retiring twins reach them
    with no route-level gate — so this is not asserting that the only check
    exists. It is asserting that the *declared* one runs, and runs first: the
    row says the seam is the authority for these addresses, and a row that
    said so while the refusal still came from three frames down would be a
    false claim.

    Both refusals are a masked 404, so a caller sees no difference. What
    changes is the code, and where the decision is made — which is the whole
    of what this feature moves.

    ``PUT /parameters`` is left out only because it needs a body; the ``GET``
    beside it shares the row's bar and the same gate.
    """
    query = _Query()
    direct_activation = _DirectActivation()

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillQueryServiceProtocol, to=query)
            binder.bind(LocalSkillUploadServiceProtocol, to=_Upload())
            binder.bind(LocalSkillDeleteServiceProtocol, to=_Delete())
            binder.bind(DirectActivationServiceProtocol, to=direct_activation)
            # Default level is NONE, and ``stranger`` does not own the bot, so
            # nothing short-circuits to OWNER.
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "stranger"}
    mount_public_error_handlers(app)
    attach_injector(app, Injector([Bindings()]))
    client = user_scoped_client(app, "stranger")

    response = getattr(client, method)(
        template.format(skill="8") + "?owner_id=someone-else"
    )

    assert response.status_code == 404, response.json()
    assert response.json()["message"] == "Not found"
    assert response.json()["data"] is None
    assert query.get_args is None, "the query service was reached despite the refusal"
    assert direct_activation.args is None
