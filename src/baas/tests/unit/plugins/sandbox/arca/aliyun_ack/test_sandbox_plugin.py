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
        app_name="sandbox-ns",
    )


def _plugin(**kw) -> AliyunAckSandboxPlugin:
    kw.setdefault("config", _creds())
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
        self,
        phase: str = "Running",
        name: str = "openclaw-ack-test-pod",
        pod_ip: str | None = "10.0.0.7",
    ) -> None:
        self.status = MagicMock()
        self.status.phase = phase
        self.status.pod_ip = pod_ip
        self.metadata = MagicMock()
        self.metadata.name = name
        self.metadata.labels = {"avernet.arcasandbox/template": TEMPLATE_ID}
        self.metadata.owner_references = [_FakeOwnerRef()]
        self.metadata.creation_timestamp = None
        self.metadata.annotations = {}
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
        assert sb._container_name == "avernet-agent"

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

    def test_connect_reads_ttl_annotation(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.annotations = {"avernet.arcasandbox/ttl-minutes": "10080"}
            h.core.list_namespaced_pod.return_value = _FakePodList([pod])
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
        assert sb._ttl_in_minutes == 10080.0

    def test_connect_ignores_invalid_ttl_annotation(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.annotations = {"avernet.arcasandbox/ttl-minutes": "bad"}
            h.core.list_namespaced_pod.return_value = _FakePodList([pod])
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
        assert sb._ttl_in_minutes is None

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

    def test_get_info_reports_ip_addr(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod(pod_ip="10.0.0.7")
            h.core.read_namespaced_pod.return_value = pod
            sb = AliyunAckSandbox(
                "aliyun-ack-1", "ns", TEMPLATE_ID, MagicMock(), pod_name="aliyun-ack-1"
            )
            info = sb.get_info()
        assert info.metadata["ip_addr"] == "10.0.0.7"
        assert info.metadata["pod_name"] == "aliyun-ack-1"
        assert info.metadata["namespace"] == "ns"

    def test_get_info_raises_when_ip_missing(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod(pod_ip=None)
            h.core.read_namespaced_pod.return_value = pod
            sb = AliyunAckSandbox(
                "aliyun-ack-1", "ns", TEMPLATE_ID, MagicMock(), pod_name="aliyun-ack-1"
            )
            with pytest.raises(RuntimeError, match="no status.pod_ip"):
                sb.get_info()

    def test_get_info_echoes_ttl_in_minutes(self) -> None:
        with _CoreHarness() as h:
            sb = AliyunAckSandbox(
                "aliyun-ack-1",
                "ns",
                TEMPLATE_ID,
                MagicMock(),
                pod_name="aliyun-ack-1",
                ttl_in_minutes=10080,
            )
            info = sb.get_info()
        assert info.ttl_in_minutes == 10080

    def test_get_info_ttl_none_by_default(self) -> None:
        with _CoreHarness() as h:
            sb = AliyunAckSandbox(
                "aliyun-ack-1", "ns", TEMPLATE_ID, MagicMock(), pod_name="aliyun-ack-1"
            )
            info = sb.get_info()
        assert info.ttl_in_minutes is None

    def test_get_info_derives_ttl_timestamp_from_pod(self) -> None:
        from datetime import UTC, datetime

        created = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.creation_timestamp = created
            pod.metadata.annotations = {"avernet.arcasandbox/ttl-minutes": "10080"}
            h.core.read_namespaced_pod.return_value = pod
            sb = AliyunAckSandbox(
                "aliyun-ack-1", "ns", TEMPLATE_ID, MagicMock(), pod_name="aliyun-ack-1"
            )
            info = sb.get_info()
        # 10080 minutes after 2026-08-01T00:00:00Z = 2026-08-08T00:00:00Z
        expected_ms = int(created.timestamp() * 1000) + 10080 * 60 * 1000
        assert info.ttl_timestamp == expected_ms

    def test_get_info_ttl_timestamp_none_without_creation(self) -> None:
        with _CoreHarness() as h:
            sb = AliyunAckSandbox(
                "aliyun-ack-1",
                "ns",
                TEMPLATE_ID,
                MagicMock(),
                pod_name="aliyun-ack-1",
                ttl_in_minutes=10080,
            )
            info = sb.get_info()
        assert info.ttl_timestamp is None

    def test_get_info_ttl_timestamp_none_with_invalid_annotation(self) -> None:
        from datetime import UTC, datetime

        created = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.creation_timestamp = created
            pod.metadata.annotations = {
                "avernet.arcasandbox/ttl-minutes": "not-a-number"
            }
            h.core.read_namespaced_pod.return_value = pod
            sb = AliyunAckSandbox(
                "aliyun-ack-1", "ns", TEMPLATE_ID, MagicMock(), pod_name="aliyun-ack-1"
            )
            info = sb.get_info()
        assert info.ttl_timestamp is None

    def test_get_info_ttl_timestamp_none_without_ttl(self) -> None:
        from datetime import UTC, datetime

        created = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.creation_timestamp = created
            pod.metadata.annotations = {}
            h.core.read_namespaced_pod.return_value = pod
            sb = AliyunAckSandbox(
                "aliyun-ack-1", "ns", TEMPLATE_ID, MagicMock(), pod_name="aliyun-ack-1"
            )
            info = sb.get_info()
        assert info.ttl_timestamp is None

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

        cfg = AliyunAckClusterConfig(
            "https://ack.example.com", "dummy-token", "default"
        )
        mgr = AliyunAckClientManager(cfg)
        fake = MagicMock()
        mgr.build_client = MagicMock(return_value=fake)
        assert mgr.get_client() is fake
        mgr.build_client.assert_called_once()

    def test_close_noop(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
            AliyunAckClusterConfig,
        )

        cfg = AliyunAckClusterConfig(
            "https://ack.example.com", "dummy-token", "default"
        )
        AliyunAckClientManager(cfg).close()


class TestSanitizePodName:
    def test_sanitizes(self) -> None:
        assert _sanitize_pod_name("ALIYUN_ACK_abc-123") == "aliyun-ack-abc-123"
        assert _sanitize_pod_name("!!!") == "aliyun-ack-sandbox"
