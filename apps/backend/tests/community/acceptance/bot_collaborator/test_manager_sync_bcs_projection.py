"""Live singlebox regression for Backend collaborator projection into BCS.

The story deliberately crosses both processes: collaborator mutations use the
public Backend HTTP API, the production manager-sync worker signs and sends its
real BCS PUT, and the registered Bot owner reads the resulting BCS roster.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest

from tests.community.acceptance.bot_collaborator.test_member_management_live import (
    _seed_member_managed_bot,
)

pytestmark = pytest.mark.acceptance

_REPO_ROOT = Path(__file__).resolve().parents[6]
_BCS_BASE_URL = os.environ.get(
    "SINGLEBOX_ACCEPTANCE_BCS_BASE_URL", "http://127.0.0.1:21000"
)
_BCS_ADMIN_BIN = Path(
    os.environ.get(
        "SINGLEBOX_ACCEPTANCE_BCS_ADMIN_BIN",
        _REPO_ROOT / "apps/bcs/target/debug/bcs-admin",
    )
)
_BCS_CONFIG_DIR = Path(
    os.environ.get(
        "SINGLEBOX_ACCEPTANCE_BCS_CONFIG_DIR",
        _REPO_ROOT / "singlebox/.dependencies/standalone/bcs-config",
    )
)
_PRINCIPAL_KEY = os.environ.get(
    "SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY",
    "singlebox-gateway-principal-key-not-for-production",
)
_BCS_TENANT = "teamclaw"
_LOCAL_SERVICE_KEY = os.environ.get("SINGLEBOX_BCS_SERVICE_KEY", "e2e-test-key")
_REGISTERED_BOT_ID = re.compile(r"^bot_[0-9a-f]{8}$")


def _fresh_staff_id(prefix: str) -> str:
    """Return a local-BCS-compatible ASCII alphanumeric user id."""
    return f"{prefix}{uuid4().hex[:12]}"


def _owner_principal(owner_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "bcs",
            "iat": now,
            "exp": now + 300,
            "principals": [
                {
                    "type": "user",
                    "tenant": _BCS_TENANT,
                    "subject": {
                        "id": owner_id,
                        "username": f"{owner_id}@example.test",
                        "display_name": None,
                        "full_name": None,
                        "tenant_id": _BCS_TENANT,
                    },
                }
            ],
        },
        _PRINCIPAL_KEY,
        algorithm="HS256",
        headers={"kid": "bare"},
    )


def _assert_status(response: httpx.Response, expected: int, operation: str) -> None:
    """Fail without echoing endpoints, credentials, or response bodies."""
    assert response.status_code == expected, (
        f"{operation} returned HTTP {response.status_code}; expected {expected}"
    )


def _ensure_human(client: httpx.Client, user_id: str) -> None:
    response = client.post(
        "/me/ensure-human",
        headers={"X-Mock-User-Id": user_id, "X-Mock-Nick-Name": user_id},
        json={},
    )
    _assert_status(response, 200, "BCS human registration")


def _register_owned_bot(client: httpx.Client, owner_id: str) -> str:
    mock_headers = {"X-Mock-User-Id": owner_id, "X-Mock-Nick-Name": owner_id}
    token_response = client.get("/register/token", headers=mock_headers)
    _assert_status(token_response, 200, "BCS registration-token issuance")
    token = token_response.json().get("token")
    assert isinstance(token, str) and token, "BCS registration token was absent"

    register_response = client.post(
        "/register",
        headers=mock_headers,
        params={"token": token, "bot-name": "Manager sync acceptance"},
    )
    _assert_status(register_response, 200, "BCS trusted Bot registration")
    bot_id = register_response.json().get("bot_uuid")
    assert isinstance(bot_id, str) and _REGISTERED_BOT_ID.fullmatch(bot_id), (
        "BCS trusted registration returned an unexpected Bot id shape"
    )
    return bot_id


def _ensure_projection_target(
    client: httpx.Client, *, bot_id: str, owner_id: str
) -> str:
    """Create the exact stable identity that the Backend projector addresses.

    Trusted ``/register`` intentionally generates canonical ``bot_<hex>`` ids;
    it does not accept the Backend's ``<bot_id>:<entity_id>`` identity. The
    local singlebox backfill surface therefore materializes that exact target
    with the same owner. This proves delivery/readback identity alignment, not
    the separate production Bot-registration lifecycle.
    """
    bcs_bot_id = f"{bot_id}:{owner_id}"
    response = client.post(
        f"/admin/bots/{quote(bcs_bot_id, safe='')}/ensure",
        # Canonical local E2E credential used by existing BCS stories. The
        # public local config has an empty api_keys registry and explicitly
        # accepts any non-empty fixture key for this backfill endpoint.
        headers={"X-BCS-Service-Key": _LOCAL_SERVICE_KEY},
        json={
            "staff_no": owner_id,
            "nick_name": owner_id,
            "name": "Manager sync projection target",
            "visibility": "protected",
        },
    )
    _assert_status(response, 200, "BCS projection-target ensure")
    body = response.json()
    assert body.get("bot_uuid") == bcs_bot_id
    assert body.get("ensured") is True
    return bcs_bot_id


def _initialize_fixture_ownership(bcs_bot_id: str) -> None:
    """Initialize ownership through the official maintenance CLI.

    This is fixture bootstrap for one freshly generated local Bot only. It is
    not a production registration assertion and never writes through SQL.
    """
    host = urlsplit(_BCS_BASE_URL).hostname
    assert host in {"127.0.0.1", "localhost"}, (
        "BCS ownership fixture bootstrap is restricted to a loopback stack"
    )
    dependencies_root = (_REPO_ROOT / "singlebox/.dependencies").resolve()
    config_dir = _BCS_CONFIG_DIR.resolve()
    admin_bin = _BCS_ADMIN_BIN.resolve()
    assert config_dir.is_relative_to(dependencies_root), (
        "BCS fixture config must be inside singlebox/.dependencies"
    )
    assert admin_bin.is_file() and os.access(admin_bin, os.X_OK), (
        "BCS fixture admin binary is missing or not executable"
    )
    assert config_dir.is_dir(), "BCS fixture runtime config directory is missing"
    data_dir = config_dir.parent / "bcs_data"
    assert data_dir.is_dir(), "BCS fixture runtime data directory is missing"

    env = os.environ.copy()
    env.update(
        {
            "BCS_CONFIG_DIR": str(config_dir),
            "BCS_DATA_DIR": str(data_dir),
            "SERVER_ENV": "local",
        }
    )
    with tempfile.TemporaryDirectory(prefix="manager-sync-bcs-admin-") as profile_dir:
        env["LLVM_PROFILE_FILE"] = str(
            Path(profile_dir) / "bcs-admin-%m-%p.profraw"
        )
        result = subprocess.run(
            [
                str(admin_bin),
                "--config-dir",
                str(config_dir),
                "--env",
                "dev",
                "bot-authority",
                "initialize",
                "--maintenance",
                "--sqlite-path",
                str(data_dir / "bcs.db"),
                "--bot-id",
                bcs_bot_id,
            ],
            cwd=_REPO_ROOT / "apps/bcs",
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert result.returncode == 0, (
        "BCS fixture ownership initialization command failed"
    )
    assert "state=backfilled" in result.stdout, (
        "BCS fixture ownership was not initialized for the fresh target"
    )


def _read_manager_ids(
    client: httpx.Client, *, bcs_bot_id: str, owner_id: str
) -> set[str]:
    response = client.get(
        f"/api/v1/collaboration/bots/{quote(bcs_bot_id, safe='')}/managers",
        headers={"X-Avernet-Principal": _owner_principal(owner_id)},
        params={"offset": 0, "limit": 100},
    )
    _assert_status(response, 200, "BCS owner manager readback")
    body = response.json()
    assert body.get("code") == 20000, "BCS manager readback envelope was not successful"
    data = body.get("data")
    assert isinstance(data, dict), "BCS manager readback data was absent"
    items = data.get("items")
    assert isinstance(items, list), "BCS manager readback items were absent"
    assert data.get("total") == len(items), "BCS manager readback page was inconsistent"
    assert all(isinstance(item, str) for item in items)
    return set(items)


def _wait_for_roster(
    read: Callable[[], set[str]], expected: set[str], *, timeout_seconds: float = 30.0
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_seen: set[str] | None = None
    while time.monotonic() < deadline:
        try:
            last_seen = read()
        except AssertionError:
            time.sleep(0.25)
            continue
        if last_seen == expected:
            return
        time.sleep(0.25)
    raise AssertionError(
        "BCS manager roster did not converge to the exact expected user set; "
        f"expected_count={len(expected)}, last_seen_count="
        f"{None if last_seen is None else len(last_seen)}"
    )


def _add_collaborator(
    client: httpx.Client, *, bot_id: str, owner_id: str, user_id: str, role: str
) -> int:
    response = client.post(
        "/api/bot/collaborator/add",
        json={
            "bot_id": bot_id,
            "owner_id": owner_id,
            "user_id": user_id,
            "user_name": user_id,
            "role": role,
        },
    )
    _assert_status(response, 200, f"Backend collaborator {role} add")
    body = response.json()
    assert body.get("success") is True, f"Backend collaborator {role} add failed"
    row_id = body.get("data", {}).get("id")
    assert isinstance(row_id, int) and row_id > 0
    return row_id


def _remove_collaborator(client: httpx.Client, row_id: int) -> None:
    response = client.post("/api/bot/collaborator/remove", json={"id": row_id})
    _assert_status(response, 200, "Backend collaborator removal")
    assert response.json().get("success") is True, "Backend collaborator removal failed"


@pytest.mark.acceptance
def test_live_backend_worker_projects_exact_bcs_manager_roster(live_backend):
    """Admin/member add and removal converge through the real Backend→BCS path."""
    owner_id = _fresh_staff_id("owner")
    admin_id = _fresh_staff_id("admin")
    member_id = _fresh_staff_id("member")
    created_rows: list[int] = []
    registered_bot_id: str | None = None
    bcs_bot_id: str | None = None

    with httpx.Client(base_url=_BCS_BASE_URL, timeout=30.0) as bcs_client:
        try:
            registered_bot_id = _register_owned_bot(bcs_client, owner_id)
            for user_id in (owner_id, admin_id, member_id):
                _ensure_human(bcs_client, user_id)
            bcs_bot_id = _ensure_projection_target(
                bcs_client, bot_id=registered_bot_id, owner_id=owner_id
            )
            _initialize_fixture_ownership(bcs_bot_id)

            with httpx.Client(
                base_url=live_backend,
                headers={"x-user-id": owner_id},
                timeout=60.0,
            ) as backend_client:
                _seed_member_managed_bot(
                    backend_client,
                    owner_id=owner_id,
                    bot_id=registered_bot_id,
                    active_engine="openclaw",
                )
                read_roster = lambda: _read_manager_ids(  # noqa: E731
                    bcs_client, bcs_bot_id=bcs_bot_id, owner_id=owner_id
                )
                try:
                    assert read_roster() == set()

                    created_rows.append(
                        _add_collaborator(
                            backend_client,
                            bot_id=registered_bot_id,
                            owner_id=owner_id,
                            user_id=admin_id,
                            role="admin",
                        )
                    )
                    created_rows.append(
                        _add_collaborator(
                            backend_client,
                            bot_id=registered_bot_id,
                            owner_id=owner_id,
                            user_id=member_id,
                            role="member",
                        )
                    )
                    _wait_for_roster(read_roster, {admin_id, member_id})
                    assert owner_id not in read_roster()

                    _remove_collaborator(backend_client, created_rows.pop(0))
                    _wait_for_roster(read_roster, {member_id})

                    _remove_collaborator(backend_client, created_rows.pop(0))
                    _wait_for_roster(read_roster, set())
                finally:
                    for row_id in created_rows:
                        try:
                            _remove_collaborator(backend_client, row_id)
                        except Exception:
                            pass
        finally:
            mock_headers = {
                "X-Mock-User-Id": owner_id,
                "X-Mock-Nick-Name": owner_id,
            }
            for bot_id in (bcs_bot_id, registered_bot_id):
                if bot_id is None:
                    continue
                try:
                    bcs_client.delete(
                        f"/bots/{quote(bot_id, safe='')}", headers=mock_headers
                    )
                except httpx.HTTPError:
                    pass
