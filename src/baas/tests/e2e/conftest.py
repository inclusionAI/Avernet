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

# Template UUIDs for all 7 PaaS platform types — seeded in _seed.py
TEMPLATE_ARCA = "TEMPLATE-4d0e2849d7004111836333de782b95d8"
TEMPLATE_LOCAL = "TEMPLATE-f996ecc77d224ef7bd80757d8d2bcd0d"
TEMPLATE_POOLAB = "TEMPLATE-54942a40aa794eaaae2be166f94890ed"
TEMPLATE_TECLAW = "TEMPLATE-3106e731ffb04e0285e27c387e153737"
TEMPLATE_K8S = "TEMPLATE-8e4a2a3b4c5d4e6f7a8b9c0d1e2f3a4b"
TEMPLATE_DOCKER = "TEMPLATE-9f5b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"
TEMPLATE_SIGMA = "TEMPLATE-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"

# Backward-compatible alias — prefer TEMPLATE_ARCA in new code
DEFAULT_TEMPLATE_UUID = TEMPLATE_ARCA

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
        params: dict[str, Any] = {"tenant": self.tenant}
        params.update({k: v for k, v in kwargs.items() if v is not None})
        return params

    # ── Bot Runtime / Dispatcher URLs ──────────────────────────────────────

    def bot_http_conn_url(self, bot_uuid: str) -> str:
        """Build bot HTTP connection info URL."""
        return f"/api/v1/bots/{bot_uuid}/http-info"

    def bot_wss_conn_url(self, bot_uuid: str) -> str:
        """Build bot WSS connection info URL."""
        return f"/api/v1/bots/{bot_uuid}/ws-info"

    def bot_cmd_url(self, bot_uuid: str) -> str:
        """Build bot CMD endpoint URL.

        NOTE: The actual route is tenant-scoped:
        /api/v1/bots/{tenant}/{bot_uuid}/execute-command

        This builder uses a tenant-agnostic format — callers should
        construct the full URL manually if tenant is required.
        """
        return f"/api/v1/bots/{self.tenant}/{bot_uuid}/execute-command"

    def bot_open_folder_url(self, bot_uuid: str) -> str:
        """Build bot open-folder endpoint URL.

        NOTE: The actual route is tenant-scoped:
        /api/v1/bots/{tenant}/{bot_uuid}/open-folder
        """
        return f"/api/v1/bots/{self.tenant}/{bot_uuid}/open-folder"

    def bot_start_progress_url(self, bot_uuid: str) -> str:
        """Build bot start progress URL."""
        return f"/api/v1/bots/{bot_uuid}/start-progress"

    def bot_devices_url(self, bot_uuid: str) -> str:
        """Build bot devices list URL."""
        return f"/api/v1/bots/{bot_uuid}/devices"

    def bot_sessions_url(self, bot_uuid: str, session_id: str | None = None) -> str:
        """Build bot sessions URL.

        NOTE: No dedicated sessions sub-route exists in the OpenAPI.
        Sessions are managed via /api/v1/sessions.
        """
        base = "/api/v1/sessions"
        if session_id:
            return f"{base}/{session_id}"
        return base

    def bot_detail_url(self, bot_uuid: str) -> str:
        """Build bot detail-by-uuid URL."""
        return f"{self.bot_url(bot_uuid)}/detail-by-uuid"

    # ── Device / Device Binding URLs ───────────────────────────────────────

    def device_url(self, device_uuid: str) -> str:
        """Build device detail URL."""
        return f"/api/v1/devices/{device_uuid}"

    def device_binding_url(self) -> str:
        """Build device binding query URL."""
        return "/api/v1/device-bindings"

    # ── Health Check URLs ──────────────────────────────────────────────────

    def bot_health_url(self, bot_uuid: str | None = None) -> str:
        """Build bot health check URL.

        The actual route is /api/v1/bot-health-checker/health.
        Passing bot_uuid adds it as a query param.
        """
        return "/api/v1/bot-health-checker/health"

    def paas_health_url(self) -> str:
        """Build PaaS health check URL.

        NOTE: No dedicated PaaS health route exists. Tests that use this
        should expect 404 or be skipped.
        """
        return "/api/v1/health-check/paas"

    def sandbox_health_url(self) -> str:
        """Build sandbox health check URL.

        The actual route is /api/v1/bot-health-checker/sandbox.
        """
        return "/api/v1/bot-health-checker/sandbox"

    # ── Open API URLs ──────────────────────────────────────────────────────

    def open_api_message_url(self) -> str:
        """Build open API message endpoint URL.

        NOTE: No /api/v1/open/* routes exist in the main OpenAPI.
        The stub-adapter-boot test references /openapi/v1/messages/stream.
        """
        return "/openapi/v1/messages"

    def open_api_run_url(self) -> str:
        """Build open API run endpoint URL.

        NOTE: No /api/v1/open/* routes exist in the main OpenAPI.
        """
        return "/openapi/v1/runs"

    def open_api_session_url(self, session_id: str | None = None) -> str:
        """Build open API session URL.

        NOTE: No /api/v1/open/* routes exist in the main OpenAPI.
        """
        base = "/openapi/v1/sessions"
        if session_id:
            return f"{base}/{session_id}"
        return base

    # ── SSE URLs ────────────────────────────────────────────────────────────

    def sse_url(self, channel: str | None = None) -> str:
        """Build SSE subscription URL.

        NOTE: No /api/v1/sse route exists in OpenAPI.
        SSE is handled via dedicated bot-health-checker routes.
        Tests using this should be skipped or expect 404.
        """
        return "/api/v1/sse/events"

    # ── BCN URLs ────────────────────────────────────────────────────────────

    def bcn_downlink_url(self) -> str:
        """Build BCN downlink URL.

        NOTE: No /bcn/downlink route exists in OpenAPI.
        BCN is likely served on a separate port or via a different router.
        Tests using this should be skipped.
        """
        return "/api/v1/bcn/downlink"

    # ── Relay Session URLs ──────────────────────────────────────────────────

    def relay_session_url(self, session_id: str | None = None) -> str:
        """Build relay session URL.

        NOTE: No dedicated relay-sessions route exists in OpenAPI.
        The closest equivalent is /api/v1/paas/devices.
        Tests using this should be skipped.
        """
        return "/api/v1/relay-sessions"

    def health_alive_url(self) -> str:
        return "/api/v1/bot-health-checker/alive"

    def health_active_bots_url(self) -> str:
        return "/api/v1/bot-health-checker/active-bots"

    def health_paas_devices_url(self, bot_uuid: str) -> str:
        return f"/api/v1/bot-health-checker/{bot_uuid}/paas-devices"

    def health_extend_ttl_url(self) -> str:
        return "/api/v1/bot-health-checker/extend-ttl"

    def health_sandbox_url(self) -> str:
        return "/api/v1/bot-health-checker/sandbox"

    def open_api_message_stream_url(self) -> str:
        return "/openapi/v1/messages/stream"

    def http_relay_url(self, path: str = "") -> str:
        return (
            f"/api/v1/bots/relay/{path.lstrip('/')}" if path else "/api/v1/bots/relay"
        )

    def start_progress_error_url(self) -> str:
        return "/api/v1/bots/start-progress/error"

    def bcn_downlink_stream_url(self) -> str:
        return "/bcn/downlink/stream"

    def paas_device_outbound_rule_url(self, device_id: str) -> str:
        return f"/api/v1/paas/devices/{device_id}/outbound-rule"

    def paas_device_invoke_http_url(
        self, device_id: str, port: int, path: str = ""
    ) -> str:
        base = f"/api/v1/paas/devices/{device_id}/invoke-http/{port}"
        if path:
            return f"{base}/{path.lstrip('/')}"
        return base

    def paas_device_open_folder_url(self, device_id: str) -> str:
        return f"/api/v1/paas/devices/{device_id}/open-folder"

    def paas_device_ttl_url(self, device_id: str) -> str:
        return f"/api/v1/paas/devices/{device_id}/ttl"

    def local_machine_info_url(self, machine_id: str) -> str:
        return f"/api/v1/local/machines/{machine_id}/info"

    def local_machine_res_dirs_url(
        self, machine_id: str, resource_dir: str | None = None
    ) -> str:
        base = f"/api/v1/local/machines/{machine_id}/res-dirs"
        if resource_dir:
            return f"{base}?dir={resource_dir}"
        return base

    def local_user_machines_url(self, user_id: str) -> str:
        return f"/api/v1/local/users/{user_id}/machines"

    # ── Config / Template / QPM / Tenant URLs ───────────────────────────────

    def device_template_url(self, template_uuid: str | None = None) -> str:
        """Build device template URL."""
        base = "/api/v1/device-templates"
        if template_uuid:
            return f"{base}/{template_uuid}"
        return base

    def qpm_config_url(self, bot_id: str | None = None) -> str:
        """Build bot QPM config URL.

        The actual route is /api/v1/bot-qpm (not under /bots/{id}/qpm).
        """
        base = "/api/v1/bot-qpm"
        if bot_id:
            return f"{base}/{bot_id}"
        return base

    # ── Internal URLs ───────────────────────────────────────────────────────

    def internal_health_url(self) -> str:
        """Build internal health check URL.

        NOTE: No dedicated internal/health route exists.
        The closest equivalent is /api/v1/bot-health-checker/health.
        """
        return "/api/v1/bot-health-checker/health"

    def internal_cache_url(self) -> str:
        """Build internal cache URL.

        The actual cache route is /api/v1/cache/{key} (key-scoped, not general).
        This builder returns the base prefix for cache tests.
        """
        return "/api/v1/cache"

    def internal_management_url(self) -> str:
        """Build internal management URL.

        NOTE: Get /api/v1/bot-health-checker/active_bots for bot management status.
        """
        return "/api/v1/bot-health-checker/active_bots"

    # ── Distributed Lock URLs ───────────────────────────────────────────────

    def lock_url(self, lock_key: str | None = None) -> str:
        """Build distributed lock URL.

        NOTE: No locks route exists in the OpenAPI. This is a stub.
        Tests using this should be skipped.
        """
        if lock_key:
            return f"/api/v1/locks/{lock_key}"
        return "/api/v1/locks"


