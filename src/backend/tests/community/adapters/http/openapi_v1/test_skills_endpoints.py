"""HTTP contract tests for #722's Bot-scoped Local Skill read routes."""

from __future__ import annotations

import time
from contextlib import contextmanager

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.skills.router import router
from agentclaw.community.api.local_skill_query_service import (
    LocalSkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import LocalSkillUploadServiceProtocol
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillOwnerAmbiguousError,
)
from agentclaw.community.core.skill_center.services.local_skill_query_service import (
    LocalSkillQueryService,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugins.bot_repository import BotRepository
from agentclaw.community.plugins.local.sqlite_models import (
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.plugins.skill_repository import SkillRepository
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)


class _Query:
    def __init__(self) -> None:
        self.list_args = None

    def list_local_skills(self, **kwargs):
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

    def get_local_skill(self, **kwargs):
        if kwargs["skill_id"] == "hidden":
            raise LocalSkillNotFoundError()
        if kwargs["skill_id"] == "ambiguous":
            raise LocalSkillOwnerAmbiguousError()
        return self.list_local_skills()[1][0]


class _Upload:
    async def upload_local_skill(self, **kwargs):
        self.args = kwargs
        return {
            "operation": "created",
            "skill": {
                "id": "8", "name": "new-skill", "description": "Useful",
                "category": "general", "tags": "[]", "active": False,
                "gmt_created": "2026-08-04T00:00:00", "gmt_modified": "2026-08-04T00:00:00",
            },
        }


def _client(query: _Query) -> TestClient:
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(LocalSkillQueryServiceProtocol, to=query)
            binder.bind(LocalSkillUploadServiceProtocol, to=_Upload())

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    return TestClient(app)


def test_upload_accepts_only_raw_zip_and_returns_created_inactive_skill():
    client = _client(_Query())
    response = client.post(
        "/openapi/v1/bots/skills/upload?bot_id=bot-1",
        content=b"PK\x03\x04",
        headers={"content-type": "application/zip"},
    )
    assert response.status_code == 201
    assert response.json()["code"] == 201000
    assert response.json()["data"] == {
        "operation": "created",
        "skill": {
            "skill_id": "8", "name": "new-skill", "description": "Useful",
            "category": "general", "tags": [], "active": False,
            "created_at": "2026-08-04T00:00:00", "updated_at": "2026-08-04T00:00:00",
        },
    }


def test_upload_rejects_multipart_and_other_content_types_before_service_call():
    client = _client(_Query())
    for content_type in ("multipart/form-data; boundary=x", "application/octet-stream"):
        response = client.post(
            "/openapi/v1/bots/skills/upload?bot_id=bot-1",
            content=b"not-a-zip",
            headers={"content-type": content_type},
        )
        assert response.status_code == 400
        assert response.json()["code"] == 400101


def test_list_uses_verified_actor_and_exposes_only_public_metadata():
    query = _Query()
    response = _client(query).get(
        "/openapi/v1/bots/skills?bot_id=bot-1&owner_entity_id=owner&active=false&keyword=cast&page=2&page_size=7"
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
    }


def test_detail_derives_scope_from_skill_id_and_masks_invisible_rows():
    query = _Query()
    client = _client(query)

    visible = client.get("/openapi/v1/bots/skills/7")
    assert visible.status_code == 200
    assert visible.json()["data"]["skill_id"] == "7"

    hidden = client.get("/openapi/v1/bots/skills/hidden")
    assert hidden.status_code == 404
    assert hidden.json()["code"] == 404000

    ambiguous = client.get("/openapi/v1/bots/skills/ambiguous")
    assert ambiguous.status_code == 409
    assert ambiguous.json()["code"] == 409104


def test_list_requires_bot_id_and_shared_page_limits():
    client = _client(_Query())
    assert client.get("/openapi/v1/bots/skills").status_code == 422
    assert (
        client.get("/openapi/v1/bots/skills?bot_id=bot&page_size=101").status_code
        == 422
    )


class _Database:
    def __init__(self, engine) -> None:
        self._session = sessionmaker(bind=engine)

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
    for model in (BotModel, Skill, DefaultSkillsetSkillExclusion):
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

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(
                LocalSkillQueryServiceProtocol,
                to=LocalSkillQueryService(skills, bots, object()),
            )

    now = int(time.time())

    def token(tenant: str) -> str:
        return jwt.encode(
            {
                "iss": "gateway",
                "aud": "backend",
                "iat": now,
                "exp": now + 60,
                "principals": [
                    {
                        "type": "user",
                        "tenant": tenant,
                        "subject": {"id": "owner", "username": "owner@example.com"},
                    }
                ],
            },
            key,
            algorithm="HS256",
        )

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.include_router(router)
    attach_injector(app, Injector([Bindings()]))
    client = TestClient(app)
    try:
        visible = client.get(
            "/openapi/v1/bots/skills?bot_id=bot",
            headers={PRINCIPAL_HEADER: token("tenant-a")},
        )
        hidden = client.get(
            "/openapi/v1/bots/skills?bot_id=bot",
            headers={PRINCIPAL_HEADER: token("tenant-b")},
        )
        invisible_git = client.get(
            f"/openapi/v1/bots/skills/{git_default['id']}",
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
    for model in (BotModel, Skill, DefaultSkillsetSkillExclusion):
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
        service = LocalSkillQueryService(skills, bots, object())
        _, a_skills = service.list_local_skills(
            bot_id="default",
            owner_id="owner-a",
            actor_id="owner-a",
            page=1,
            page_size=20,
            active=None,
            keyword=None,
        )
        _, b_skills = service.list_local_skills(
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
