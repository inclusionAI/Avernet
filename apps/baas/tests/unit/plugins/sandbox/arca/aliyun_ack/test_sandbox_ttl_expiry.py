"""Full-coverage tests for the ACK TTL-expiry change.

Covers the changed/added surface in ``_sandbox.py`` (get_info's deadline read +
remaining-minutes derivation, _read_ttl_expiration_timestamp,
_compute_expiration_from_creation, _remaining_minutes, extend_ttl) and
``_sandbox_plugin.py`` (_build_template_vars' TTL_EXPIRATION_TIMESTAMP branch,
connect_sync_sandbox deadline recovery, _convert_outbound_rules,
_wait_for_ready).
"""

from __future__ import annotations

import time
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.device_manage import (
    ArcaCredentials,
    OutBoundOperationRule,
)
from secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox import (
    AliyunAckSandbox,
)
from secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox_plugin import (
    AliyunAckSandboxPlugin,
    _build_template_vars,
    _convert_outbound_rules,
    _sanitize_pod_name,
    _wait_for_ready,
)
from secbaas.community.spi.sandbox.arca import ArcaSandboxInfo

_SANDBOX = "secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox"
_PLUGIN = "secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox_plugin"
_MGR = "secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager"

TEMPLATE_ID = "ALIYUN_ACK_DEFAULT"
ANN_KEY = "avernet.arcasandbox/ttl_expiration_timestamp"


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
    def __init__(self) -> None:
        self.core = MagicMock()
        self.core.read_namespaced_pod.return_value = _FakePod()
        self.core.list_namespaced_pod.return_value = _FakePodList()
        self.apps = MagicMock()
        self.client = MagicMock()
        self.create_from_yaml = MagicMock()
        self._stack: ExitStack | None = None

    def __enter__(self) -> _CoreHarness:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
            AliyunAckClientManager,
        )

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


def _plugin(**kw) -> AliyunAckSandboxPlugin:
    kw.setdefault(
        "config",
        ArcaCredentials(
            template_id=1,
            template_uuid="u",
            base_url="http://x",
            api_key="k",
            arca_template_id=TEMPLATE_ID,
            app_name="sandbox-ns",
        ),
    )
    kw.setdefault("arca_utils", MagicMock())
    return AliyunAckSandboxPlugin(**kw)


def _sandbox(**kw) -> AliyunAckSandbox:
    kw.setdefault("sandbox_id", "aliyun-ack-1")
    kw.setdefault("namespace", "ns")
    kw.setdefault("template_id", TEMPLATE_ID)
    kw.setdefault("client", MagicMock())
    kw.setdefault("pod_name", "aliyun-ack-1")
    return AliyunAckSandbox(**kw)


class TestReadTtlExpirationTimestamp:
    def test_reads_positive_int(self) -> None:
        pod = _FakePod()
        pod.metadata.annotations = {ANN_KEY: "1750000000000"}
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) == 1750000000000

    def test_reads_float_string(self) -> None:
        pod = _FakePod()
        pod.metadata.annotations = {ANN_KEY: "1750000000000.9"}
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) == 1750000000000

    def test_returns_none_when_missing(self) -> None:
        pod = _FakePod()
        pod.metadata.annotations = {}
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) is None

    def test_returns_none_when_annotations_missing(self) -> None:
        pod = _FakePod()
        pod.metadata.annotations = None
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) is None

    def test_returns_none_when_non_numeric(self) -> None:
        pod = _FakePod()
        pod.metadata.annotations = {ANN_KEY: "not-a-number"}
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) is None

    def test_returns_none_when_overflow(self) -> None:
        pod = _FakePod()
        pod.metadata.annotations = {ANN_KEY: "1e9999"}
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) is None

    def test_returns_none_when_zero(self) -> None:
        pod = _FakePod()
        pod.metadata.annotations = {ANN_KEY: "0"}
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) is None

    def test_returns_none_when_negative(self) -> None:
        pod = _FakePod()
        pod.metadata.annotations = {ANN_KEY: "-5"}
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) is None

    def test_returns_none_when_no_metadata(self) -> None:
        pod = MagicMock()
        pod.metadata = None
        assert AliyunAckSandbox._read_ttl_expiration_timestamp(pod) is None


