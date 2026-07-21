from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.session_favorites.router import router
from engine.community.shared.utils import encode_session_key


@pytest.fixture()
def repository():
    with patch("engine.community.api.session_favorites.router.get_session_favorite_repository") as factory:
        repository = MagicMock()
        factory.return_value = repository
        yield repository


def _make_session(session_id: str):
    session = MagicMock()
    session.id = session_id
    session.title = f"Title for {session_id}"
    session.user_id = "user-a"
    session.agent_id = "main"
    session.model = None
    session.permission_mode = None
    session.cwd = None
    session.created_at = None
    session.updated_at = None
    session.message_count = 0
    session.runtime = None
    session.last_message = None
    session.ext_info = None
    return session


@pytest.fixture()
def session_api():
    api = MagicMock()
    with patch("engine.community.api.session_favorites.router._get_session_api", return_value=api):
        yield api


@pytest.fixture()
def client(repository, session_api) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_list_returns_full_session_data_for_current_users_favorites(
    client,
    repository,
    session_api,
):
    repository.list_session_ids.return_value = ["session-1", "session-3"]
    session_api.list = AsyncMock(return_value=[
        _make_session("session-1"),
        _make_session("session-2"),
        _make_session("session-3"),
    ])

    response = client.get(
        "/api/session-favorites",
        params={"user_id": "user-a", "agent_id": "main", "limit": 1, "offset": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == [{
        "id": "session-3",
        "title": "Title for session-3",
        "user_id": "user-a",
        "agent_id": "main",
        "model": None,
        "permission_mode": None,
        "cwd": None,
        "gmt_created": "",
        "gmt_modified": "",
        "message_count": 0,
    }]
    repository.list_session_ids.assert_called_once_with("user-a")
    request = session_api.list.call_args.args[0]
    assert request.user_id == "user-a"
    assert request.agent_id == "main"
    assert request.limit == 10_000
    assert request.offset == 0


def test_list_skips_engine_query_when_user_has_no_favorites(client, repository, session_api):
    repository.list_session_ids.return_value = []

    response = client.get("/api/session-favorites", params={"user_id": "user-a"})

    assert response.status_code == 200
    assert response.json()["data"] == []
    session_api.list.assert_not_called()


@pytest.mark.parametrize("method,path", [
    ("get", "/api/session-favorites"),
    ("put", "/api/session-favorites/session-1"),
    ("delete", "/api/session-favorites/session-1"),
])
def test_user_id_is_required(client, method, path):
    response = getattr(client, method)(path)

    assert response.status_code == 422


def test_add_decodes_session_key_and_is_idempotent(client, repository):
    raw_session_id = "agent:main:session:123:user:user-a"
    encoded_session_id = encode_session_key(raw_session_id)

    response = client.put(
        f"/api/session-favorites/{encoded_session_id}",
        params={"user_id": "user-a"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Session favorited"
    repository.add.assert_called_once_with("user-a", raw_session_id)


def test_remove_only_removes_calling_users_favorite(client, repository):
    response = client.delete(
        "/api/session-favorites/session-1",
        params={"user_id": "user-a"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Session unfavorited"
    repository.remove.assert_called_once_with("user-a", "session-1")


@pytest.mark.parametrize(
    ("method", "path", "repository_method"),
    [
        ("get", "/api/session-favorites", "list_session_ids"),
        ("put", "/api/session-favorites/session-1", "add"),
        ("delete", "/api/session-favorites/session-1", "remove"),
    ],
)
def test_repository_errors_return_a_generic_server_error(
    client,
    repository,
    method,
    path,
    repository_method,
):
    getattr(repository, repository_method).side_effect = RuntimeError("database unavailable")

    response = getattr(client, method)(path, params={"user_id": "user-a"})

    assert response.status_code == 500
    assert response.json()["detail"].startswith("Failed to")


def test_engine_error_returns_a_generic_server_error(client, repository, session_api):
    repository.list_session_ids.return_value = ["session-1"]
    session_api.list.side_effect = RuntimeError("engine unavailable")

    response = client.get("/api/session-favorites", params={"user_id": "user-a"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to list session favorites"


@pytest.mark.parametrize("error", [ConnectionError("disconnected"), TimeoutError("timed out")])
def test_engine_transport_error_returns_503(client, repository, session_api, error):
    repository.list_session_ids.return_value = ["session-1"]
    session_api.list.side_effect = error

    response = client.get("/api/session-favorites", params={"user_id": "user-a"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Session gateway unavailable"