@pytest.fixture
def api(http_client: httpx.AsyncClient, test_tenant: str) -> APITestHelper:
    """Create API test helper."""
    return APITestHelper(http_client, test_tenant)


@pytest.fixture
def unique_bot_name(unique_id: str) -> str:
    """Generate unique bot name for test isolation."""
    return f"e2e-test-{unique_id}"


@pytest_asyncio.fixture
async def created_bot(
    api: APITestHelper,
    unique_bot_name: str,
    created_bot_uuids: list[str],
) -> dict[str, Any]:
    """Create and activate a test bot, with cleanup on teardown."""
    template_uuid = DEFAULT_TEMPLATE_UUID
    bot = await create_test_bot(api, unique_bot_name, template_uuid=template_uuid)
    created_bot_uuids.append(bot["bot_uuid"])
    await activate_test_bot(api, bot)
    yield bot
    await cleanup_bot(api, bot["bot_uuid"])


@pytest_asyncio.fixture
async def created_paas_device(
    api: APITestHelper,
    unique_id: str,
) -> dict[str, Any]:
    """Create a PaaS device via facade and return device info, with teardown cleanup."""
    from .conftest import create_paas_device

    return await create_paas_device(api, unique_id)


async def create_paas_device(
    api: APITestHelper,
    unique_id: str,
    template_uuid: str = DEFAULT_TEMPLATE_UUID,
    detail_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a PaaS device via POST /api/v1/paas/devices."""
    if detail_config is None:
        detail_config = {
            "name": f"e2e-device-{unique_id}",
            "ttl_in_minutes": 60,
        }
    response = await api.client.post(
        api.paas_device_url(),
        params=api.params(),
        json={
            "tenant_name": api.tenant,
            "device_template_uuid": template_uuid,
            "detail_config": detail_config,
        },
    )
    response.raise_for_status()
    return response.json()["data"]


async def destroy_paas_device(
    api: APITestHelper,
    paas_device_id: str,
) -> httpx.Response:
    """Destroy a PaaS device via DELETE /api/v1/paas/devices/{id}."""
    return await api.client.delete(
        api.paas_device_url(paas_device_id),
        params=api.params(),
    )


@pytest.fixture
def mock_paas_env() -> dict[str, str]:
    """Fixture for setting PAAS_MOCK_* env vars with automatic cleanup."""
    return {}


def set_mock_paas_failure(monkeypatch: Any, env_var: str, value: str = "1") -> None:
    """Set a PAAS_MOCK_* env var with cleanup."""
    monkeypatch.setenv(env_var, value)


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
