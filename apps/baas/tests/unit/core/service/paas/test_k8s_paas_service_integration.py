"""Stub-driven integration tests for K8sPaasService lifecycle + _map_error + concurrency.

This file bridges the gap between the existing unit tests (test_k8s_paas_service.py)
and true integration-level testing. The existing unit tests test operations in
isolation but the stub plugin's auto-generated sandbox_id ("stub-K8S-{uuid}") never
matches K8sPaasService's derived statefulset_name, so connect_device() always returns
404 — making create-to-scale-to-destroy flow untestable in isolation.

Integration strategy: pre-register StubK8sSandbox instances in the plugin's _sandboxes
dict under the statefulset_name key, bypassing the auto-generated UUID gap. This
enables proper StatefulSet lifecycle testing with the stub plugin.
"""

from __future__ import annotations

import asyncio

import pytest

from secbaas.community.api.device_manage import (
    DeviceCreateConfig,
    ErrorCode,
    K8sCreationResult,
    K8sCredentials,
    K8sDeviceInfo,
    PaasError,
)
from secbaas.community.core.service.paas._k8s_paas_service import K8sPaasService
from secbaas.community.plugins.sandbox.k8s import StubK8sSandboxPlugin
from secbaas.community.plugins.sandbox.k8s.stub._stub_k8s_sandbox import StubK8sSandbox

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def k8s_credentials():
    """Return a K8sCredentials instance with test values."""
    return K8sCredentials(
        template_id=1,
        template_uuid="tpl-test-001",
        namespace="test-ns",
        image="test-image:latest",
        cpu_request="500m",
        cpu_limit="1",
        memory_request="512Mi",
        memory_limit="1Gi",
    )


@pytest.fixture
def stub_plugin():
    """Return a fresh StubK8sSandboxPlugin instance (no shared state across tests)."""
    return StubK8sSandboxPlugin()


@pytest.fixture
def stub_service(stub_plugin, k8s_credentials):
    """Return K8sPaasService backed by stub plugin (no pre-registration).

    Since no sandbox is pre-registered, connect_device() returns 404 and
    create_device enters the lazy-create path.
    """
    return K8sPaasService(plugin=stub_plugin, credentials=k8s_credentials)


@pytest.fixture
def stub_service_with_prereg(stub_plugin, k8s_credentials):
    """Return K8sPaasService with a pre-registered StubK8sSandbox.

    Pre-registers a StubK8sSandbox with sandbox_id="test-bot-0" (the Pod-0
    name derived from statefulset_name="test-bot") so that connect_device()
    succeeds. This enables scale-up and lifecycle testing that the plain
    stub_service fixture cannot support.
    """
    sandbox = StubK8sSandbox(
        sandbox_id="test-bot-0",
        namespace=k8s_credentials.namespace or "default",
        pod_ip="10.244.0.1",
    )
    stub_plugin._sandboxes["test-bot-0"] = sandbox
    return K8sPaasService(plugin=stub_plugin, credentials=k8s_credentials)


# ---------------------------------------------------------------------------
# TestMapError — direct _map_error() HTTP status mapping verification
# ---------------------------------------------------------------------------


