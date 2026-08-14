"""Unit tests for teamclaw token router endpoints.

Tests the POST /api/teamclaw/token endpoint including:
- Happy path: ARCA device returns 200 with JWT
- Permission denied: device not owned by user returns 403
- Unsupported platform: LOCAL device returns 501
- Token structure: decoded JWT contains correct claims
- Different secret key: JWT signed with teamclaw secret, not proxypass secret
"""

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.teamclaw.teamclaw_token_router import (
    router,
)
from tests.unit.adapters.web.conftest import iter_api_routes

# ── Create test app with the teamclaw token router ──────────────────────────
app = FastAPI()
app.include_router(router)


# ── Helper: build a mock DeviceResponse (MagicMock with attrs) ──────────────
def _make_device_response(
    *,
    device_uuid: str = "device-uuid-001",
    tenant: str = "test_tenant",
    env: str = "dev",
    creator: str = "000001",
    provider_type: str = "arca",
) -> MagicMock:
    """Build a MagicMock that quacks like a DeviceResponse Pydantic model."""
    mock = MagicMock()
    mock.id = 1
    mock.device_uuid = device_uuid
    mock.tenant = tenant
    mock.env = env
    mock.domain = "default"
    mock.status = "ACTIVE"
    mock.provider_type = provider_type
    mock.provider_device_id = f"ARCA_sandbox_{device_uuid}@42"
    mock.provider_device_props = {}
    mock.extra_config = None
    mock.err_msg = None
    mock.creator = creator
    mock.modifier = creator
    mock.gmt_create = "2026-08-14T00:00:00"
    mock.gmt_modified = "2026-08-14T00:00:00"
    return mock


def _make_bot_device_rel_record(bot_id: int = 1, device_uuid: str = "device-uuid-001", creator: str = "000001") -> MagicMock:
    """Build a mock BotDeviceRelRecord."""
    record = MagicMock()
    record.id = 1
    record.bot_id = bot_id
    record.device_uuid = device_uuid
    record.tenant = "test_tenant"
    record.env = "dev"
    record.creator = creator
    return record


def _make_bot_record(bot_uuid: str = "bot-uuid-001", creator: str = "000001") -> MagicMock:
    """Build a mock BotRecord."""
    record = MagicMock()
    record.id = 1
    record.bot_uuid = bot_uuid
    record.tenant = "test_tenant"
    record.env = "dev"
    record.creator = creator
    return record


def _make_auth_user(staff_id: str = "000001") -> MagicMock:
    """Build a mock AuthUser."""
    user = MagicMock()
    user.staffId = staff_id
    user.id = f"user-{staff_id}"
    user.operatorName = "test_operator"
    return user


# ── Fixture: mock all dependencies via dependency_overrides ──────────────────


@pytest.fixture
def mocks():
    """Set up mock dependencies and return them for assertion.

    Overrides all dependencies injected into the teamclaw token endpoint.
    """
    mock_auth_service = AsyncMock()
    mock_device_service = MagicMock()
    mock_secret_plugin = MagicMock()
    mock_bot_device_rel_repo = MagicMock()
    mock_bot_repo = MagicMock()

    overrides = {}

    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            name = dep.name
            if name == "auth_service":
                overrides[dep.call] = lambda: mock_auth_service
            elif name == "device_service":
                overrides[dep.call] = lambda: mock_device_service
            elif name == "secret_plugin":
                overrides[dep.call] = lambda: mock_secret_plugin
            elif name == "bot_device_rel_repo":
                overrides[dep.call] = lambda: mock_bot_device_rel_repo
            elif name == "bot_repo":
                overrides[dep.call] = lambda: mock_bot_repo

    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.update(overrides)

    yield {
        "auth_service": mock_auth_service,
        "device_service": mock_device_service,
        "secret_plugin": mock_secret_plugin,
        "bot_device_rel_repo": mock_bot_device_rel_repo,
        "bot_repo": mock_bot_repo,
    }

    app.dependency_overrides = old_overrides


# ── Helper: set up the happy-path mocks ─────────────────────────────────────


