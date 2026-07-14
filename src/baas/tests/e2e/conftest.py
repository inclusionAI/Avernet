"""E2E test fixtures and configuration."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Generator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from tests.utils import load_web_port

DEFAULT_BASE_URL = f"http://localhost:{load_web_port()}"
DEFAULT_TENANT = "team_claw"
DEFAULT_TEMPLATE_UUID = "TEMPLATE-4d0e2849d7004111836333de782b95d8"
DEFAULT_TIMEOUT = 120.0


def pytest_report_teststatus(report, config):
    if report.when == "call":
        return (
            report.outcome,
            report.outcome[0].upper(),
            (f"{report.outcome.upper()} ({report.duration:.2f}s)"),
        )


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def app_base_url() -> str:
    """Return the base URL for the running application."""
    return DEFAULT_BASE_URL


@pytest.fixture(scope="session")
def test_tenant() -> str:
    """Return the default test tenant."""
    return DEFAULT_TENANT


@pytest.fixture(scope="session")
def test_template_uuid() -> str:
    """Return the default test template UUID."""
    return DEFAULT_TEMPLATE_UUID


@pytest_asyncio.fixture(scope="function")
async def http_client(
    app_base_url: str,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Create async HTTP client for API testing."""
    async with httpx.AsyncClient(
        base_url=app_base_url,
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
    ) as client:
        yield client


@pytest.fixture
def unique_id() -> str:
    """Generate unique ID for test isolation."""
    return uuid4().hex[:16]


@pytest.fixture
def unique_request_id(unique_id: str) -> str:
    """Generate unique request ID for idempotent operations."""
    return uuid4().hex


@pytest.fixture
def created_bot_uuids() -> list[str]:
    """Track bot UUIDs created during tests for cleanup."""
    return []


@pytest.fixture
def created_publish_ids() -> list[int]:
    """Track publish IDs created during tests for cleanup."""
    return []


class APITestHelper:
    """Helper class for E2E API testing."""

    def __init__(self, client: httpx.AsyncClient, tenant: str):
        self.client = client
        self.tenant = tenant

    def bot_url(self, bot_uuid: str | None = None) -> str:
        """Build bot API URL."""
        base = "/api/v1/bots"
        if bot_uuid:
            return f"{base}/{bot_uuid}"
        return base

    def publish_url(
        self, publish_id: int | None = None, action: str | None = None
    ) -> str:
        """Build publish API URL."""
        base = "/api/v1/publishes"
        if publish_id:
            if action:
                return f"{base}/{publish_id}/{action}"
            return f"{base}/{publish_id}"
        return base

    def session_url(self, session_id: str | None = None) -> str:
        """Build session API URL."""
        base = "/api/v1/sessions"
        if session_id:
            return f"{base}/{session_id}"
        return base

    def tenant_url(self, identifier: str | None = None) -> str:
        """Build tenant API URL."""
        base = "/api/v1/tenants"
        if identifier:
            return f"{base}/{identifier}"
        return base

    def api_key_url(
        self, key_prefix: str | None = None, action: str | None = None
    ) -> str:
        """Build user-facing API key URL (/api/v1/api-keys/...)."""
        base = "/api/v1/api-keys"
        if key_prefix:
            if action:
                return f"{base}/{key_prefix}/{action}"
            return f"{base}/{key_prefix}"
        if action:
            return f"{base}/{action}"
        return base

    def admin_api_key_url(self, key_prefix: str | None = None) -> str:
        """Build admin API key URL (/api/v1/admin/api-keys/...)."""
        base = "/api/v1/admin/api-keys"
        if key_prefix:
            return f"{base}/{key_prefix}"
        return base

    def system_config_url(self, conf_key: str | None = None) -> str:
        """Build system config API URL."""
        base = "/api/v1/system-configs"
        if conf_key:
            return f"{base}/{conf_key}"
        return base

    def paas_device_url(
        self, device_id: str | None = None, action: str | None = None
    ) -> str:
        """Build PaaS device API URL."""
        base = "/api/v1/paas/devices"
        if device_id:
            if action:
                return f"{base}/{device_id}/{action}"
            return f"{base}/{device_id}"
        return base

    def bot_invoke_url(self, bot_uuid: str, async_mode: bool = False) -> str:
        """Build bot invocation URL."""
        path = "invoke/async" if async_mode else "invoke"
        return f"/api/v1/bots/{bot_uuid}/{path}"

    def callback_url(self) -> str:
        """Build device callback API URL."""
        return "/api/v1/publish/device-callback"

    def admin_force_success_url(self) -> str:
        """Build admin force-success API URL."""
        return "/api/v1/admin/force-success"

    def params(self, **kwargs: Any) -> dict[str, Any]:
        """Build query params with tenant."""
        params = {"tenant": self.tenant}
        params.update({k: v for k, v in kwargs.items() if v is not None})
        return params


@pytest.fixture
def api(http_client: httpx.AsyncClient, test_tenant: str) -> APITestHelper:
    """Create API test helper."""
    return APITestHelper(http_client, test_tenant)


