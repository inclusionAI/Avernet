"""WebSocket test utilities for E2E tests."""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

from ..conftest import DEFAULT_BASE_URL

DEFAULT_WS_BASE = DEFAULT_BASE_URL.replace("http://", "ws://").replace(
    "https://", "wss://"
)


def build_ws_url(
    path: str = "/ws/local/management",
    machine_id: str = "test-machine",
    **kwargs: str,
) -> str:
    """Build a WebSocket URL with query parameters.

    Returns a full ws:// URL, e.g. ws://localhost:8888/ws/local/management?machine_id=test-machine
    """
    params = [f"machine_id={machine_id}"]
    for k, v in kwargs.items():
        params.append(f"{k}={v}")
    return f"{DEFAULT_WS_BASE}{path}?{'&'.join(params)}"


def build_jwt_token(
    sno: str = "test-sno-001",
    machine_id: str = "test-machine-001",
    machine_name: str = "e2e-test-machine",
) -> str:
    import base64
    import json

    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        .decode()
        .rstrip("=")
    )
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"sno": sno, "machine_id": machine_id, "machine_name": machine_name}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.fake-signature"


def build_invalid_jwt_token() -> str:
    return "invalid.token.format"


@pytest.fixture(scope="session")
def ws_base_url() -> str:
    return DEFAULT_WS_BASE


@pytest_asyncio.fixture
async def ws_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client for WebSocket upgrade testing."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client