class TestMapError:
    """Verify _map_error() translates RuntimeError HTTP status codes to ErrorCode.

    K8sPaasService._map_error() parses "(XXX)" patterns from RuntimeError messages
    to derive HTTP status codes. Each status code maps to a specific ErrorCode
    per the D-07 mapping table. These tests verify every branch.
    """

    @pytest.mark.asyncio
    async def test_map_error_404(self, stub_service):
        """RuntimeError with (404) maps to DEVICE_NOT_FOUND."""
        mapped = stub_service._map_error(
            RuntimeError("(404) Deployment not found"),
            ErrorCode.DEVICE_CREATION_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.DEVICE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_map_error_409(self, stub_service):
        """RuntimeError with (409) maps to DEVICE_UNAVAILABLE."""
        mapped = stub_service._map_error(
            RuntimeError("(409) Conflict"),
            ErrorCode.DEVICE_DESTROY_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.DEVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_map_error_422(self, stub_service):
        """RuntimeError with (422) maps to CONFIG_INVALID."""
        mapped = stub_service._map_error(
            RuntimeError("(422) Unprocessable"),
            ErrorCode.DEVICE_CREATION_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.CONFIG_INVALID

    @pytest.mark.asyncio
    async def test_map_error_429(self, stub_service):
        """RuntimeError with (429) maps to RATE_LIMITED."""
        mapped = stub_service._map_error(
            RuntimeError("(429) Too Many Requests"),
            ErrorCode.DEVICE_CREATION_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.RATE_LIMITED

    @pytest.mark.asyncio
    async def test_map_error_503(self, stub_service):
        """RuntimeError with (503) maps to PLATFORM_UNAVAILABLE."""
        mapped = stub_service._map_error(
            RuntimeError("(503) Service Unavailable"),
            ErrorCode.COMMAND_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.PLATFORM_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_map_error_500(self, stub_service):
        """RuntimeError with (500) maps to PLATFORM_UNAVAILABLE."""
        mapped = stub_service._map_error(
            RuntimeError("(500) Internal Server Error"),
            ErrorCode.COMMAND_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.PLATFORM_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_map_error_502(self, stub_service):
        """RuntimeError with (502) maps to PLATFORM_UNAVAILABLE."""
        mapped = stub_service._map_error(
            RuntimeError("(502) Bad Gateway"),
            ErrorCode.COMMAND_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.PLATFORM_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_map_error_no_http_code(self, stub_service):
        """RuntimeError without (XXX) falls back to default_code."""
        mapped = stub_service._map_error(
            RuntimeError("Connection refused — no code"),
            ErrorCode.COMMAND_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.COMMAND_FAILED

    @pytest.mark.asyncio
    async def test_map_error_other_4xx(self, stub_service):
        """RuntimeError with (400) maps to CONFIG_INVALID (other 4xx)."""
        mapped = stub_service._map_error(
            RuntimeError("(400) Bad Request"),
            ErrorCode.DEVICE_CREATION_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.CONFIG_INVALID

    @pytest.mark.asyncio
    async def test_map_error_other_5xx(self, stub_service):
        """RuntimeError with (504) maps to PLATFORM_ERROR (other 5xx)."""
        mapped = stub_service._map_error(
            RuntimeError("(504) Gateway Timeout"),
            ErrorCode.DEVICE_CREATION_FAILED,
        )
        assert isinstance(mapped, PaasError)
        assert mapped.code == ErrorCode.PLATFORM_ERROR


# ---------------------------------------------------------------------------
# TestK8sPaasServiceLifecycle — StatefulSet lifecycle integration tests
# ---------------------------------------------------------------------------


class TestK8sPaasServiceLifecycle:
    """Verify K8sPaasService StatefulSet lifecycle with stub pre-registration.

    Tests lazy-create (no pre-registration), scale-up (pre-registered),
    scale-down, delete-at-replicas-1, idempotent 404 destroy, invalid ID
    handling, and get_device_info.
    """

    @pytest.mark.asyncio
    async def test_lazy_create_first_device(self, stub_service):
        """First create_device with no pre-registration triggers lazy-create.

        The stub returns 404 on connect_device (no pre-reg'd sandbox), so
        K8sPaasService enters the lazy-create path and creates a new sandbox.
        """
        config = DeviceCreateConfig(name="lifecycle-bot")
        result = await stub_service.create_device(config)
        assert isinstance(result, K8sCreationResult)
        assert result.platform == "k8s"
        assert result.device_id.endswith("--0")
        assert result.status == "CREATED"

    @pytest.mark.asyncio
    async def test_scale_up_second_device(self, stub_service_with_prereg):
        """Second create_device with pre-registered sandbox scales replicas up.

        The pre-registered StubK8sSandbox (sandbox_id="test-bot") allows
        connect_device to succeed. The first create_device on this service
        enters the scale-up path (current_replicas defaults to 1, scales to 2).
        The second call scales further to 3. We verify both return distinct
        device_ids with different ordinals.
        """
        config = DeviceCreateConfig(name="test-bot")
        result1 = await stub_service_with_prereg.create_device(config)
        result2 = await stub_service_with_prereg.create_device(config)
        assert isinstance(result1, K8sCreationResult)
        assert isinstance(result2, K8sCreationResult)
        # Different ordinals prove scaling worked
        ordinal1 = int(result1.device_id.rsplit("--", 1)[-1])
        ordinal2 = int(result2.device_id.rsplit("--", 1)[-1])
        assert ordinal1 != ordinal2

    @pytest.mark.asyncio
    async def test_destroy_device_scales_down(self, stub_service_with_prereg):
        """destroy_device at replicas>1 scales down (does not delete).

        First create two devices on the pre-reg'd service to push replicas to 3
        (default 1 -> scale to 2 -> scale to 3). Then destroy one — replicas
        goes from 3 to 2 (scale-down, not delete).
        """
        config = DeviceCreateConfig(name="test-bot")
        # First call: replicas 1->2, ordinal 1
        result1 = await stub_service_with_prereg.create_device(config)
        # Second call: replicas 2->3, ordinal 2
        result2 = await stub_service_with_prereg.create_device(config)
        # Destroy one device — should scale down (replicas 3->2)
        destroyed = await stub_service_with_prereg.destroy_device(result1.device_id)
        assert destroyed is True

    @pytest.mark.asyncio
    async def test_destroy_device_deletes_at_replicas_1(self, stub_service_with_prereg):
        """destroy_device at replicas=1 deletes the StatefulSet.

        Create one device on the pre-reg'd service (scale-up from 1 to 2).
        Then destroy both devices — the last one should delete the StatefulSet.
        """
        config = DeviceCreateConfig(name="test-bot")
        result = await stub_service_with_prereg.create_device(config)
        # Now replicas should be 2. Destroy one -> scale down to 1.
        await stub_service_with_prereg.destroy_device(result.device_id)
        # Create another device (replicas 1->2, ordinal 1)
        result2 = await stub_service_with_prereg.create_device(config)
        # Now replicas=2 again. Destroy both to reach replicas=0.
        destroyed1 = await stub_service_with_prereg.destroy_device(result.device_id)
        assert destroyed1 is True
        destroyed2 = await stub_service_with_prereg.destroy_device(result2.device_id)
        # Last destroy at replicas=1 triggers StatefulSet deletion
        assert destroyed2 is True

    @pytest.mark.asyncio
    async def test_destroy_device_idempotent_404(self, stub_service):
        """destroy_device on nonexistent StatefulSet returns True (idempotent)."""
        result = await stub_service.destroy_device("nonexistent--0")
        assert result is True

    @pytest.mark.asyncio
    async def test_destroy_device_invalid_id(self, stub_service):
        """destroy_device with malformed ID (no -- separator) raises CONFIG_INVALID."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.destroy_device("no-separator")
        assert exc_info.value.code == ErrorCode.CONFIG_INVALID

    @pytest.mark.asyncio
    async def test_get_device_info(self, stub_service_with_prereg):
        """get_device_info with pre-registered sandbox returns K8sDeviceInfo."""
        device_info = await stub_service_with_prereg.get_device_info("test-bot--0")
        assert isinstance(device_info, K8sDeviceInfo)
        assert device_info.platform is not None


# ---------------------------------------------------------------------------
# TestAsyncLockConcurrency — per-StatefulSet asyncio.Lock serialization
# ---------------------------------------------------------------------------


class TestAsyncLockConcurrency:
    """Verify per-StatefulSet asyncio.Lock prevents concurrent create_device races.

    Without the Lock, two concurrent create_device calls would both read
    replicas=1 and both attempt to scale to 2 — producing a lost update.
    The Lock serializes read-check-write cycles so ordinals are sequential.
    """

    @pytest.mark.asyncio
    async def test_concurrent_create_device_serialized(self, stub_service_with_prereg):
        """Two concurrent create_device calls produce sequential ordinals.

        Fired via asyncio.gather() on a pre-registered service. The per-StatefulSet
        asyncio.Lock must serialize both calls so the second reads the updated
        replicas from the first — producing ordinals 1 and 2 (not two 1s).
        """
        config = DeviceCreateConfig(name="test-bot")

        async def create():
            return await stub_service_with_prereg.create_device(config)

        results = await asyncio.gather(create(), create())
        assert len(results) == 2

        ordinals = sorted(int(r.device_id.rsplit("--", 1)[-1]) for r in results)
        assert ordinals == [1, 2], (
            f"Expected sequential ordinals [1, 2] but got {ordinals} — "
            f"asyncio.Lock may not be serializing create_device calls"
        )