async def create_test_bot(
    api: APITestHelper,
    name: str,
    operator: str = "e2e-test",
    template_uuid: str | None = None,
    device_count: int = 1,
    request_id: str | None = None,
    deploy_config: dict[str, Any] | None = None,
    callback_timeout_seconds: int | None = None,
    auto_approve_publish: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "template_uuid": template_uuid or DEFAULT_TEMPLATE_UUID,
        "device_count": device_count,
        "operator": operator,
        "request_id": request_id or uuid4().hex,
    }
    config: dict[str, Any] = {}
    if deploy_config is not None:
        config["deploy_config"] = deploy_config
    if callback_timeout_seconds is not None:
        config["callback_timeout_seconds"] = callback_timeout_seconds
    if auto_approve_publish:
        config["auto_approve_publish"] = auto_approve_publish
    if config:
        body["config"] = config
    response = await api.client.post(
        api.bot_url(),
        params=api.params(),
        json=body,
    )
    response.raise_for_status()
    return response.json()["data"]


async def activate_test_bot(
    api: APITestHelper,
    bot: dict[str, Any],
    operator: str = "e2e-test",
) -> None:
    """Activate a bot by approving its initial publish and waiting for completion.

    Works with both hook and no-hook bots:
    - No hook: approve returns SUCCESS immediately (fast path)
    - With hook: approve returns APPROVING, internal callback drives completion

    Handles multi-stage publishes by approving each stage until SUCCESS/FAILED.
    """
    publish_id = bot.get("publish_id")
    if not publish_id:
        return

    # Approve (PENDING → ACTIVE, auto-executes first stage)
    r = await api.client.post(
        api.publish_url(publish_id, "approve"),
        params=api.params(),
        json={"operator": operator, "request_id": uuid4().hex},
    )
    if r.status_code != 200:
        return

    # Wait for callback-driven or fast-path completion
    await _wait_for_publish_status(api, publish_id, {"SUCCESS", "FAILED"})

    # Approve subsequent stages until done
    for _ in range(10):
        resp = await api.client.get(api.publish_url(publish_id), params=api.params())
        if resp.status_code != 200:
            break
        status = resp.json()["data"]["status"]
        if status in ("SUCCESS", "FAILED", "REJECTED", "REVOKED"):
            break
        if status == "APPROVING":
            r = await api.client.post(
                api.publish_url(publish_id, "approve"),
                params=api.params(),
                json={"operator": operator, "request_id": uuid4().hex},
            )
            if r.status_code != 200:
                break
            await _wait_for_publish_status(
                api, publish_id, {"SUCCESS", "FAILED", "APPROVING"}
            )
        else:
            await asyncio.sleep(0.5)


async def _wait_for_publish_status(
    api: APITestHelper,
    publish_id: int,
    target_statuses: set[str],
    timeout_seconds: float = 0.5,
    poll_interval: float = 0.5,
) -> str:
    """Poll until publish reaches one of the target statuses."""
    import asyncio

    elapsed = 0.0
    status = "UNKNOWN"
    while elapsed < timeout_seconds:
        resp = await api.client.get(api.publish_url(publish_id), params=api.params())
        if resp.status_code != 200:
            break
        status = resp.json()["data"]["status"]
        if status in target_statuses:
            return status
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return status


async def find_existing_bot(
    api: APITestHelper,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Find an existing bot in the system (avoids creating new PaaS resources).

    Prefers ACTIVE, then PENDING, then FAILED bots; skips DESTROYING unless
    explicitly requested via status parameter.

    Returns bot data dict or None if no bots found.
    """
    params = api.params(page=1, page_size=1)
    if status:
        params["status"] = status
        response = await api.client.get(api.bot_url(), params=params)
        if response.status_code != 200:
            return None
        data = response.json()["data"]
        if not data["items"]:
            return None
        return data["items"][0]

    # Prefer statuses that allow mutation (ACTIVE, PENDING, FAILED)
    for preferred in ("ACTIVE", "PENDING", "FAILED"):
        response = await api.client.get(
            api.bot_url(), params=api.params(page=1, page_size=1, status=preferred)
        )
        if response.status_code != 200:
            continue
        data = response.json()["data"]
        if data["items"]:
            return data["items"][0]

    return None


async def cleanup_bot(
    api: APITestHelper, bot_uuid: str, operator: str = "e2e-cleanup"
) -> None:
    """Helper to destroy a bot."""
    try:
        await api.client.post(
            api.bot_url(bot_uuid) + "/destroy",
            params=api.params(),
            json={
                "operator": operator,
                "request_id": uuid4().hex,
            },
        )
    except Exception:
        pass  # Ignore cleanup errors


async def call_device_callback(
    api: APITestHelper,
    device_uuid: str,
    publish_id: int,
    event_type: str = "start",
    result_status: str = "SUCCESS",
    exit_code: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> httpx.Response:
    """Call the device callback endpoint."""
    body: dict[str, Any] = {
        "device_uuid": device_uuid,
        "publish_id": publish_id,
        "event_type": event_type,
        "result_status": result_status,
        "tenant": api.tenant,
    }
    if exit_code is not None:
        body["exit_code"] = exit_code
    if stdout is not None:
        body["stdout"] = stdout
    if stderr is not None:
        body["stderr"] = stderr
    return await api.client.post(
        api.callback_url(),
        json=body,
    )


# ── Logging configuration ────────────────────────────────────────────────────


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Set timestamp format on cli log handler."""
    handler = config.pluginmanager.get_plugin("logging-plugin")
    if handler is None:
        return
    cli_handler = getattr(handler, "handler", None)
    if cli_handler is None:
        return
    fmt = "%(asctime)s %(levelname)s %(name)s:%(message)s"
    cli_handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
