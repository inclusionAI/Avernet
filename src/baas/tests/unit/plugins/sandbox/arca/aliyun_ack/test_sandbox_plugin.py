"""Unit tests for AliyunAckSandboxPlugin / AliyunAckSandbox / AliyunAckClientManager."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.device_manage import ArcaCredentials
from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
    AliyunAckClientManager,
)
from secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox import AliyunAckSandbox
from secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox_plugin import (
    AliyunAckSandboxPlugin,
    _sanitize_pod_name,
)
from secbaas.community.spi.sandbox.arca import ArcaSandboxInfo

_SANDBOX = "secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox"
_PLUGIN = "secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox_plugin"
_MGR = "secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager"

TEMPLATE_ID = "ALIYUN_ACK_DEFAULT"


def _creds(arca_template_id: str = TEMPLATE_ID) -> ArcaCredentials:
    return ArcaCredentials(
        template_id=1,
        template_uuid="u",
        base_url="http://x",
        api_key="k",
        arca_template_id=arca_template_id,
    )


def _plugin(**kw) -> AliyunAckSandboxPlugin:
    kw.setdefault("config", _creds())
    kw.setdefault("api_server", "https://ack.example.com")
    kw.setdefault("token", "dummy-token")
    kw.setdefault("namespace", "sandbox-ns")
    kw.setdefault("arca_utils", MagicMock())
    return AliyunAckSandboxPlugin(**kw)


class _FakeOwnerRef:
    def __init__(
        self, kind: str = "ReplicaSet", name: str = "openclaw-ack-uid-abcd"
    ) -> None:
        self.kind = kind
        self.name = name


class _FakeContainer:
    def __init__(self, name: str = "openclaw") -> None:
        self.name = name


class _FakePodSpec:
    def __init__(self, containers: list | None = None) -> None:
        self.containers = containers if containers is not None else [_FakeContainer()]


class _FakePod:
    def __init__(
        self, phase: str = "Running", name: str = "openclaw-ack-test-pod"
    ) -> None:
        self.status = MagicMock()
        self.status.phase = phase
        self.metadata = MagicMock()
        self.metadata.name = name
        self.metadata.labels = {"avernet.arcasandbox/template": TEMPLATE_ID}
        self.metadata.owner_references = [_FakeOwnerRef()]
        self.spec = _FakePodSpec()


class _FakePodList:
    def __init__(self, pods: list | None = None) -> None:
        self.items = pods if pods is not None else [_FakePod()]


class _CoreHarness:
    """Stack the k8s CoreV1Api + client-manager patches; expose ``core``."""

    def __init__(self) -> None:
        self.core = MagicMock()
        self.core.read_namespaced_pod.return_value = _FakePod()
        self.core.list_namespaced_pod.return_value = _FakePodList()
        self.apps = MagicMock()
        self.client = MagicMock()
        self.create_from_yaml = MagicMock()
        self._stack: ExitStack | None = None

    def __enter__(self) -> _CoreHarness:
        self._stack = ExitStack()
        self._stack.enter_context(
            patch(f"{_PLUGIN}.CoreV1Api", MagicMock(return_value=self.core))
        )
        self._stack.enter_context(
            patch(f"{_SANDBOX}.CoreV1Api", MagicMock(return_value=self.core))
        )
        self._stack.enter_context(
            patch(f"{_PLUGIN}.create_from_yaml", self.create_from_yaml)
        )
        self._stack.enter_context(
            patch(f"{_SANDBOX}.AppsV1Api", MagicMock(return_value=self.apps))
        )
        self._stack.enter_context(
            patch.object(AliyunAckClientManager, "get_client", return_value=self.client)
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert self._stack is not None
        self._stack.__exit__(exc_type, exc, tb)
        return False


class TestCreateSyncSandbox:
    def test_create_success(self) -> None:
        with _CoreHarness() as h:
            sb = _plugin().create_sync_sandbox(
                template_id=TEMPLATE_ID, ready_timeout_in_seconds=1
            )
        assert isinstance(sb, AliyunAckSandbox)
        assert h.create_from_yaml.called
        assert sb._container_name == "openclaw"

    def test_create_timeout_cleans_up(self) -> None:
        with _CoreHarness() as h:
            h.core.list_namespaced_pod.return_value = _FakePodList(
                [_FakePod("Pending")]
            )
            with patch(f"{_PLUGIN}.time.sleep"):
                with pytest.raises(RuntimeError, match="did not become ready"):
                    _plugin().create_sync_sandbox(
                        template_id=TEMPLATE_ID, ready_timeout_in_seconds=1
                    )
            h.core.delete_namespaced_deployment.assert_called()


class TestConnect:
    def test_connect_success(self) -> None:
        with _CoreHarness() as h:
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
        assert isinstance(sb, AliyunAckSandbox)
        assert sb.sandbox_id == "aliyun-ack-abc123"

    def test_connect_not_found(self) -> None:
        with _CoreHarness() as h:
            h.core.list_namespaced_pod.return_value = _FakePodList([])
            with pytest.raises(RuntimeError, match="not found"):
                _plugin().connect_sync_sandbox("aliyun-ack-missing")


class TestConnectionInfo:
    def _utils(self):
        u = MagicMock()
        u._get_arca_target.return_value = "ARCA_ack:20003"
        u._get_proxypass_token.return_value = "token"
        u.build_proxypass_url.return_value = "wss://proxy/x"
        return u

    def test_resolve_ws_conn_info(self) -> None:
        info = _plugin(arca_utils=self._utils()).resolve_ws_conn_info(
            "ack-1", 20003, "/api/openclaw/ws"
        )
        assert info.ws_url.startswith("wss://")
        assert info.token == "token"

    def test_resolve_http_conn_info(self) -> None:
        u = self._utils()
        u.build_proxypass_url.return_value = "https://proxy/x"
        info = _plugin(arca_utils=u).resolve_http_connection_info("ack-1", 20003, "/")
        assert info.http_url.startswith("https://")
        assert info.token == "token"


class TestDeleteStorage:
    def test_delete_success(self) -> None:
        with _CoreHarness() as h:
            assert _plugin().delete_storage("pv-1", "tenant") is True
        h.core.delete_namespaced_persistent_volume_claim.assert_called()

    def test_delete_not_found_idempotent(self) -> None:
        class _ApiClientException(Exception):
            status = 404

        with _CoreHarness() as h:
            h.core.delete_namespaced_persistent_volume_claim.side_effect = (
                _ApiClientException()
            )
            with patch(f"{_PLUGIN}.ApiException", _ApiClientException):
                assert _plugin().delete_storage("pv-1", "tenant") is True

    def test_delete_error_false(self) -> None:
        class _ApiClientException(Exception):
            status = 500

        with _CoreHarness() as h:
            h.core.delete_namespaced_persistent_volume_claim.side_effect = (
                _ApiClientException()
            )
            with patch(f"{_PLUGIN}.ApiException", _ApiClientException):
                assert _plugin().delete_storage("pv-1", "tenant") is False


class TestSandbox:
    def test_get_info_returns_unified_model(self) -> None:
        with _CoreHarness() as h:
            sb = AliyunAckSandbox(
                "aliyun-ack-1", "ns", TEMPLATE_ID, MagicMock(), pod_name="aliyun-ack-1"
            )
            info = sb.get_info()
        assert isinstance(info, ArcaSandboxInfo)
        assert info.sandbox_id == "aliyun-ack-1"
        assert info.status == "Running"

    def test_destroy_idempotent(self) -> None:
        class _ApiClientException(Exception):
            status = 404

        with _CoreHarness() as h:
            h.apps.delete_namespaced_deployment.side_effect = _ApiClientException()
            with patch(f"{_SANDBOX}.ApiException", _ApiClientException):
                sb = AliyunAckSandbox(
                    "aliyun-ack-1",
                    "ns",
                    TEMPLATE_ID,
                    MagicMock(),
                    pod_name="openclaw-ack-uid-abcd",
                    deployment_name="openclaw-ack-uid",
                )
                assert sb.destroy() is True


class TestClientManager:
    def test_validate_missing_api_server(self) -> None:
        with pytest.raises(ValueError, match="api_server"):
            AliyunAckClientManager(cluster=None).validate()

    def test_get_client_lazy_builds(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
            AliyunAckClusterConfig,
        )

        cfg = AliyunAckClusterConfig("https://ack.example.com", "dummy-token", "default")
        mgr = AliyunAckClientManager(cfg)
        fake = MagicMock()
        mgr.build_client = MagicMock(return_value=fake)
        assert mgr.get_client() is fake
        mgr.build_client.assert_called_once()

    def test_close_noop(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
            AliyunAckClusterConfig,
        )

        cfg = AliyunAckClusterConfig("https://ack.example.com", "dummy-token", "default")
        AliyunAckClientManager(cfg).close()


class TestSanitizePodName:
    def test_sanitizes(self) -> None:
        assert _sanitize_pod_name("ALIYUN_ACK_abc-123") == "aliyun-ack-abc-123"
        assert _sanitize_pod_name("!!!") == "aliyun-ack-sandbox"