class TestComputeExpirationFromCreation:
    def test_computes(self) -> None:
        created = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
        pod = _FakePod()
        pod.metadata.creation_timestamp = created
        expected = int(created.timestamp() * 1000) + 10080 * 60 * 1000
        assert (
            AliyunAckSandbox._compute_expiration_from_creation(pod, 10080) == expected
        )

    def test_none_when_ttl_none(self) -> None:
        pod = _FakePod()
        assert AliyunAckSandbox._compute_expiration_from_creation(pod, None) is None

    def test_none_when_no_metadata(self) -> None:
        pod = MagicMock()
        pod.metadata = None
        assert AliyunAckSandbox._compute_expiration_from_creation(pod, 10080) is None

    def test_none_when_no_creation_timestamp(self) -> None:
        pod = _FakePod()
        pod.metadata.creation_timestamp = None
        assert AliyunAckSandbox._compute_expiration_from_creation(pod, 10080) is None

    def test_none_when_creation_not_datetime(self) -> None:
        pod = _FakePod()
        pod.metadata.creation_timestamp = "not-a-date"
        assert AliyunAckSandbox._compute_expiration_from_creation(pod, 10080) is None


class TestRemainingMinutes:
    def test_none_when_timestamp_none(self) -> None:
        assert AliyunAckSandbox._remaining_minutes(None) is None

    def test_positive_remaining(self) -> None:
        future_ms = time.time() * 1000 + 10 * 60 * 1000
        assert 9.9 <= AliyunAckSandbox._remaining_minutes(future_ms) <= 10.0

    def test_negative_when_past(self) -> None:
        past_ms = time.time() * 1000 - 5 * 60 * 1000
        assert AliyunAckSandbox._remaining_minutes(past_ms) < 0