def _setup_happy_path(mocks, *, device_uuid="device-uuid-001", bot_uuid="bot-uuid-001",
                       user_staff_id="000001", provider_type="arca", secret_key="test-teamclaw-secret"):
    """Configure all mocks for a successful token issuance."""
    auth_user = _make_auth_user(staff_id=user_staff_id)
    mocks["auth_service"].authenticate_request.return_value = auth_user

    device_resp = _make_device_response(
        device_uuid=device_uuid,
        creator=user_staff_id,
        provider_type=provider_type,
    )
    mocks["device_service"].get_device_info.return_value = device_resp

    rel_record = _make_bot_device_rel_record(
        bot_id=1, device_uuid=device_uuid, creator=user_staff_id
    )
    mocks["bot_device_rel_repo"].get_by_device_uuid.return_value = rel_record

    bot_record = _make_bot_record(bot_uuid=bot_uuid, creator=user_staff_id)
    mocks["bot_repo"].get_by_id.return_value = bot_record

    mocks["secret_plugin"].get_secret.return_value = secret_key


def _setup_auth_and_device(mocks, *, device_uuid="device-uuid-001", auth_user=None,
                            device_creator="000001", provider_type="arca"):
    """Configure auth and device mocks only (no bot/rel setup)."""
    if auth_user is None:
        auth_user = _make_auth_user(staff_id=device_creator)
    mocks["auth_service"].authenticate_request.return_value = auth_user

    device_resp = _make_device_response(
        device_uuid=device_uuid,
        creator=device_creator,
        provider_type=provider_type,
    )
    mocks["device_service"].get_device_info.return_value = device_resp


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: Happy path — ARCA device returns 200
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generate_token_happy_path_arca(mocks):
    """POST /api/teamclaw/token?device_id=ARCA_device returns 200 with valid response."""
    _setup_happy_path(mocks)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "token" in data
    assert data["device_id"] == "device-uuid-001"
    assert data["bot_uuid"] == "bot-uuid-001"
    assert "expires_at" in data
    # expires_at should be ~600s from now
    now = int(time.time())
    assert now + 590 <= data["expires_at"] <= now + 610


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: Permission denied — device not owned by user
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generate_token_permission_denied(mocks):
    """POST with device_id not owned by current user returns 403."""
    _setup_auth_and_device(mocks, device_creator="owner-001")

    # Authenticate as a different user
    other_user = _make_auth_user(staff_id="intruder-002")
    mocks["auth_service"].authenticate_request.return_value = other_user

    # Bot belongs to owner-001
    rel_record = _make_bot_device_rel_record(bot_id=1, device_uuid="device-uuid-001", creator="owner-001")
    mocks["bot_device_rel_repo"].get_by_device_uuid.return_value = rel_record
    bot_record = _make_bot_record(bot_uuid="bot-uuid-001", creator="owner-001")
    mocks["bot_repo"].get_by_id.return_value = bot_record

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["error_code"] == "ACCESS_DENIED"


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: Unsupported platform — LOCAL device returns 501
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generate_token_unsupported_platform_local(mocks):
    """POST with LOCAL device_id returns 501 with PLATFORM_NOT_SUPPORTED."""
    _setup_auth_and_device(mocks, provider_type="local")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 501
    detail = resp.json()["detail"]
    assert detail["error_code"] == "PLATFORM_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_generate_token_unsupported_platform_k8s(mocks):
    """POST with K8S device_id returns 501 with PLATFORM_NOT_SUPPORTED."""
    _setup_auth_and_device(mocks, provider_type="k8s")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 501
    detail = resp.json()["detail"]
    assert detail["error_code"] == "PLATFORM_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_generate_token_unsupported_platform_docker(mocks):
    """POST with DOCKER device_id returns 501 with PLATFORM_NOT_SUPPORTED."""
    _setup_auth_and_device(mocks, provider_type="docker")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 501
    detail = resp.json()["detail"]
    assert detail["error_code"] == "PLATFORM_NOT_SUPPORTED"


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: Token structure — decoded JWT contains correct claims
# ═══════════════════════════════════════════════════════════════════════════


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload (middle part) without verifying signature."""
    parts = token.split(".")
    assert len(parts) == 3, f"Expected 3 JWT parts, got {len(parts)}"
    # Add padding for base64url decode
    payload_b64 = parts[1]
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    payload_bytes = base64.urlsafe_b64decode(payload_b64)
    return json.loads(payload_bytes)


@pytest.mark.asyncio
async def test_generate_token_jwt_structure(mocks):
    """Decoded JWT contains {sub, bot_uuid, device_id, exp} with exp ~now+600s."""
    _setup_happy_path(mocks)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 200
    data = resp.json()["data"]
    token = data["token"]

    # Decode JWT payload
    payload = _decode_jwt_payload(token)

    assert payload["sub"] == "000001"
    assert payload["bot_uuid"] == "bot-uuid-001"
    assert payload["device_id"] == "device-uuid-001"
    now = int(time.time())
    assert now + 590 <= payload["exp"] <= now + 610


@pytest.mark.asyncio
async def test_generate_token_expires_at_matches_jwt_exp(mocks):
    """Response expires_at matches the exp claim in the JWT."""
    _setup_happy_path(mocks)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 200
    data = resp.json()["data"]

    payload = _decode_jwt_payload(data["token"])
    assert payload["exp"] == data["expires_at"]


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: Different secret key — JWT signed with teamclaw secret
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generate_token_uses_teamclaw_secret(mocks):
    """JWT is signed with teamclaw_jwt_secret, NOT proxypass_secret."""
    teamclaw_secret = "teamclaw-terminal-jwt-secret-123"
    _setup_happy_path(mocks, secret_key=teamclaw_secret)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 200
    data = resp.json()["data"]

    # Verify the secret_plugin.get_secret was called with the correct name
    mocks["secret_plugin"].get_secret.assert_called_once_with(
        "other_manual_teamclaw_terminal_jwt_secret"
    )

    # Verify JWT can be manually re-signed with the same secret (proof of correct key)
    import hashlib
    import hmac

    parts = data["token"].split(".")
    signing_input = f"{parts[0]}.{parts[1]}"
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(teamclaw_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    assert parts[2] == expected_sig, "JWT signature does not match teamclaw secret"


@pytest.mark.asyncio
async def test_generate_token_rejects_wrong_secret(mocks):
    """JWT signed with teamclaw secret cannot be verified with proxypass secret."""
    teamclaw_secret = "teamclaw-terminal-jwt-secret-123"
    proxypass_secret = "proxypass-different-secret-456"
    _setup_happy_path(mocks, secret_key=teamclaw_secret)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 200
    data = resp.json()["data"]

    # Verify that re-signing with proxypass_secret produces a DIFFERENT signature
    import hashlib
    import hmac

    parts = data["token"].split(".")
    signing_input = f"{parts[0]}.{parts[1]}"
    wrong_sig = base64.urlsafe_b64encode(
        hmac.new(proxypass_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    assert parts[2] != wrong_sig, (
        "JWT signature should NOT match proxypass secret — "
        "must use independent teamclaw secret"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generate_token_device_not_found(mocks):
    """POST with non-existent device_id returns 404."""
    mocks["auth_service"].authenticate_request.return_value = _make_auth_user()
    mocks["device_service"].get_device_info.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=nonexistent")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error_code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_generate_token_device_not_linked_to_bot(mocks):
    """POST with device not linked to any bot returns 403."""
    _setup_auth_and_device(mocks)
    # No bot-device relationship
    mocks["bot_device_rel_repo"].get_by_device_uuid.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["error_code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_generate_token_teclaw_platform_supported(mocks):
    """POST with TeClaw device_id returns 200 (TeClaw is supported)."""
    _setup_happy_path(mocks, provider_type="teclaw")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/teamclaw/token?device_id=device-uuid-001")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "token" in data


@pytest.mark.asyncio
async def test_generate_token_logs_issuance(mocks):
    """Token issuance is logged at INFO level."""
    _setup_happy_path(mocks)

    with patch("secbaas.community.adapters.web.routers.teamclaw.teamclaw_token_router.logger") as mock_logger:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/teamclaw/token?device_id=device-uuid-001")

        mock_logger.info.assert_called()