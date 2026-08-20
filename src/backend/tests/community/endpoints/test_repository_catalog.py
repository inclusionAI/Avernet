"""Endpoint-gate coverage for the canonical governed Repo catalog."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.repository_catalog_service import (
    RepositoryCatalogServiceProtocol,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_USER_ID = "repository-catalog-user"
_KEY = "repository-catalog-framework-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


class _Catalog:
    def list(self, *, path=None, orderby=None):
        return [{"id": "7", "name": "report", "path": path, "orderby": orderby}]

    def list_page(self, *, path=None, orderby=None, keyword="", page: int, page_size: int):
        return 1, self.list(path=path, orderby=orderby)

    def search(self, *, keyword: str, limit: int = 100):
        return self.list()[:limit]

    def tree(self):
        return [{"name": "ops", "children": []}]

    def detail(self, skill_id: str):
        return {"id": skill_id, "name": "report"}

    def sync(self):
        return {"status": "completed", "result": {"synced": True}}


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {"type": "user", "subject": {"id": _USER_ID, "username": "repo@test"}}
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _seed_catalog(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.injector.binder.bind(
        RepositoryCatalogServiceProtocol, to=_Catalog(), scope=None
    )


def _input(*, user_id: str = _USER_ID, path_params=None, query_params=None) -> CaseInput:
    return CaseInput(
        path_params=path_params or {},
        query_params={"user_id": user_id, **(query_params or {})},
        headers={PRINCIPAL_HEADER: _principal()},
    )


for _method, _path, _input_kwargs, _success in (
    (
        "GET",
        "/openapi/v1/bots/skills/repository",
        {"query_params": {"keyword": "report", "page": 1, "page_size": 20}},
        {"data": {"total": 1, "items": [{"id": "7", "name": "report"}]}},
    ),
    (
        "GET",
        "/openapi/v1/bots/skills/repository/tree",
        {},
        {"data": [{"name": "ops"}]},
    ),
    (
        "GET",
        "/openapi/v1/bots/skills/repository/{skill_id}",
        {"path_params": {"skill_id": "7"}},
        {"data": {"id": "7", "name": "report"}},
    ),
    (
        "POST",
        "/openapi/v1/bots/skills/repository/sync",
        {},
        {"data": {"synced": True}},
    ),
):
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        seed=_seed_catalog,
        input=_input(**_input_kwargs),
        expect=ExpectSuccess(status=200, json_contains=_success),
    )(lambda: None)
    endpoint_test(
        method=_method,
        path=_path,
        scenario="wrong_user",
        seed=_seed_catalog,
        input=_input(user_id="someone-else", **_input_kwargs),
        expect=ExpectError(status=403),
    )(lambda: None)