class TestGetInfo:
    def test_unknown_status_when_phase_none(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.status.phase = None
            h.core.read_namespaced_pod.return_value = pod
            info = _sandbox().get_info()
        assert info.status == "UNKNOWN"

    def test_raises_runtime_error_on_api_exception(self) -> None:
        from kubernetes.client.exceptions import ApiException

        with _CoreHarness() as h:
            h.core.read_namespaced_pod.side_effect = ApiException(status=500)
            sb = _sandbox()
            with pytest.raises(RuntimeError, match="get_info failed"):
                sb.get_info()

    def test_expiration_annotation_wins_over_creation_fallback(self) -> None:
        created = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
        expiry = int(created.timestamp() * 1000) + 60 * 60 * 1000
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.creation_timestamp = created
            pod.metadata.annotations = {ANN_KEY: str(expiry)}
            h.core.read_namespaced_pod.return_value = pod
            info = _sandbox(ttl_in_minutes=10080).get_info()
        assert info.ttl_timestamp == expiry

    def test_is_ready_true(self) -> None:
        with _CoreHarness():
            assert _sandbox().is_ready is True

    def test_is_ready_false_when_pending(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod(phase="Pending")
            h.core.read_namespaced_pod.return_value = pod
            assert _sandbox().is_ready is False

    def test_is_ready_false_on_error(self) -> None:
        with _CoreHarness() as h:
            h.core.read_namespaced_pod.side_effect = RuntimeError("boom")
            assert _sandbox().is_ready is False


class TestExtendTtl:
    def test_extends_existing_deadline(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            base = 1750000000000
            pod.metadata.annotations = {ANN_KEY: str(base)}
            h.core.read_namespaced_pod.return_value = pod
            assert _sandbox().extend_ttl(30) is True
        patches = h.core.patch_namespaced_pod.call_args
        expected = str(int(base + 30 * 60 * 1000))
        assert patches.kwargs["body"]["metadata"]["annotations"][ANN_KEY] == expected

    def test_extends_from_now_without_deadline(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.annotations = {}
            h.core.read_namespaced_pod.return_value = pod
            before = int(time.time() * 1000)
            assert _sandbox().extend_ttl(30) is True
        patches = h.core.patch_namespaced_pod.call_args
        new_val = int(patches.kwargs["body"]["metadata"]["annotations"][ANN_KEY])
        assert (
            before + 30 * 60 * 1000 - 1000 <= new_val <= before + 30 * 60 * 1000 + 1000
        )

    def test_raises_on_api_exception(self) -> None:
        from kubernetes.client.exceptions import ApiException

        with _CoreHarness() as h:
            h.core.read_namespaced_pod.side_effect = ApiException(status=500)
            with pytest.raises(RuntimeError, match="extend_ttl failed"):
                _sandbox().extend_ttl(30)


class TestDestroy:
    def test_destroy_success(self) -> None:
        with _CoreHarness() as h:
            sb = _sandbox(
                deployment_name="avernet-agent-uid",
                resource_names={
                    "Deployment": "avernet-agent-uid",
                    "ConfigMap": "envoy-header-rules-uid",
                },
            )
            assert sb.destroy() is True
        h.apps.delete_namespaced_deployment.assert_called_once()
        h.core.delete_namespaced_config_map.assert_called_once()
        h.core.delete_namespaced_network_policy.assert_not_called()

    def test_destroy_raises_on_500(self) -> None:
        from kubernetes.client.exceptions import ApiException

        with _CoreHarness() as h:
            h.apps.delete_namespaced_deployment.side_effect = ApiException(status=500)
            sb = _sandbox(deployment_name="avernet-agent-uid")
            with pytest.raises(RuntimeError, match="destroy failed"):
                sb.destroy()


class TestExecCommand:
    def test_exec_success(self) -> None:
        with _CoreHarness() as h:
            fake_resp = MagicMock()
            fake_resp.returncode = 0
            fake_resp.read_stdout.return_value = "out"
            fake_resp.read_stderr.return_value = "err"
            with patch(f"{_SANDBOX}.k8s_stream", return_value=fake_resp):
                result = _sandbox().exec_command("echo hi")
        assert result.exit_code == 0
        assert result.stdout == "out"
        assert result.stderr == "err"

    def test_exec_raises_on_api_exception(self) -> None:
        from kubernetes.client.exceptions import ApiException

        with _CoreHarness() as h:
            with patch(
                f"{_SANDBOX}.k8s_stream",
                side_effect=ApiException(status=500, reason="boom"),
            ):
                with pytest.raises(RuntimeError, match="exec_command failed"):
                    _sandbox().exec_command("echo hi")


class TestUpdateOutboundRule:
    def test_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            _sandbox().update_outbound_rule(MagicMock(), MagicMock())


class TestBuildTemplateVars:
    def test_empty_ttl_when_none(self) -> None:
        vars_ = _build_template_vars("u", "ns", {})
        assert vars_["ttl_expiration_timestamp"] == ""

    def test_computes_absolute_deadline(self) -> None:
        now_ms = int(time.time() * 1000)
        vars_ = _build_template_vars("u", "ns", {}, ttl_in_minutes=10)
        expiry = int(vars_["ttl_expiration_timestamp"])
        assert expiry >= now_ms + 10 * 60 * 1000 - 1000
        assert expiry <= now_ms + 10 * 60 * 1000 + 1000

    def test_storage_and_resource_vars(self) -> None:
        storage = MagicMock()
        storage.storage_id = "sid"
        storage.path = "/mnt"
        storage.quota = "2Gi"
        spec = MagicMock()
        spec.cpu = 4
        spec.memory = 8
        images = {"avernet-agent": "img", "avernet-sidecar": "side", "init": "ini"}
        vars_ = _build_template_vars(
            "u", "ns", images, storage=storage, resource_spec=spec
        )
        assert vars_["storage_id"] == "sid"
        assert vars_["mount_path"] == "/mnt"
        assert vars_["storage_size"] == "2Gi"
        assert vars_["cpu"] == "4"
        assert vars_["memory"] == "8Gi"
        assert vars_["agent_image"] == "img"


class TestConvertOutboundRules:
    def test_empty(self) -> None:
        assert _convert_outbound_rules(None) == "    rules: []"

    def test_set_and_remove(self) -> None:
        rule = MagicMock()
        h1 = MagicMock()
        h1.domains = ["a.com"]
        h1.action = "set"
        h1.header_name = "X-1"
        h1.value = "v1"
        h1.placeholder = None
        h2 = MagicMock()
        h2.domains = ["a.com"]
        h2.action = "remove"
        h2.header_name = "X-2"
        h2.value = ""
        h2.placeholder = None
        rule.header_operation_rules = [h1, h2]
        out = _convert_outbound_rules(rule)
        assert "set" in out
        assert "remove" in out
        assert "X-1" in out


class TestWaitForReady:
    def test_returns_name_when_running(self) -> None:
        core = MagicMock()
        core.list_namespaced_pod.return_value = _FakePodList([_FakePod()])
        name = _wait_for_ready(core, "uid", "ns", 1)
        assert name == "openclaw-ack-test-pod"

    def test_timeout(self) -> None:
        core = MagicMock()
        core.list_namespaced_pod.return_value = _FakePodList([_FakePod("Pending")])
        with patch(f"{_PLUGIN}.time.sleep"):
            with pytest.raises(RuntimeError, match="did not become ready"):
                _wait_for_ready(core, "uid", "ns", 2)

    def test_timeout_with_items_but_not_running(self) -> None:
        core = MagicMock()
        core.list_namespaced_pod.return_value = _FakePodList([_FakePod("Pending")])
        with patch(f"{_PLUGIN}.time.sleep"):
            with pytest.raises(RuntimeError, match="did not become ready"):
                _wait_for_ready(core, "uid", "ns", 2)

    def test_timeout_with_no_items(self) -> None:
        core = MagicMock()
        core.list_namespaced_pod.return_value = _FakePodList([])
        with patch(f"{_PLUGIN}.time.sleep"):
            with pytest.raises(RuntimeError, match="did not become ready"):
                _wait_for_ready(core, "uid", "ns", 2)

    def test_list_error_raises(self) -> None:
        core = MagicMock()
        core.list_namespaced_pod.side_effect = RuntimeError("k8s down")
        with pytest.raises(RuntimeError, match="failed to list pods"):
            _wait_for_ready(core, "uid", "ns", 1)


class TestConnectTtlRecovery:
    def test_recovers_deadline_from_pod_via_get_info(self) -> None:
        expiry = int(time.time() * 1000) + 60 * 60 * 1000
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.annotations = {ANN_KEY: str(expiry)}
            h.core.list_namespaced_pod.return_value = _FakePodList([pod])
            h.core.read_namespaced_pod.return_value = pod
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
            info = sb.get_info()
        assert info.ttl_timestamp == expiry
        assert info.ttl_in_minutes is not None

    def test_connect_sets_ttl_in_minutes_none(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            h.core.list_namespaced_pod.return_value = _FakePodList([pod])
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
        assert sb._ttl_in_minutes is None

    def test_connect_recovers_template_and_deployment_name(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.labels = {"avernet.arcasandbox/template": "ALIYUN_ACK_DEFAULT"}
            pod.metadata.owner_references = [
                _FakeOwnerRef(kind="ReplicaSet", name="avernet-agent-xyz-1234")
            ]
            pod.spec = _FakePodSpec([_FakeContainer(name="avernet-agent")])
            h.core.list_namespaced_pod.return_value = _FakePodList([pod])
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
        assert sb._template_id == "ALIYUN_ACK_DEFAULT"
        assert sb._deployment_name == "avernet-agent-aliyun-ack-abc123"
        assert sb._container_name == "avernet-agent"

    def test_connect_defaults_when_metadata_missing(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.labels = None
            pod.metadata.owner_references = None
            pod.spec = _FakePodSpec([])
            h.core.list_namespaced_pod.return_value = _FakePodList([pod])
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
        assert sb._template_id == "aliyun_ack"
        assert sb._deployment_name == "avernet-agent-aliyun-ack-abc123"
        assert sb._container_name == "avernet-agent"

    def test_connect_defaults_when_no_owner_or_spec(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.labels = {}
            pod.metadata.owner_references = []
            pod.spec = _FakePodSpec([])
            h.core.list_namespaced_pod.return_value = _FakePodList([pod])
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
        assert sb._template_id == "aliyun_ack"
        assert sb._deployment_name == "avernet-agent-aliyun-ack-abc123"
        assert sb._container_name == "avernet-agent"

    def test_connect_owner_ref_not_replicaset(self) -> None:
        with _CoreHarness() as h:
            pod = _FakePod()
            pod.metadata.owner_references = [
                _FakeOwnerRef(kind="StatefulSet", name="ss-1234")
            ]
            h.core.list_namespaced_pod.return_value = _FakePodList([pod])
            sb = _plugin().connect_sync_sandbox("aliyun-ack-abc123")
        assert sb._deployment_name == "avernet-agent-aliyun-ack-abc123"

    def test_connect_api_exception_raises(self) -> None:
        from kubernetes.client.exceptions import ApiException

        with _CoreHarness() as h:
            h.core.list_namespaced_pod.side_effect = ApiException(status=500)
            with pytest.raises(RuntimeError, match="not found"):
                _plugin().connect_sync_sandbox("aliyun-ack-missing")


class TestGetTemplateId:
    def test_returns_ack_id(self) -> None:
        assert _plugin()._get_template_id() == TEMPLATE_ID

    def test_raises_when_no_config(self) -> None:
        p = AliyunAckSandboxPlugin(config=None)
        with pytest.raises(ValueError, match="arca_template_id"):
            p._get_template_id()

    def test_raises_when_no_ack_id(self) -> None:
        p = AliyunAckSandboxPlugin(
            config=ArcaCredentials(
                template_id=1,
                template_uuid="u",
                base_url="http://x",
                api_key="k",
                arca_template_id=None,
                app_name="ns",
            )
        )
        with pytest.raises(ValueError, match="arca_template_id"):
            p._get_template_id()


class TestClientManagerFull:
    def test_validate_missing_token(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
            AliyunAckClientManager,
            AliyunAckClusterConfig,
        )

        cfg = AliyunAckClusterConfig("https://ack", "", "ns")
        with pytest.raises(ValueError, match="token"):
            AliyunAckClientManager(cfg).validate()

    def test_build_client(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
            AliyunAckClientManager,
            AliyunAckClusterConfig,
        )

        cfg = AliyunAckClusterConfig("https://ack", "tok", "ns")
        client = AliyunAckClientManager(cfg).build_client()
        assert client is not None

    def test_get_client_cached(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
            AliyunAckClientManager,
            AliyunAckClusterConfig,
        )

        cfg = AliyunAckClusterConfig("https://ack", "tok", "ns")
        mgr = AliyunAckClientManager(cfg)
        c1 = mgr.get_client()
        c2 = mgr.get_client()
        assert c1 is c2
        mgr.close()

    def test_close_with_client_and_error(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._client_manager import (
            AliyunAckClientManager,
            AliyunAckClusterConfig,
        )

        cfg = AliyunAckClusterConfig("https://ack", "tok", "ns")
        mgr = AliyunAckClientManager(cfg)
        mgr._client = MagicMock()
        mgr._client.close.side_effect = RuntimeError("close boom")
        mgr.close()
        assert mgr._client is None


class TestConvertOutboundRulesPlaceholder:
    def test_placeholder_included(self) -> None:
        rule = MagicMock()
        h1 = MagicMock()
        h1.domains = ["a.com"]
        h1.action = "replace"
        h1.header_name = "X-1"
        h1.value = "v1"
        h1.placeholder = "ph"
        rule.header_operation_rules = [h1]
        out = _convert_outbound_rules(rule)
        assert "placeholder" in out
        assert "ph" in out

    def test_unknown_action_ignored(self) -> None:
        rule = MagicMock()
        h1 = MagicMock()
        h1.domains = ["a.com"]
        h1.action = "bogus"
        h1.header_name = "X-1"
        h1.value = "v1"
        h1.placeholder = None
        rule.header_operation_rules = [h1]
        out = _convert_outbound_rules(rule)
        assert "X-1" not in out

    def test_remove_action_appended(self) -> None:
        rule = MagicMock()
        h1 = MagicMock()
        h1.domains = ["a.com"]
        h1.action = "remove"
        h1.header_name = "X-1"
        h1.value = "v1"
        h1.placeholder = None
        rule.header_operation_rules = [h1]
        out = _convert_outbound_rules(rule)
        assert "X-1" in out
        assert "remove:" in out


class TestPluginFactory:
    def test_factory_builds_plugin(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox_plugin import (
            aliyun_ack_plugin_factory,
        )

        build = aliyun_ack_plugin_factory(default_images={"a": "b"})
        plugin = build(_creds_like())
        assert isinstance(plugin, AliyunAckSandboxPlugin)
        assert plugin._default_images == {"a": "b"}

    def test_factory_absorbs_positional_arg(self) -> None:
        from secbaas.community.plugins.sandbox.arca.aliyun_ack._sandbox_plugin import (
            aliyun_ack_plugin_factory,
        )

        build = aliyun_ack_plugin_factory("ignored", default_images={})
        plugin = build(_creds_like())
        assert isinstance(plugin, AliyunAckSandboxPlugin)


def _creds_like():  # type: ignore[no-untyped-def]
    return ArcaCredentials(
        template_id=1,
        template_uuid="u",
        base_url="http://x",
        api_key="k",
        arca_template_id=TEMPLATE_ID,
        app_name="sandbox-ns",
    )
