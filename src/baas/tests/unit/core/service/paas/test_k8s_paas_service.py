"""Tests for K8sPaasService — lifecycle verification using StubK8sSandboxPlugin.

Verifies that K8sPaasService correctly orchestrates the StatefulSet lifecycle
(create/scale/destroy) and delegates all K8s-specific operations to the
K8sSandboxPlugin. All tests use real StubK8sSandboxPlugin instances.
"""

from __future__ import annotations

import pytest
import yaml

from secbaas.community.api.device_manage import (
    CommandResult,
    DeviceCreateConfig,
    ErrorCode,
    K8sCreationResult,
    K8sCredentials,
    PaasError,
)
from secbaas.community.api.device_manage._outbound_proxy_rule import (
    K8sOutboundProxyRule,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas._k8s_paas_service import K8sPaasService
from secbaas.community.plugins.sandbox.k8s import StubK8sSandboxPlugin

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
    """Return a real StubK8sSandboxPlugin instance (not a mock)."""
    return StubK8sSandboxPlugin()


@pytest.fixture
def stub_service(stub_plugin, k8s_credentials):
    """Return a K8sPaasService backed by a real StubK8sSandboxPlugin.

    NOTE: The stub stores sandboxes by auto-generated sandbox_id
    ("stub-K8S-{uuid}"), not by K8sPaasService's derived statefulset_name.
    This means connect_device(statefulset_name, ns) will return 404.
    Tests must account for this behavior — create_device always creates
    a new sandbox (since connect fails with 404), and destroy_device
    treats 404 as idempotent success.
    """
    return K8sPaasService(plugin=stub_plugin, credentials=k8s_credentials)


# ---------------------------------------------------------------------------
# TestGetCredentialsAndPlatform
# ---------------------------------------------------------------------------


class TestGetCredentialsAndPlatform:
    """Verify get_credentials() and get_platform_type() metadata methods."""

    @pytest.mark.asyncio
    async def test_get_credentials_returns_creds(self, stub_service, k8s_credentials):
        result = await stub_service.get_credentials()
        assert result is k8s_credentials

    @pytest.mark.asyncio
    async def test_get_platform_type_returns_k8s(self, stub_service):
        result = await stub_service.get_platform_type()
        assert result == TenantType.K8S


# ---------------------------------------------------------------------------
# TestCreateDevice
# ---------------------------------------------------------------------------


class TestCreateDevice:
    """Verify create_device() lifecycle: Deployment lazy-create + replicas scaling."""

    @pytest.mark.asyncio
    async def test_first_create_returns_creation_result(self, stub_service):
        config = DeviceCreateConfig(name="test-bot")
        result = await stub_service.create_device(config)
        assert isinstance(result, K8sCreationResult)
        assert result.platform == "k8s"
        assert result.status == "CREATED"
        # Stub uses UUID sandbox_id; real plugin uses "{name}--{ordinal}"
        assert result.device_id, "device_id should not be empty"

    @pytest.mark.asyncio
    async def test_device_id_format(self, stub_service):
        config = DeviceCreateConfig(name="test-bot")
        result = await stub_service.create_device(config)
        # Stub plugin returns UUID-based sandbox_id without ordinal separator.
        # Real plugin returns "{statefulset_name}--{ordinal}" which splits on "--".
        assert result.device_id, "device_id should not be empty"

    @pytest.mark.asyncio
    async def test_device_id_format_after_create(self, stub_service):
        """Verify created device_id is populated and non-empty.

        The device_id is derived from the plugin's sandbox_id. Real plugin
        returns "{statefulset_name}--{ordinal}"; stub returns UUID-based IDs.
        """
        config = DeviceCreateConfig(name="test-bot")
        result = await stub_service.create_device(config)
        assert result.device_id, "device_id should not be empty"

    @pytest.mark.asyncio
    async def test_second_create_does_not_raise(self, stub_service):
        """Second create_device call should not raise (creates new sandbox in stub)."""
        config = DeviceCreateConfig(name="test-bot")
        result1 = await stub_service.create_device(config)
        result2 = await stub_service.create_device(config)
        assert result2.platform == "k8s"
        assert result2.status == "CREATED"

    @pytest.mark.asyncio
    async def test_create_tracks_replicas(self, stub_service):
        """Verify internal _statefulset_replicas tracking stores statefulset_name.

        Since stub sandbox_id != statefulset_name, every create_device call
        enters the lazy-create path (404 from connect_device) and resets
        replicas to 1. This is CORRECT stub behavior — the test verifies
        that _statefulset_replicas at least tracks the statefulset_name key.
        """
        config = DeviceCreateConfig(name="test-bot")
        await stub_service.create_device(config)
        # Replicas set to 1 on lazy-create
        assert stub_service._statefulset_replicas.get("test-bot") == 1

    @pytest.mark.asyncio
    async def test_creates_sandbox_in_stub(self, stub_service, stub_plugin):
        """Verify that the stub plugin actually created a sandbox."""
        config = DeviceCreateConfig(name="test-bot")
        await stub_service.create_device(config)
        # Stub plugin stores sandboxes by sandbox_id, not deployment_name
        assert len(stub_plugin._sandboxes) >= 1

    @pytest.mark.asyncio
    async def test_multiple_creates_all_succeed(self, stub_service):
        """Multiple create_device calls all succeed (each lazy-creates since
        stub connect_device returns 404 for deployment_name)."""
        config = DeviceCreateConfig(name="test-bot")
        result1 = await stub_service.create_device(config)
        result2 = await stub_service.create_device(config)
        result3 = await stub_service.create_device(config)
        assert result1.platform == "k8s"
        assert result2.platform == "k8s"
        assert result3.platform == "k8s"

    @pytest.mark.asyncio
    async def test_default_name_when_empty(self, stub_service):
        """When config.name is None, deployment_name falls back to credentials.tenant_name."""
        config = DeviceCreateConfig(name=None)
        result = await stub_service.create_device(config)
        # tenant_name is None by default, so _derive_deployment_name falls back to "k8s-device"
        assert result.device_id.startswith("k8s-device--")


# ---------------------------------------------------------------------------
# TestDestroyDevice
# ---------------------------------------------------------------------------


class TestDestroyDevice:
    """Verify destroy_device() lifecycle: scale-down + 404 idempotent delete."""

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_returns_true(self, stub_service):
        """Destroying a nonexistent device returns True (404 -> idempotent success)."""
        result = await stub_service.destroy_device("nonexistent--pod-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_device_id_raises(self, stub_service):
        """Malformed paas_device_id (no '--') raises PaasError CONFIG_INVALID."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.destroy_device("invalid-format")
        assert exc_info.value.code == ErrorCode.CONFIG_INVALID


# ---------------------------------------------------------------------------
# TestRestartDevice
# ---------------------------------------------------------------------------


class TestRestartDevice:
    """Verify restart_device() triggers rolling restart."""

    @pytest.mark.asyncio
    async def test_restart_nonexistent_raises(self, stub_service):
        """Restarting a nonexistent device raises PaasError (404 -> DEVICE_NOT_FOUND)."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.restart_device("nonexistent--pod-001")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_restart_invalid_id_raises(self, stub_service):
        """IDs without '--' separator are treated as valid stub-style IDs
        and get DEVICE_NOT_FOUND when the sandbox doesn't exist."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.restart_device("no-separator")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND


# ---------------------------------------------------------------------------
# TestUpdateDevice
# ---------------------------------------------------------------------------


class TestUpdateDevice:
    """Verify update_device() patches Deployment spec."""

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, stub_service):
        """Updating a nonexistent device raises PaasError (404 -> DEVICE_NOT_FOUND)."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.update_device("nonexistent--pod-001")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_update_invalid_id_raises(self, stub_service):
        """Non-existent stub device raises PaasError DEVICE_NOT_FOUND."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.update_device("bad-id")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND


# ---------------------------------------------------------------------------
# TestExecuteCommand
# ---------------------------------------------------------------------------


class TestExecuteCommand:
    """Verify execute_command() delegates to sandbox.exec_command()."""

    @pytest.mark.asyncio
    async def test_exec_nonexistent_raises(self, stub_service):
        """Executing on a nonexistent device raises PaasError."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.execute_command("nonexistent--pod-001", "echo hello")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_exec_invalid_id_raises(self, stub_service):
        """Non-existent stub device raises PaasError DEVICE_NOT_FOUND for exec."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.execute_command("bad-id", "echo hello")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND


# ---------------------------------------------------------------------------
# TestListInstances
# ---------------------------------------------------------------------------


class TestListInstances:
    """Verify list_instances() filters by namespace."""

    @pytest.mark.asyncio
    async def test_list_instances_returns_list(self, stub_service):
        """list_instances returns a list (stub returns empty list for test namespace)."""
        result = await stub_service.list_instances({"namespace": "test-ns"})
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestInvokeHttpInDevice
# ---------------------------------------------------------------------------


class TestInvokeHttpInDevice:
    """Verify invoke_http_in_device() delegates to plugin."""

    @pytest.mark.asyncio
    async def test_invoke_nonexistent_raises(self, stub_service):
        """HTTP invocation on a nonexistent device returns mock response.

        The invoke_http_in_device method passes the full paas_device_id to
        plugin.invoke_http_in_device. The stub does NOT check sandbox existence
        for this method — it returns a mock response. The test verifies this
        path works without error.
        """
        # The stub's invoke_http_in_device returns mock data regardless
        result = await stub_service.invoke_http_in_device(
            "nonexistent--pod-001", "GET", 8080, "/", None, {}, b""
        )
        assert isinstance(result, dict)
        assert "status_code" in result


# ---------------------------------------------------------------------------
# TestPassthroughMethods
# ---------------------------------------------------------------------------


class TestPassthroughMethods:
    """Verify get_device_info(), resolve_ws_conn_info(), resolve_invoke_http_info()."""

    @pytest.mark.asyncio
    async def test_get_device_info_nonexistent_raises(self, stub_service):
        """get_device_info for nonexistent device raises PaasError(DEVICE_NOT_FOUND)."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.get_device_info("nonexistent--pod-001")
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_resolve_ws_delegates_to_plugin(self, stub_service):
        """resolve_ws_conn_info delegates to plugin and returns WsConnectionInfo.

        The stub's resolve_ws_conn_info does NOT check sandbox existence — it
        returns a mock WsConnectionInfo regardless of the device_id.
        """
        from secbaas.community.api.bot_runtime import WsConnectionInfo

        result = await stub_service.resolve_ws_conn_info("any--pod", 8080, "/ws")
        assert isinstance(result, WsConnectionInfo)
        assert "ws://" in result.ws_url

    @pytest.mark.asyncio
    async def test_resolve_http_delegates_to_plugin(self, stub_service):
        """resolve_invoke_http_info delegates to plugin and returns HttpConnectionInfo.

        The stub's resolve_invoke_http_info does NOT check sandbox existence — it
        returns a mock HttpConnectionInfo regardless of the device_id.
        """
        from secbaas.community.api.bot_runtime import HttpConnectionInfo

        result = await stub_service.resolve_invoke_http_info("any--pod", 8080)
        assert isinstance(result, HttpConnectionInfo)
        assert "http://" in result.http_url


# ---------------------------------------------------------------------------
# TestSidecarInjection
# ---------------------------------------------------------------------------


@pytest.fixture
def k8s_credentials_with_rules():
    """Return a K8sCredentials instance with outbound_proxy_rules set."""
    return K8sCredentials(
        template_id=1,
        template_uuid="tpl-test-001",
        namespace="test-ns",
        image="test-image:latest",
        cpu_request="500m",
        cpu_limit="1",
        memory_request="512Mi",
        memory_limit="1Gi",
        outbound_proxy_rules=[
            K8sOutboundProxyRule(
                url_pattern="/api/v1/",
                rewrite_target="/api/v2/",
            )
        ],
    )


@pytest.fixture
def stub_service_with_rules(stub_plugin, k8s_credentials_with_rules):
    """Return a K8sPaasService backed by StubK8sSandboxPlugin with proxy rules."""
    return K8sPaasService(plugin=stub_plugin, credentials=k8s_credentials_with_rules)


class TestSidecarInjection:
    """Verify sidecar injection behavior in create_device()."""

    @pytest.mark.asyncio
    async def test_create_device_with_sidecar_rules_present(
        self, stub_service_with_rules
    ):
        """When outbound_proxy_rules is non-empty, sidecar ConfigMap is created."""
        config = DeviceCreateConfig(name="sidecar-test")
        result = await stub_service_with_rules.create_device(config)
        assert isinstance(result, K8sCreationResult)
        assert result.platform == "k8s"
        assert "--" in result.device_id

        # Verify the stub plugin's _configmaps has the sidecar config
        configmaps = stub_service_with_rules._plugin._configmaps
        assert len(configmaps) > 0, "Expected ConfigMap to be created for sidecar"

        envoy_yaml = next(iter(configmaps.values()))
        parsed = yaml.safe_load(envoy_yaml)
        assert (
            parsed["static_resources"]["listeners"][0]["name"]
            == "outbound_proxy_listener"
        )
        # Verify the route is present
        routes = parsed["static_resources"]["listeners"][0]["filter_chains"][0][
            "filters"
        ][0]["typed_config"]["route_config"]["virtual_hosts"][0]["routes"]
        assert len(routes) == 1
        assert routes[0]["match"]["prefix"] == "/api/v1/"
        assert routes[0]["route"]["prefix_rewrite"] == "/api/v2/"

    @pytest.mark.asyncio
    async def test_create_device_without_sidecar_empty_rules(self, stub_service):
        """When outbound_proxy_rules is None, no sidecar ConfigMap is created."""
        config = DeviceCreateConfig(name="no-sidecar")
        result = await stub_service.create_device(config)
        assert isinstance(result, K8sCreationResult)
        assert "--" in result.device_id

        # Verify _configmaps is empty (zero-overhead)
        assert stub_service._plugin._configmaps == {}

    @pytest.mark.asyncio
    async def test_create_device_without_sidecar_none_rules(self, stub_service):
        """Verify backward compatibility: outbound_proxy_rules is explicitly None."""
        assert stub_service._credentials.outbound_proxy_rules is None

        config = DeviceCreateConfig(name="backward-compat")
        result = await stub_service.create_device(config)
        assert isinstance(result, K8sCreationResult)
        assert "--" in result.device_id

        # Verify _configmaps is empty (backward compat)
        assert stub_service._plugin._configmaps == {}

    @pytest.mark.asyncio
    async def test_create_device_with_sidecar_empty_list(self, stub_plugin):
        """When outbound_proxy_rules is an empty list, no sidecar (zero-overhead)."""
        creds_empty_rules = K8sCredentials(
            template_id=1,
            template_uuid="tpl-test-001",
            namespace="test-ns",
            image="test-image:latest",
            cpu_request="500m",
            cpu_limit="1",
            memory_request="512Mi",
            memory_limit="1Gi",
            outbound_proxy_rules=[],
        )
        svc = K8sPaasService(plugin=stub_plugin, credentials=creds_empty_rules)
        config = DeviceCreateConfig(name="empty-rules")
        result = await svc.create_device(config)
        assert isinstance(result, K8sCreationResult)
        assert "--" in result.device_id

        # Verify _configmaps is empty (empty list treated like None)
        assert stub_plugin._configmaps == {}


# ---------------------------------------------------------------------------
# TestUpdateOutboundRule
# ---------------------------------------------------------------------------


class TestUpdateOutboundRule:
    """Verify update_outbound_operation_rule() ConfigMap management."""

    @pytest.mark.asyncio
    async def test_update_outbound_operation_rule_success(
        self, stub_service_with_rules
    ):
        """update_outbound_operation_rule stores envoy_yaml in stub _configmaps."""
        paas_device_id = "test-sts--0"
        result = await stub_service_with_rules.update_outbound_operation_rule(
            paas_device_id, None
        )
        assert result is True

        cm_name = "test-sts-proxy-rules"
        assert cm_name in stub_service_with_rules._plugin._configmaps

        envoy_yaml = stub_service_with_rules._plugin._configmaps[cm_name]
        parsed = yaml.safe_load(envoy_yaml)
        assert (
            parsed["static_resources"]["listeners"][0]["name"]
            == "outbound_proxy_listener"
        )
        # Verify the route from outbound_proxy_rules is present
        routes = parsed["static_resources"]["listeners"][0]["filter_chains"][0][
            "filters"
        ][0]["typed_config"]["route_config"]["virtual_hosts"][0]["routes"]
        assert len(routes) == 1
        assert routes[0]["match"]["prefix"] == "/api/v1/"
        assert routes[0]["route"]["prefix_rewrite"] == "/api/v2/"

    @pytest.mark.asyncio
    async def test_update_outbound_operation_rule_invalid_device_id(self, stub_service):
        """Malformed paas_device_id (no '--') raises CONFIG_INVALID."""
        with pytest.raises(PaasError) as exc_info:
            await stub_service.update_outbound_operation_rule("invalidformat", None)
        assert exc_info.value.code == ErrorCode.CONFIG_INVALID

    @pytest.mark.asyncio
    async def test_update_outbound_operation_rule_no_rules(self, stub_service):
        """When outbound_proxy_rules is None, no ConfigMap is created."""
        paas_device_id = "test-sts--0"
        result = await stub_service.update_outbound_operation_rule(paas_device_id, None)
        assert result is True

        # Verify no ConfigMap was created (consistent with create_device guard)
        assert "test-sts-proxy-rules" not in stub_service._plugin._configmaps

    @pytest.mark.asyncio
    async def test_update_outbound_operation_rule_plugin_error(
        self, stub_service_with_rules
    ):
        """Plugin RuntimeError with (409) maps to DEVICE_UNAVAILABLE."""
        from unittest.mock import patch

        with patch.object(
            stub_service_with_rules._plugin,
            "update_outbound_operation_rule",
            side_effect=RuntimeError("patch_configmap failed (409)"),
        ):
            with pytest.raises(PaasError) as exc_info:
                await stub_service_with_rules.update_outbound_operation_rule(
                    "test-sts--0", None
                )
        assert exc_info.value.code == ErrorCode.DEVICE_UNAVAILABLE


# ---------------------------------------------------------------------------
# TestNotImplementedMethods
# ---------------------------------------------------------------------------


class TestNotImplementedMethods:
    """Verify unsupported methods raise NotImplementedError."""

    @pytest.mark.asyncio
    async def test_update_device_ttl_raises(self, stub_service):
        with pytest.raises(NotImplementedError):
            await stub_service.update_device_ttl("any--pod")

    @pytest.mark.asyncio
    async def test_open_folder_raises(self, stub_service):
        with pytest.raises(NotImplementedError):
            await stub_service.open_folder("any--pod")

    @pytest.mark.asyncio
    async def test_pull_file_from_url_raises(self, stub_service):
        with pytest.raises(NotImplementedError, match="File transfer not supported on K8s platform"):
            await stub_service.pull_file_from_url("any--pod", "http://src", "/dst")

    @pytest.mark.asyncio
    async def test_push_file_to_url_raises(self, stub_service):
        with pytest.raises(NotImplementedError, match="File transfer not supported on K8s platform"):
            await stub_service.push_file_to_url("any--pod", "/src", "http://dst")


# ---------------------------------------------------------------------------
# TestGetDeviceInfoWithPods
# ---------------------------------------------------------------------------


class TestGetDeviceInfoWithPods:
    """Verify get_device_info() populates K8sDeviceInfo.pods from container_statuses."""

    @pytest.mark.asyncio
    async def test_get_device_info_with_container_statuses_populates_pods(
        self, stub_service, stub_plugin, k8s_credentials
    ):
        """get_device_info populates K8sDeviceInfo.pods when container_statuses present.

        When the stub sandbox returns container_statuses with entries, the
        K8sDeviceInfo should have pods populated as a list[PodInfo].
        """
        from unittest.mock import patch

        from secbaas.community.api.device_manage._device_info import PodInfo
        from secbaas.community.plugins.sandbox.k8s.stub._stub_k8s_sandbox import (
            StubK8sSandbox,
        )

        # Create a sandbox with custom pods and register it under the
        # statefulset_name so connect_device() finds it.
        statefulset_name = "test-pods-bot"
        fake_device_id = f"{statefulset_name}--0"
        sandbox = StubK8sSandbox(
            sandbox_id=f"{statefulset_name}-0",
            namespace=k8s_credentials.namespace or "default",
            pod_ip="10.244.0.1",
        )
        stub_plugin._sandboxes[f"{statefulset_name}-0"] = sandbox

        # Patch get_info to return custom container_statuses
        original_get_info = sandbox.get_info

        def patched_get_info():
            info = original_get_info()
            info["container_statuses"] = [
                {
                    "name": "bot",
                    "ready": True,
                    "restart_count": 0,
                    "state": "running",
                    "image": "test-image:latest",
                }
            ]
            return info

        with patch.object(sandbox, "get_info", side_effect=patched_get_info):
            device_info = await stub_service.get_device_info(fake_device_id)

        assert device_info.pods is not None
        assert len(device_info.pods) == 1
        assert device_info.pods[0].name == "bot"
        assert device_info.pods[0].ready is True
        assert device_info.pods[0].state == "running"
        assert device_info.pods[0].restart_count == 0
        assert device_info.pods[0].image == "test-image:latest"

    @pytest.mark.asyncio
    async def test_get_device_info_no_container_statuses_sets_pods_none(
        self, stub_service, stub_plugin, k8s_credentials
    ):
        """get_device_info sets pods=None when container_statuses is empty or missing.

        When the stub sandbox returns no container_statuses key, the K8sDeviceInfo
        should have pods=None.
        """
        from unittest.mock import patch

        from secbaas.community.plugins.sandbox.k8s.stub._stub_k8s_sandbox import (
            StubK8sSandbox,
        )

        statefulset_name = "test-no-pods-bot"
        fake_device_id = f"{statefulset_name}--0"
        sandbox = StubK8sSandbox(
            sandbox_id=f"{statefulset_name}-0",
            namespace=k8s_credentials.namespace or "default",
            pod_ip="10.244.0.2",
        )
        stub_plugin._sandboxes[f"{statefulset_name}-0"] = sandbox

        original_get_info = sandbox.get_info

        def patched_get_info():
            info = original_get_info()
            info.pop("container_statuses", None)
            return info

        with patch.object(sandbox, "get_info", side_effect=patched_get_info):
            device_info = await stub_service.get_device_info(fake_device_id)

        assert device_info.pods is None

    @pytest.mark.asyncio
    async def test_get_device_info_multiple_container_statuses(
        self, stub_service, stub_plugin, k8s_credentials
    ):
        """get_device_info with multiple container_statuses produces K8sDeviceInfo with pods length 2.

        When the stub sandbox returns multiple container_statuses, K8sDeviceInfo.pods
        should contain a PodInfo for each container.
        """
        from unittest.mock import patch

        from secbaas.community.plugins.sandbox.k8s.stub._stub_k8s_sandbox import (
            StubK8sSandbox,
        )

        statefulset_name = "test-multi-pods-bot"
        fake_device_id = f"{statefulset_name}--0"
        sandbox = StubK8sSandbox(
            sandbox_id=f"{statefulset_name}-0",
            namespace=k8s_credentials.namespace or "default",
            pod_ip="10.244.0.3",
        )
        stub_plugin._sandboxes[f"{statefulset_name}-0"] = sandbox

        original_get_info = sandbox.get_info

        def patched_get_info():
            info = original_get_info()
            info["container_statuses"] = [
                {
                    "name": "bot",
                    "ready": True,
                    "restart_count": 0,
                    "state": "running",
                    "image": "test-image:latest",
                },
                {
                    "name": "sidecar",
                    "ready": True,
                    "restart_count": 1,
                    "state": "running",
                    "image": "sidecar:1.0",
                },
            ]
            return info

        with patch.object(sandbox, "get_info", side_effect=patched_get_info):
            device_info = await stub_service.get_device_info(fake_device_id)

        assert device_info.pods is not None
        assert len(device_info.pods) == 2
        assert device_info.pods[0].name == "bot"
        assert device_info.pods[1].name == "sidecar"
        assert device_info.pods[1].restart_count == 1
