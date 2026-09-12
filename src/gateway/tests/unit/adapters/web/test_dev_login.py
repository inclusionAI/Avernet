"""The local-only dev-login page arms the dev_cookie strategy's cookies.

The gateway is the only hop a singlebox browser actually reaches, and the
``dev_cookie`` identity strategy resolves a local user from a ``staff_id``
cookie. Before this page existed, "logging in" locally meant teaching every
operator to set that cookie with DevTools on whichever host they happened to
browse from. ``GET /_dev/login`` does it for them: it sets the cookies on the
host it was opened from (cookie jars are port-agnostic) and points at the
frontend port to continue.

The route must be indistinguishable from absent outside the environments where
the dev_cookie strategy itself is enabled, so a production deployment returns
404, not a disabled page.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _set_cookie_values(response) -> dict[str, str]:
    """Collect ``set-cookie`` headers into a {name: value} map."""
    jar: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        first = header.split(";", 1)[0].strip()
        name, _, value = first.partition("=")
        jar[name] = value
    return jar


def test_returns_404_outside_enabled_envs(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "prod")
    assert client.get("/_dev/login").status_code == 404


def test_local_env_sets_default_staff_cookies(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "local")
    response = client.get("/_dev/login")
    assert response.status_code == 200
    jar = _set_cookie_values(response)
    assert jar.get("staff_id") == "001"
    assert jar.get("nick_name") is not None


def test_custom_staff_id_is_reflected(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "local")
    jar = _set_cookie_values(client.get("/_dev/login", params={"staff_id": "abc-123"}))
    assert jar.get("staff_id") == "abc-123"


def test_cookie_injection_in_staff_id_is_rejected(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("SERVER_ENV", "local")
    response = client.get("/_dev/login", params={"staff_id": "001; Path=/evil"})
    assert response.status_code == 400


def test_next_port_is_honored_in_continue_link(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "local")
    body = client.get("/_dev/login", params={"next": "8001"}).text
    assert ":8001/" in body


def test_invalid_next_port_is_rejected(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "local")
    response = client.get("/_dev/login", params={"next": "evil.example"})
    assert response.status_code == 400


def test_nick_name_is_urlquoted_into_the_cookie(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("SERVER_ENV", "local")
    jar = _set_cookie_values(
        client.get("/_dev/login", params={"nick_name": "测试 dev"})
    )
    # dev_cookie strategy URL-decodes when reading; the wire format is quoted.
    assert jar.get("nick_name") == "%E6%B5%8B%E8%AF%95%20dev"
