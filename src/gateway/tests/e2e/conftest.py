from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio

DEFAULT_PORT = int(os.environ.get("GATEWAY_E2E_PORT", "8888"))
DEFAULT_BASE_URL = f"http://127.0.0.1:{DEFAULT_PORT}"


@pytest_asyncio.fixture(scope="function")
async def http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        base_url=DEFAULT_BASE_URL,
        timeout=httpx.Timeout(10.0),
        follow_redirects=True,
    ) as client:
        yield client
