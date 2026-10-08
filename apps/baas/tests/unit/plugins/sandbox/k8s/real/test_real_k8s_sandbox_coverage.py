"""Comprehensive unit tests for RealK8sSandbox and RealK8sSandboxPlugin.

Mocks the kubernetes SDK to avoid needing a real cluster.
Covers all public and private methods, branches, error paths, and edge cases.
"""

from __future__ import annotations

import base64
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Mock the kubernetes SDK before importing the source module.
# ---------------------------------------------------------------------------

# We need a real ApiException class because the source code catches it
# and accesses .status on it.


class ApiException(Exception):
    """Fake kubernetes ApiException with a .status attribute."""

    def __init__(self, status: int = 500, reason: str = "", body: str = ""):
        self.status = status
        self.reason = reason
        self.body = body
        super().__init__(f"({status}) {reason}")


# Build a mock kubernetes module tree
_k8s_client_mock = MagicMock()
_k8s_client_rest_mock = MagicMock()
_k8s_client_rest_mock.ApiException = ApiException

# Each V1* class should be a plain callable that stores kwargs so we can
# inspect arguments in tests.  Using MagicMock for them works but doesn't
# let us easily assert constructor kwargs.  Let's use a simple class factory.


class _K8sModel:
    """Simple stand-in for kubernetes model classes. Stores all kwargs."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.__dict__})"


# Map every V1* class name used in the source to _K8sModel
_K8S_CLASS_NAMES = [
    "AppsV1Api",
    "CoreV1Api",
    "V1ConfigMap",
    "V1ConfigMapVolumeSource",
    "V1Container",
    "V1ContainerPort",
    "V1DeleteOptions",
    "V1EnvVar",
    "V1LabelSelector",
    "V1ObjectMeta",
    "V1Pod",
    "V1PodSpec",
    "V1PodTemplateSpec",
    "V1ResourceRequirements",
    "V1StatefulSet",
    "V1StatefulSetSpec",
    "V1Volume",
    "V1VolumeMount",
    "ApiClient",
]

for _name in _K8S_CLASS_NAMES:
    _cls = type(_name, (_K8sModel,), {})
    setattr(_k8s_client_mock, _name, _cls)

_k8s_client_mock.rest = _k8s_client_rest_mock

# Also mock the kubernetes.config sub-module (used by client_manager)
_k8s_config_mock = MagicMock()
_k8s_mock = MagicMock()
_k8s_mock.client = _k8s_client_mock
_k8s_mock.config = _k8s_config_mock

sys.modules.setdefault("kubernetes", _k8s_mock)
sys.modules.setdefault("kubernetes.client", _k8s_client_mock)
sys.modules.setdefault("kubernetes.client.rest", _k8s_client_rest_mock)
sys.modules.setdefault("kubernetes.config", _k8s_config_mock)

# Now import the source module
# Also import the module itself for attribute manipulation
import secbaas.community.plugins.sandbox.k8s.real._real_k8s_sandbox as _mod  # noqa: E402
from secbaas.community.plugins.sandbox.k8s.real._real_k8s_sandbox import (  # noqa: E402
    RealK8sSandbox,
    RealK8sSandboxPlugin,
    _env_list_from_dict,
    _import_k8s,
    _sanitize_rfc1123,
)

# ---------------------------------------------------------------------------
# Trigger _import_k8s once so the module namespace is populated.
# ---------------------------------------------------------------------------
_import_k8s()

# Convenience references to the kubernetes classes now attached to the module
AppsV1Api = _mod.AppsV1Api
CoreV1Api = _mod.CoreV1Api
V1ConfigMap = _mod.V1ConfigMap
V1ConfigMapVolumeSource = _mod.V1ConfigMapVolumeSource
V1Container = _mod.V1Container
V1ContainerPort = _mod.V1ContainerPort
V1DeleteOptions = _mod.V1DeleteOptions
V1EnvVar = _mod.V1EnvVar
V1LabelSelector = _mod.V1LabelSelector
V1ObjectMeta = _mod.V1ObjectMeta
V1Pod = _mod.V1Pod
V1PodSpec = _mod.V1PodSpec
V1PodTemplateSpec = _mod.V1PodTemplateSpec
V1ResourceRequirements = _mod.V1ResourceRequirements
V1StatefulSet = _mod.V1StatefulSet
V1StatefulSetSpec = _mod.V1StatefulSetSpec
V1Volume = _mod.V1Volume
V1VolumeMount = _mod.V1VolumeMount
ModApiException = _mod.ApiException


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_pod_status(
    phase: str | None = "Running",
    pod_ip: str | None = "10.0.0.1",
    container_statuses: list | None = None,
    conditions: list | None = None,
) -> MagicMock:
    """Create a mock V1Pod status object."""
    status = MagicMock()
    status.phase = phase
    status.pod_ip = pod_ip
    status.container_statuses = container_statuses or []
    status.conditions = conditions or []
    return status


def _make_pod(
    phase: str | None = "Running",
    pod_ip: str | None = "10.0.0.1",
    container_statuses: list | None = None,
    conditions: list | None = None,
) -> MagicMock:
    """Create a mock V1Pod."""
    pod = MagicMock()
    pod.status = _make_pod_status(phase, pod_ip, container_statuses, conditions)
    return pod


def _make_container_status(
    name: str = "bot-runtime",
    ready: bool = True,
    restart_count: int = 0,
    image: str = "test:latest",
    state: MagicMock | None = None,
) -> MagicMock:
    cs = MagicMock()
    cs.name = name
    cs.ready = ready
    cs.restart_count = restart_count
    cs.image = image
    cs.state = state
    return cs


def _make_container_state(
    running: Any = None,
    waiting: Any = None,
    terminated: Any = None,
) -> MagicMock:
    state = MagicMock()
    # MagicMock auto-creates attributes, so we need to explicitly set them
    state.running = running
    state.waiting = waiting
    state.terminated = terminated
    return state


def _make_state_obj(reason: str | None = None, message: str | None = None) -> MagicMock:
    obj = MagicMock()
    obj.reason = reason
    obj.message = message
    return obj


def _make_condition(
    type_: str = "Ready",
    status: str = "True",
    reason: str | None = "PodReady",
) -> MagicMock:
    cond = MagicMock()
    cond.type = type_
    cond.status = status
    cond.reason = reason
    return cond


def _make_credentials() -> MagicMock:
    creds = MagicMock()
    creds.kubeconfig = "apiVersion: v1\nkind: Config"
    creds.context = "test-context"
    creds.namespace = "default"
    creds.extra_k8s_opts = {}
    return creds


# ---------------------------------------------------------------------------
# Tests for module-level functions
# ---------------------------------------------------------------------------


class TestImportK8s:
    """Tests for _import_k8s lazy loader."""

    def test_import_k8s_already_loaded(self):
        """When _k8s_loaded is True, _import_k8s is a no-op."""
        # _import_k8s has already been called once. Calling again should
        # return immediately without error.
        original = _mod._k8s_loaded
        _mod._k8s_loaded = True
        try:
            _import_k8s()
            # All classes should still be present
            assert _mod.V1Pod is not None
            assert _mod.CoreV1Api is not None
        finally:
            _mod._k8s_loaded = original

    def test_import_k8s_sets_classes(self):
        """After _import_k8s, all k8s classes are available on the module."""
        assert hasattr(_mod, "AppsV1Api")
        assert hasattr(_mod, "CoreV1Api")
        assert hasattr(_mod, "V1ConfigMap")
        assert hasattr(_mod, "V1ConfigMapVolumeSource")
        assert hasattr(_mod, "V1Container")
        assert hasattr(_mod, "V1ContainerPort")
        assert hasattr(_mod, "V1DeleteOptions")
        assert hasattr(_mod, "V1EnvVar")
        assert hasattr(_mod, "V1LabelSelector")
        assert hasattr(_mod, "V1ObjectMeta")
        assert hasattr(_mod, "V1Pod")
        assert hasattr(_mod, "V1PodSpec")
        assert hasattr(_mod, "V1PodTemplateSpec")
        assert hasattr(_mod, "V1ResourceRequirements")
        assert hasattr(_mod, "V1StatefulSet")
        assert hasattr(_mod, "V1StatefulSetSpec")
        assert hasattr(_mod, "V1Volume")
        assert hasattr(_mod, "V1VolumeMount")
        assert hasattr(_mod, "ApiException")
        assert _mod._k8s_loaded is True


class TestSanitizeRfc1123:
    """Tests for _sanitize_rfc1123 helper."""

    def test_basic_alphanumeric(self):
        assert _sanitize_rfc1123("mybot") == "mybot"

    def test_uppercase_to_lower(self):
        assert _sanitize_rfc1123("MyBot") == "mybot"

    def test_underscores_to_hyphens(self):
        assert _sanitize_rfc1123("my_bot_name") == "my-bot-name"

    def test_special_chars_to_hyphens(self):
        assert _sanitize_rfc1123("my.bot@name!") == "my-bot-name"

    def test_multiple_hyphens_collapsed(self):
        assert _sanitize_rfc1123("my---bot") == "my-bot"

    def test_leading_trailing_hyphens_stripped(self):
        assert _sanitize_rfc1123("---mybot---") == "mybot"

    def test_empty_result_returns_default(self):
        assert _sanitize_rfc1123("___") == "k8s-bot"

    def test_all_special_chars(self):
        assert _sanitize_rfc1123("!!!") == "k8s-bot"

    def test_already_clean(self):
        assert _sanitize_rfc1123("abc-123") == "abc-123"

    def test_uuid_format(self):
        result = _sanitize_rfc1123("tenant-uuid-1234")
        assert result == "tenant-uuid-1234"

    def test_mixed_case_with_special(self):
        assert _sanitize_rfc1123("Tenant.UUID_123") == "tenant-uuid-123"


class TestEnvListFromDict:
    """Tests for _env_list_from_dict helper."""

    def test_empty_dict(self):
        assert _env_list_from_dict({}) == []

    def test_none_dict(self):
        assert _env_list_from_dict(None) == []

    def test_single_entry(self):
        result = _env_list_from_dict({"KEY": "value"})
        assert len(result) == 1
        assert result[0].name == "KEY"
        assert result[0].value == "value"

    def test_multiple_entries(self):
        result = _env_list_from_dict({"A": "1", "B": "2", "C": "3"})
        assert len(result) == 3
        names = [e.name for e in result]
        values = [e.value for e in result]
        assert "A" in names and "B" in names and "C" in names
        assert "1" in values and "2" in values and "3" in values


# ---------------------------------------------------------------------------
# Tests for RealK8sSandbox
# ---------------------------------------------------------------------------


class TestRealK8sSandbox:
    """Tests for RealK8sSandbox class."""

    @pytest.fixture
    def mock_client(self):
        return MagicMock()

    @pytest.fixture
    def sandbox(self, mock_client):
        return RealK8sSandbox(
            sandbox_id="test-sts-0",
            namespace="default",
            pod=None,
            client=mock_client,
        )

    # --- sandbox_id property ---

    def test_sandbox_id_property(self, sandbox):
        assert sandbox.sandbox_id == "test-sts-0"

    # --- is_ready property ---

    def test_is_ready_pod_none(self, sandbox):
        assert sandbox.is_ready is False

    def test_is_ready_pod_running(self, mock_client):
        pod = _make_pod(phase="Running")
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        assert sb.is_ready is True

    def test_is_ready_pod_not_running(self, mock_client):
        pod = _make_pod(phase="Pending")
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        assert sb.is_ready is False

    def test_is_ready_pod_status_none(self, mock_client):
        pod = MagicMock()
        pod.status = None
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        assert sb.is_ready is False

    def test_is_ready_pod_phase_none(self, mock_client):
        pod = MagicMock()
        pod.status = MagicMock()
        pod.status.phase = None
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        assert sb.is_ready is False

    # --- get_info ---

    def test_get_info_pod_none(self, sandbox):
        info = sandbox.get_info()
        assert info["sandbox_id"] == "test-sts-0"
        assert info["namespace"] == "default"
        assert info["status"] == "PROVISIONING"
        assert info["pod_ip"] is None
        assert info["container_statuses"] == []
        assert info["conditions"] == []

    def test_get_info_basic(self, mock_client):
        pod = _make_pod(phase="Running", pod_ip="10.0.0.5")
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        assert info["sandbox_id"] == "sb-0"
        assert info["namespace"] == "default"
        assert info["status"] == "Running"
        assert info["pod_ip"] == "10.0.0.5"
        assert info["container_statuses"] == []
        assert info["conditions"] == []

    def test_get_info_phase_none(self, mock_client):
        pod = _make_pod(phase=None)
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        assert info["status"] == "Unknown"

    def test_get_info_with_container_statuses_running(self, mock_client):
        running_state = _make_container_state(
            running=_make_state_obj(reason=None, message=None),
            waiting=None,
            terminated=None,
        )
        cs = _make_container_status(
            name="bot-runtime",
            ready=True,
            restart_count=0,
            image="img:latest",
            state=running_state,
        )
        pod = _make_pod(container_statuses=[cs])
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        assert len(info["container_statuses"]) == 1
        cs_info = info["container_statuses"][0]
        assert cs_info["name"] == "bot-runtime"
        assert cs_info["ready"] is True
        assert cs_info["restart_count"] == 0
        assert cs_info["state"] == "running"
        assert cs_info["image"] == "img:latest"

    def test_get_info_with_container_statuses_waiting(self, mock_client):
        waiting_state = _make_container_state(
            running=None,
            waiting=_make_state_obj(reason="ImagePullBackOff", message="pulling"),
            terminated=None,
        )
        cs = _make_container_status(name="c1", ready=False, state=waiting_state)
        pod = _make_pod(container_statuses=[cs])
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        cs_info = info["container_statuses"][0]
        assert cs_info["state"] == "waiting"

    def test_get_info_with_container_statuses_terminated(self, mock_client):
        terminated_state = _make_container_state(
            running=None,
            waiting=None,
            terminated=_make_state_obj(reason="Completed", message="done"),
        )
        cs = _make_container_status(name="c1", ready=False, state=terminated_state)
        pod = _make_pod(container_statuses=[cs])
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        cs_info = info["container_statuses"][0]
        assert cs_info["state"] == "terminated"

    def test_get_info_with_container_state_none(self, mock_client):
        """When cs.state is None, state_info should be {"type": "unknown"}."""
        cs = _make_container_status(name="c1", state=None)
        pod = _make_pod(container_statuses=[cs])
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        cs_info = info["container_statuses"][0]
        assert cs_info["state"] == "unknown"

    def test_get_info_with_container_state_no_matching_attr(self, mock_client):
        """When cs.state has running/waiting/terminated all None,
        state_info should be {"type": "unknown"}."""
        state = _make_container_state(running=None, waiting=None, terminated=None)
        cs = _make_container_status(name="c1", state=state)
        pod = _make_pod(container_statuses=[cs])
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        cs_info = info["container_statuses"][0]
        assert cs_info["state"] == "unknown"

    def test_get_info_with_conditions(self, mock_client):
        cond = _make_condition(type_="Ready", status="True", reason="PodReady")
        pod = _make_pod(conditions=[cond])
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        assert len(info["conditions"]) == 1
        cond_info = info["conditions"][0]
        assert cond_info["type"] == "Ready"
        assert cond_info["status"] == "True"
        assert cond_info["reason"] == "PodReady"

    def test_get_info_status_none_attrs(self, mock_client):
        """When pod.status attributes are falsy, defaults are used."""
        pod = MagicMock()
        pod.status = MagicMock()
        pod.status.phase = None
        pod.status.pod_ip = None
        pod.status.container_statuses = None
        pod.status.conditions = None
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        assert info["status"] == "Unknown"
        assert info["pod_ip"] is None
        assert info["container_statuses"] == []
        assert info["conditions"] == []

    def test_get_info_multiple_container_statuses(self, mock_client):
        running_state = _make_container_state(
            running=_make_state_obj(),
            waiting=None,
            terminated=None,
        )
        terminated_state = _make_container_state(
            running=None,
            waiting=None,
            terminated=_make_state_obj(reason="Error", message="crash"),
        )
        cs1 = _make_container_status(name="c1", ready=True, state=running_state)
        cs2 = _make_container_status(
            name="c2", ready=False, restart_count=3, state=terminated_state
        )
        pod = _make_pod(container_statuses=[cs1, cs2])
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)
        info = sb.get_info()
        assert len(info["container_statuses"]) == 2
        assert info["container_statuses"][0]["name"] == "c1"
        assert info["container_statuses"][1]["name"] == "c2"
        assert info["container_statuses"][1]["restart_count"] == 3

    # --- exec_command ---

    def test_exec_command_success(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)

        mock_core_api = MagicMock()
        mock_resp = MagicMock()
        mock_resp.returncode = 0
        mock_resp.read_stdout.return_value = "hello"
        mock_resp.read_stderr.return_value = ""
        mock_core_api.connect_get_namespaced_pod_exec.return_value = mock_resp

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = sb.exec_command("echo hello", timeout_in_millis=5000)

        assert result.exit_code == 0
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.elapsed_time == 0.0
        mock_core_api.connect_get_namespaced_pod_exec.assert_called_once()
        call_kwargs = mock_core_api.connect_get_namespaced_pod_exec.call_args.kwargs
        assert call_kwargs["name"] == "sb-0"
        assert call_kwargs["namespace"] == "default"
        assert call_kwargs["command"] == ["/bin/sh", "-c", "echo hello"]
        mock_resp.run_forever.assert_called_once_with(timeout=5.0)

    def test_exec_command_none_returncode(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)

        mock_core_api = MagicMock()
        mock_resp = MagicMock()
        mock_resp.returncode = None
        mock_resp.read_stdout.return_value = "output"
        mock_resp.read_stderr.return_value = "err"
        mock_core_api.connect_get_namespaced_pod_exec.return_value = mock_resp

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = sb.exec_command("cmd")

        assert result.exit_code == 0

    def test_exec_command_none_stdout_stderr(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)

        mock_core_api = MagicMock()
        mock_resp = MagicMock()
        mock_resp.returncode = 1
        mock_resp.read_stdout.return_value = None
        mock_resp.read_stderr.return_value = None
        mock_core_api.connect_get_namespaced_pod_exec.return_value = mock_resp

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = sb.exec_command("cmd")

        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == ""

    def test_exec_command_api_exception(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)

        mock_core_api = MagicMock()
        mock_core_api.connect_get_namespaced_pod_exec.side_effect = ModApiException(
            status=500, reason="Internal Server Error"
        )

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="exec_command failed"):
                sb.exec_command("cmd")

    def test_exec_command_with_envs(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)

        mock_core_api = MagicMock()
        mock_resp = MagicMock()
        mock_resp.returncode = 0
        mock_resp.read_stdout.return_value = "ok"
        mock_resp.read_stderr.return_value = ""
        mock_core_api.connect_get_namespaced_pod_exec.return_value = mock_resp

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = sb.exec_command("cmd", envs={"FOO": "bar"})

        assert result.exit_code == 0

    # --- destroy ---

    def test_destroy_success(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("my-sts-0", "default", pod, mock_client)

        mock_apps_api = MagicMock()
        mock_core_api = MagicMock()

        with (
            patch.object(_mod, "AppsV1Api", return_value=mock_apps_api),
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
        ):
            result = sb.destroy()

        assert result is True
        mock_apps_api.delete_namespaced_stateful_set.assert_called_once()
        mock_core_api.delete_namespaced_config_map.assert_called_once()

    def test_destroy_statefulset_404(self, mock_client):
        """When StatefulSet is already gone (404), destroy is idempotent."""
        pod = _make_pod()
        sb = RealK8sSandbox("my-sts-0", "default", pod, mock_client)

        mock_apps_api = MagicMock()
        mock_apps_api.delete_namespaced_stateful_set.side_effect = ModApiException(
            status=404
        )
        mock_core_api = MagicMock()

        with (
            patch.object(_mod, "AppsV1Api", return_value=mock_apps_api),
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
        ):
            result = sb.destroy()

        assert result is True

    def test_destroy_statefulset_non_404_error(self, mock_client):
        """When StatefulSet deletion fails with non-404, RuntimeError is raised."""
        pod = _make_pod()
        sb = RealK8sSandbox("my-sts-0", "default", pod, mock_client)

        mock_apps_api = MagicMock()
        mock_apps_api.delete_namespaced_stateful_set.side_effect = ModApiException(
            status=500
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            with pytest.raises(RuntimeError, match="destroy failed"):
                sb.destroy()

    def test_destroy_configmap_404(self, mock_client):
        """ConfigMap 404 is non-fatal (pass)."""
        pod = _make_pod()
        sb = RealK8sSandbox("my-sts-0", "default", pod, mock_client)

        mock_apps_api = MagicMock()
        mock_core_api = MagicMock()
        mock_core_api.delete_namespaced_config_map.side_effect = ModApiException(
            status=404
        )

        with (
            patch.object(_mod, "AppsV1Api", return_value=mock_apps_api),
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
        ):
            result = sb.destroy()

        assert result is True

    def test_destroy_configmap_non_404_error(self, mock_client):
        """ConfigMap non-404 error is logged as warning but returns True."""
        pod = _make_pod()
        sb = RealK8sSandbox("my-sts-0", "default", pod, mock_client)

        mock_apps_api = MagicMock()
        mock_core_api = MagicMock()
        mock_core_api.delete_namespaced_config_map.side_effect = ModApiException(
            status=403
        )

        with (
            patch.object(_mod, "AppsV1Api", return_value=mock_apps_api),
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
        ):
            result = sb.destroy()

        assert result is True

    def test_destroy_no_ordinal_in_sandbox_id(self, mock_client):
        """When sandbox_id has no ordinal part, statefulset_name = sandbox_id."""
        pod = _make_pod()
        sb = RealK8sSandbox("noordinal", "default", pod, mock_client)

        mock_apps_api = MagicMock()
        mock_core_api = MagicMock()

        with (
            patch.object(_mod, "AppsV1Api", return_value=mock_apps_api),
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
        ):
            result = sb.destroy()

        assert result is True
        # statefulset_name should be "noordinal" (no rsplit match)
        del_args = mock_apps_api.delete_namespaced_stateful_set.call_args.kwargs
        assert del_args["name"] == "noordinal"

    # --- restart ---

    def test_restart_success(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)

        mock_core_api = MagicMock()

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = sb.restart()

        assert result is True
        mock_core_api.delete_namespaced_pod.assert_called_once()

    def test_restart_404(self, mock_client):
        """When Pod is already gone (404), restart is idempotent."""
        pod = _make_pod()
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)

        mock_core_api = MagicMock()
        mock_core_api.delete_namespaced_pod.side_effect = ModApiException(status=404)

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = sb.restart()

        assert result is True

    def test_restart_non_404_error(self, mock_client):
        """When Pod deletion fails with non-404, RuntimeError is raised."""
        pod = _make_pod()
        sb = RealK8sSandbox("sb-0", "default", pod, mock_client)

        mock_core_api = MagicMock()
        mock_core_api.delete_namespaced_pod.side_effect = ModApiException(status=500)

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="restart failed"):
                sb.restart()

    # --- update ---

    def test_update_success(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("my-sts-0", "default", pod, mock_client)

        mock_apps_api = MagicMock()

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = sb.update(replicas=2, image="new:latest")

        assert result is True
        mock_apps_api.patch_namespaced_stateful_set.assert_called_once()
        call_args = mock_apps_api.patch_namespaced_stateful_set.call_args.kwargs
        assert call_args["name"] == "my-sts"
        assert call_args["body"] == {"spec": {"replicas": 2, "image": "new:latest"}}

    def test_update_no_ordinal_in_sandbox_id(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("noordinal", "default", pod, mock_client)

        mock_apps_api = MagicMock()

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = sb.update(replicas=3)

        assert result is True
        call_args = mock_apps_api.patch_namespaced_stateful_set.call_args.kwargs
        assert call_args["name"] == "noordinal"

    def test_update_api_exception(self, mock_client):
        pod = _make_pod()
        sb = RealK8sSandbox("my-sts-0", "default", pod, mock_client)

        mock_apps_api = MagicMock()
        mock_apps_api.patch_namespaced_stateful_set.side_effect = ModApiException(
            status=500
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            with pytest.raises(RuntimeError, match="update failed"):
                sb.update(replicas=2)


# ---------------------------------------------------------------------------
# Tests for RealK8sSandboxPlugin
# ---------------------------------------------------------------------------


class TestRealK8sSandboxPlugin:
    """Tests for RealK8sSandboxPlugin class."""

    @pytest.fixture
    def credentials(self):
        return _make_credentials()

    @pytest.fixture
    def client_manager(self):
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        return cm

    @pytest.fixture
    def plugin(self, credentials, client_manager):
        p = RealK8sSandboxPlugin(credentials, client_manager)
        p._client = MagicMock()
        return p

    # --- __init__ ---

    def test_init(self, credentials, client_manager):
        p = RealK8sSandboxPlugin(credentials, client_manager)
        assert p._credentials is credentials
        assert p._client_manager is client_manager
        assert p._client is None

    # --- _ensure_client ---

    def test_ensure_client_creates_client(self, credentials, client_manager):
        p = RealK8sSandboxPlugin(credentials, client_manager)
        mock_client = MagicMock()
        client_manager.get_or_create_client.return_value = mock_client

        client = p._ensure_client()

        assert client is mock_client
        client_manager.get_or_create_client.assert_called_once_with(credentials)

    def test_ensure_client_caches(self, credentials, client_manager):
        p = RealK8sSandboxPlugin(credentials, client_manager)
        mock_client = MagicMock()
        client_manager.get_or_create_client.return_value = mock_client

        client1 = p._ensure_client()
        client2 = p._ensure_client()

        assert client1 is client2
        client_manager.get_or_create_client.assert_called_once()

    # --- create_device (new StatefulSet path) ---

    def test_create_device_new_statefulset(self, plugin):
        mock_apps_api = MagicMock()
        # read_namespaced_stateful_set raises 404 -> new path
        mock_apps_api.read_namespaced_stateful_set.side_effect = ModApiException(
            status=404
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            sandbox = plugin.create_device(
                template_id=1,
                template_uuid="uuid-1234",
                tenant_name="tenant-1",
                namespace="default",
                image="test:latest",
                cpu_request="100m",
                cpu_limit="200m",
                memory_request="128Mi",
                memory_limit="256Mi",
            )

        assert sandbox.sandbox_id == "tenant-1-uuid-1234-0"
        assert sandbox._namespace == "default"
        assert sandbox._pod is None
        mock_apps_api.create_namespaced_stateful_set.assert_called_once()

    def test_create_device_new_statefulset_with_envs(self, plugin):
        mock_apps_api = MagicMock()
        mock_apps_api.read_namespaced_stateful_set.side_effect = ModApiException(
            status=404
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            sandbox = plugin.create_device(
                template_id=1,
                template_uuid="uuid-1",
                tenant_name="tenant-1",
                namespace="default",
                image="test:latest",
                cpu_request="100m",
                cpu_limit="200m",
                memory_request="128Mi",
                memory_limit="256Mi",
                envs={"FOO": "bar", "BAZ": "qux"},
            )

        assert sandbox.sandbox_id == "tenant-1-uuid-1-0"

    def test_create_device_new_statefulset_with_metadata(self, plugin):
        mock_apps_api = MagicMock()
        mock_apps_api.read_namespaced_stateful_set.side_effect = ModApiException(
            status=404
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            sandbox = plugin.create_device(
                template_id=1,
                template_uuid="uuid-1",
                tenant_name="tenant-1",
                namespace="default",
                image="test:latest",
                cpu_request="100m",
                cpu_limit="200m",
                memory_request="128Mi",
                memory_limit="256Mi",
                metadata={"custom-label": "val"},
            )

        assert sandbox.sandbox_id == "tenant-1-uuid-1-0"

    def test_create_device_new_statefulset_with_envoy(self, plugin):
        mock_apps_api = MagicMock()
        mock_apps_api.read_namespaced_stateful_set.side_effect = ModApiException(
            status=404
        )

        with (
            patch.object(_mod, "AppsV1Api", return_value=mock_apps_api),
            patch.object(plugin, "_create_proxy_configmap") as mock_cm,
        ):
            sandbox = plugin.create_device(
                template_id=1,
                template_uuid="uuid-1",
                tenant_name="tenant-1",
                namespace="default",
                image="test:latest",
                cpu_request="100m",
                cpu_limit="200m",
                memory_request="128Mi",
                memory_limit="256Mi",
                envoy_yaml="envoy config yaml",
            )

        assert sandbox.sandbox_id == "tenant-1-uuid-1-0"
        mock_cm.assert_called_once()
        # Verify ConfigMap name is passed correctly
        cm_args = mock_cm.call_args
        assert "tenant-1-uuid-1-proxy-rules" in cm_args[0]

    def test_create_device_scale_up(self, plugin):
        mock_apps_api = MagicMock()
        existing_sts = MagicMock()
        existing_sts.spec.replicas = 2
        mock_apps_api.read_namespaced_stateful_set.return_value = existing_sts

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            sandbox = plugin.create_device(
                template_id=1,
                template_uuid="uuid-1",
                tenant_name="tenant-1",
                namespace="default",
                image="test:latest",
                cpu_request="100m",
                cpu_limit="200m",
                memory_request="128Mi",
                memory_limit="256Mi",
            )

        # ordinal = new_replicas - 1 = 3 - 1 = 2
        assert sandbox.sandbox_id == "tenant-1-uuid-1-2"
        mock_apps_api.patch_namespaced_stateful_set_scale.assert_called_once()
        scale_args = mock_apps_api.patch_namespaced_stateful_set_scale.call_args.kwargs
        assert scale_args["body"] == {"spec": {"replicas": 3}}
        mock_apps_api.create_namespaced_stateful_set.assert_not_called()

    def test_create_device_scale_up_from_zero_replicas(self, plugin):
        """When existing_sts.spec.replicas is None, treat as 0."""
        mock_apps_api = MagicMock()
        existing_sts = MagicMock()
        existing_sts.spec.replicas = None
        mock_apps_api.read_namespaced_stateful_set.return_value = existing_sts

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            sandbox = plugin.create_device(
                template_id=1,
                template_uuid="uuid-1",
                tenant_name="tenant-1",
                namespace="default",
                image="test:latest",
                cpu_request="100m",
                cpu_limit="200m",
                memory_request="128Mi",
                memory_limit="256Mi",
            )

        # current_replicas = None or 0 = 0; new_replicas = 1; ordinal = 0
        assert sandbox.sandbox_id == "tenant-1-uuid-1-0"
        scale_args = mock_apps_api.patch_namespaced_stateful_set_scale.call_args.kwargs
        assert scale_args["body"] == {"spec": {"replicas": 1}}

    def test_create_device_read_sts_non_404_error(self, plugin):
        """When read_namespaced_stateful_set raises non-404, it propagates
        to the outer try/except which wraps it as RuntimeError."""
        mock_apps_api = MagicMock()
        mock_apps_api.read_namespaced_stateful_set.side_effect = ModApiException(
            status=500
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            with pytest.raises(RuntimeError, match="create_device failed"):
                plugin.create_device(
                    template_id=1,
                    template_uuid="uuid-1",
                    tenant_name="tenant-1",
                    namespace="default",
                    image="test:latest",
                    cpu_request="100m",
                    cpu_limit="200m",
                    memory_request="128Mi",
                    memory_limit="256Mi",
                )

    def test_create_device_create_sts_error(self, plugin):
        """When create_namespaced_stateful_set fails, RuntimeError is raised."""
        mock_apps_api = MagicMock()
        mock_apps_api.read_namespaced_stateful_set.side_effect = ModApiException(
            status=404
        )
        mock_apps_api.create_namespaced_stateful_set.side_effect = ModApiException(
            status=500
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            with pytest.raises(RuntimeError, match="create_device failed"):
                plugin.create_device(
                    template_id=1,
                    template_uuid="uuid-1",
                    tenant_name="tenant-1",
                    namespace="default",
                    image="test:latest",
                    cpu_request="100m",
                    cpu_limit="200m",
                    memory_request="128Mi",
                    memory_limit="256Mi",
                )

    # --- connect_device ---

    def test_connect_device_success(self, plugin):
        mock_pod = _make_pod(phase="Running", pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            sandbox = plugin.connect_device("sb-0", "default")

        assert sandbox.sandbox_id == "sb-0"
        assert sandbox._namespace == "default"
        assert sandbox._pod is mock_pod

    def test_connect_device_not_found(self, plugin):
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.side_effect = ModApiException(status=404)

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="not found"):
                plugin.connect_device("sb-0", "default")

    def test_connect_device_other_error(self, plugin):
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.side_effect = ModApiException(status=500)

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="not found"):
                plugin.connect_device("sb-0", "default")

    # --- list_instances ---

    def test_list_instances_running(self, plugin):
        sts1 = MagicMock()
        sts1.metadata.name = "sts-1"
        sts1.spec.replicas = 2
        sts1.status.ready_replicas = 2

        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(
            items=[sts1]
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = plugin.list_instances("default")

        assert len(result) == 1
        assert result[0]["sandbox_id"] == "sts-1"
        assert result[0]["status"] == "RUNNING"
        assert result[0]["namespace"] == "default"
        assert result[0]["replicas"] == 2
        assert result[0]["ready_replicas"] == 2

    def test_list_instances_provisioning(self, plugin):
        sts = MagicMock()
        sts.metadata.name = "sts-1"
        sts.spec.replicas = 3
        sts.status.ready_replicas = 1

        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(items=[sts])

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = plugin.list_instances("default")

        assert result[0]["status"] == "PROVISIONING"

    def test_list_instances_destroyed(self, plugin):
        sts = MagicMock()
        sts.metadata.name = "sts-1"
        sts.spec.replicas = 0
        sts.status.ready_replicas = 0

        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(items=[sts])

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = plugin.list_instances("default")

        assert result[0]["status"] == "DESTROYED"

    def test_list_instances_spec_replicas_none(self, plugin):
        """When spec.replicas is None, treat as 0 -> DESTROYED."""
        sts = MagicMock()
        sts.metadata.name = "sts-1"
        sts.spec.replicas = None
        sts.status.ready_replicas = 0

        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(items=[sts])

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = plugin.list_instances("default")

        assert result[0]["status"] == "DESTROYED"
        assert result[0]["replicas"] == 0

    def test_list_instances_ready_replicas_none(self, plugin):
        """When ready_replicas is None, treat as 0."""
        sts = MagicMock()
        sts.metadata.name = "sts-1"
        sts.spec.replicas = 2
        sts.status.ready_replicas = None

        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(items=[sts])

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = plugin.list_instances("default")

        assert result[0]["status"] == "PROVISIONING"
        assert result[0]["ready_replicas"] == 0

    def test_list_instances_empty(self, plugin):
        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(items=[])

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = plugin.list_instances("default")

        assert result == []

    def test_list_instances_with_label_selector(self, plugin):
        sts = MagicMock()
        sts.metadata.name = "sts-1"
        sts.spec.replicas = 1
        sts.status.ready_replicas = 1

        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(items=[sts])

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = plugin.list_instances("default", label_selector="app=test")

        assert len(result) == 1
        call_args = mock_apps_api.list_namespaced_stateful_set.call_args.kwargs
        assert call_args["label_selector"] == "app=test"

    def test_list_instances_multiple(self, plugin):
        sts1 = MagicMock()
        sts1.metadata.name = "sts-1"
        sts1.spec.replicas = 2
        sts1.status.ready_replicas = 2

        sts2 = MagicMock()
        sts2.metadata.name = "sts-2"
        sts2.spec.replicas = 1
        sts2.status.ready_replicas = 0

        sts3 = MagicMock()
        sts3.metadata.name = "sts-3"
        sts3.spec.replicas = 0
        sts3.status.ready_replicas = 0

        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(
            items=[sts1, sts2, sts3]
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            result = plugin.list_instances("default")

        assert len(result) == 3
        assert result[0]["status"] == "RUNNING"
        assert result[1]["status"] == "PROVISIONING"
        assert result[2]["status"] == "DESTROYED"

    def test_list_instances_api_exception(self, plugin):
        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.side_effect = ModApiException(
            status=500
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            with pytest.raises(RuntimeError, match="list_instances failed"):
                plugin.list_instances("default")

    # --- resolve_ws_conn_info ---

    def test_resolve_ws_conn_info_success(self, plugin):
        mock_pod = _make_pod(phase="Running", pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = plugin.resolve_ws_conn_info(
                "sts-name--0", 8080, "/api/ws", "default"
            )

        assert result.ws_url == "ws://10.0.0.1:8080/api/ws"
        assert result.token == ""
        assert result.target == "10.0.0.1:8080"
        assert result.expires_at is not None

    def test_resolve_ws_conn_info_normalizes_path(self, plugin):
        mock_pod = _make_pod(pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = plugin.resolve_ws_conn_info(
                "sts-name--0", 8080, "ws/path", "default"
            )

        assert result.ws_url == "ws://10.0.0.1:8080/ws/path"

    def test_resolve_ws_conn_info_no_pod_ip(self, plugin):
        mock_pod = _make_pod(pod_ip=None)
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="IP not yet assigned"):
                plugin.resolve_ws_conn_info("sts-name--0", 8080, "/api/ws", "default")

    def test_resolve_ws_conn_info_api_exception(self, plugin):
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.side_effect = ModApiException(status=500)

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="resolve_ws_conn_info failed"):
                plugin.resolve_ws_conn_info("sts-name--0", 8080, "/api/ws", "default")

    # --- resolve_invoke_http_info ---

    def test_resolve_invoke_http_info_success(self, plugin):
        mock_pod = _make_pod(pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = plugin.resolve_invoke_http_info(
                "sts-name--0", 8080, "/api/health", "default"
            )

        assert result.http_url == "http://10.0.0.1:8080/api/health"
        assert result.token == ""

    def test_resolve_invoke_http_info_normalizes_path(self, plugin):
        mock_pod = _make_pod(pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            result = plugin.resolve_invoke_http_info(
                "sts-name--0", 8080, "health", "default"
            )

        assert result.http_url == "http://10.0.0.1:8080/health"

    def test_resolve_invoke_http_info_no_pod_ip(self, plugin):
        mock_pod = _make_pod(pod_ip=None)
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="IP not yet assigned"):
                plugin.resolve_invoke_http_info(
                    "sts-name--0", 8080, "/api/health", "default"
                )

    def test_resolve_invoke_http_info_api_exception(self, plugin):
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.side_effect = ModApiException(status=500)

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="resolve_invoke_http_info failed"):
                plugin.resolve_invoke_http_info(
                    "sts-name--0", 8080, "/api/health", "default"
                )

    # --- invoke_http_in_device ---

    def test_invoke_http_in_device_success(self, plugin):
        mock_pod = _make_pod(pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.content = b'{"ok": true}'

        mock_httpx_client = MagicMock()
        mock_httpx_client.request.return_value = mock_response

        with (
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
            patch("httpx.Client", return_value=mock_httpx_client),
        ):
            result = plugin.invoke_http_in_device(
                "sts-name--0", "GET", 8080, "/api/health", "default"
            )

        assert result["status_code"] == 200
        assert result["headers"] == {"Content-Type": "application/json"}
        assert base64.b64decode(result["body"]) == b'{"ok": true}'
        mock_httpx_client.close.assert_called_once()

    def test_invoke_http_in_device_with_query_string(self, plugin):
        mock_pod = _make_pod(pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.content = b""

        mock_httpx_client = MagicMock()
        mock_httpx_client.request.return_value = mock_response

        with (
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
            patch("httpx.Client", return_value=mock_httpx_client),
        ):
            result = plugin.invoke_http_in_device(
                "sts-name--0", "GET", 8080, "/api", "default", query_string="?foo=bar"
            )

        assert result["status_code"] == 200
        # Verify the URL was constructed with query string
        request_args = mock_httpx_client.request.call_args.kwargs
        assert "?foo=bar" in request_args["url"]

    def test_invoke_http_in_device_with_headers_and_body(self, plugin):
        mock_pod = _make_pod(pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.headers = {"X-Custom": "yes"}
        mock_response.content = b"created"

        mock_httpx_client = MagicMock()
        mock_httpx_client.request.return_value = mock_response

        with (
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
            patch("httpx.Client", return_value=mock_httpx_client),
        ):
            result = plugin.invoke_http_in_device(
                "sts-name--0",
                "POST",
                8080,
                "/api/create",
                "default",
                headers={"Content-Type": "application/json"},
                body=b'{"name": "test"}',
            )

        assert result["status_code"] == 201
        request_args = mock_httpx_client.request.call_args.kwargs
        assert request_args["method"] == "POST"
        assert request_args["headers"] == {"Content-Type": "application/json"}
        assert request_args["content"] == b'{"name": "test"}'

    def test_invoke_http_in_device_no_pod_ip(self, plugin):
        mock_pod = _make_pod(pod_ip=None)
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="IP not yet assigned"):
                plugin.invoke_http_in_device(
                    "sts-name--0", "GET", 8080, "/api", "default"
                )

    def test_invoke_http_in_device_api_exception(self, plugin):
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.side_effect = ModApiException(status=500)

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="invoke_http_in_device failed"):
                plugin.invoke_http_in_device(
                    "sts-name--0", "GET", 8080, "/api", "default"
                )

    def test_invoke_http_in_device_httpx_error(self, plugin):
        mock_pod = _make_pod(pod_ip="10.0.0.1")
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        mock_httpx_client = MagicMock()
        mock_httpx_client.request.side_effect = httpx.HTTPError("conn refused")

        with (
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
            patch("httpx.Client", return_value=mock_httpx_client),
        ):
            with pytest.raises(RuntimeError, match="invoke_http_in_device failed"):
                plugin.invoke_http_in_device(
                    "sts-name--0", "GET", 8080, "/api", "default"
                )

        mock_httpx_client.close.assert_called_once()

    # --- close ---

    def test_close(self, plugin):
        # Should be a no-op, not raise
        plugin.close()

    # --- update_outbound_operation_rule ---

    def test_update_outbound_operation_rule_success(self, plugin):
        with patch.object(plugin, "_patch_proxy_configmap") as mock_patch:
            plugin.update_outbound_operation_rule("my-sts", "default", "envoy: yaml")

        mock_patch.assert_called_once_with(
            "my-sts-proxy-rules", "default", "envoy: yaml"
        )

    def test_update_outbound_operation_rule_api_exception(self, plugin):
        with patch.object(
            plugin, "_patch_proxy_configmap", side_effect=ModApiException(status=500)
        ):
            with pytest.raises(
                RuntimeError, match="update_outbound_operation_rule failed"
            ):
                plugin.update_outbound_operation_rule(
                    "my-sts", "default", "envoy: yaml"
                )

    # --- _create_proxy_configmap ---

    def test_create_proxy_configmap_success(self, plugin):
        mock_core_api = MagicMock()

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            plugin._create_proxy_configmap("cm-1", "default", "envoy yaml")

        mock_core_api.create_namespaced_config_map.assert_called_once()
        call_args = mock_core_api.create_namespaced_config_map.call_args.kwargs
        assert call_args["namespace"] == "default"
        body = call_args["body"]
        assert body.data == {"envoy.yaml": "envoy yaml"}
        assert body.metadata.name == "cm-1"

    def test_create_proxy_configmap_api_exception(self, plugin):
        mock_core_api = MagicMock()
        mock_core_api.create_namespaced_config_map.side_effect = ModApiException(
            status=500
        )

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="create_configmap failed"):
                plugin._create_proxy_configmap("cm-1", "default", "envoy yaml")

    # --- _patch_proxy_configmap ---

    def test_patch_proxy_configmap_success(self, plugin):
        mock_core_api = MagicMock()

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            plugin._patch_proxy_configmap("cm-1", "default", "new yaml")

        mock_core_api.patch_namespaced_config_map.assert_called_once()
        call_args = mock_core_api.patch_namespaced_config_map.call_args.kwargs
        assert call_args["name"] == "cm-1"
        assert call_args["namespace"] == "default"
        assert call_args["body"] == {"data": {"envoy.yaml": "new yaml"}}

    def test_patch_proxy_configmap_404_fallback_to_create(self, plugin):
        """On 404, falls back to _create_proxy_configmap."""
        mock_core_api = MagicMock()
        mock_core_api.patch_namespaced_config_map.side_effect = ModApiException(
            status=404
        )

        with (
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
            patch.object(plugin, "_create_proxy_configmap") as mock_create,
        ):
            plugin._patch_proxy_configmap("cm-1", "default", "new yaml")

        mock_create.assert_called_once_with("cm-1", "default", "new yaml")

    def test_patch_proxy_configmap_non_404_error(self, plugin):
        mock_core_api = MagicMock()
        mock_core_api.patch_namespaced_config_map.side_effect = ModApiException(
            status=500
        )

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            with pytest.raises(RuntimeError, match="patch_configmap failed"):
                plugin._patch_proxy_configmap("cm-1", "default", "new yaml")

    # --- _parse_pod_name ---

    def test_parse_pod_name_valid(self, plugin):
        assert plugin._parse_pod_name("my-sts--0") == "my-sts-0"

    def test_parse_pod_name_valid_multi_dash(self, plugin):
        assert plugin._parse_pod_name("my-sts-name--3") == "my-sts-name-3"

    def test_parse_pod_name_no_separator(self, plugin):
        with pytest.raises(RuntimeError, match="Invalid paas_device_id format"):
            plugin._parse_pod_name("no-separator")

    def test_parse_pod_name_invalid_ordinal(self, plugin):
        with pytest.raises(RuntimeError, match="Invalid paas_device_id ordinal"):
            plugin._parse_pod_name("sts--abc")

    def test_parse_pod_name_negative_ordinal(self, plugin):
        with pytest.raises(RuntimeError, match="must be >= 0"):
            plugin._parse_pod_name("sts---1")

    def test_parse_pod_name_zero_ordinal(self, plugin):
        assert plugin._parse_pod_name("sts--0") == "sts-0"

    def test_parse_pod_name_large_ordinal(self, plugin):
        assert plugin._parse_pod_name("sts--999") == "sts-999"


# ---------------------------------------------------------------------------
# Tests for RealK8sSandboxPlugin with _ensure_client delegation
# ---------------------------------------------------------------------------


class TestRealK8sSandboxPluginClientDelegation:
    """Test that methods use _ensure_client and delegate to client_manager."""

    def test_create_device_calls_ensure_client(self):
        creds = _make_credentials()
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        plugin = RealK8sSandboxPlugin(creds, cm)

        mock_apps_api = MagicMock()
        mock_apps_api.read_namespaced_stateful_set.side_effect = ModApiException(
            status=404
        )

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            plugin.create_device(
                template_id=1,
                template_uuid="uuid-1",
                tenant_name="tenant-1",
                namespace="default",
                image="test:latest",
                cpu_request="100m",
                cpu_limit="200m",
                memory_request="128Mi",
                memory_limit="256Mi",
            )

        cm.get_or_create_client.assert_called_once_with(creds)

    def test_connect_device_calls_ensure_client(self):
        creds = _make_credentials()
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        plugin = RealK8sSandboxPlugin(creds, cm)

        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = _make_pod()

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            plugin.connect_device("sb-0", "default")

        cm.get_or_create_client.assert_called_once_with(creds)

    def test_list_instances_calls_ensure_client(self):
        creds = _make_credentials()
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        plugin = RealK8sSandboxPlugin(creds, cm)

        mock_apps_api = MagicMock()
        mock_apps_api.list_namespaced_stateful_set.return_value = MagicMock(items=[])

        with patch.object(_mod, "AppsV1Api", return_value=mock_apps_api):
            plugin.list_instances("default")

        cm.get_or_create_client.assert_called_once_with(creds)

    def test_resolve_ws_conn_info_calls_ensure_client(self):
        creds = _make_credentials()
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        plugin = RealK8sSandboxPlugin(creds, cm)

        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = _make_pod(pod_ip="1.2.3.4")

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            plugin.resolve_ws_conn_info("sts--0", 8080, "/ws", "default")

        cm.get_or_create_client.assert_called_once_with(creds)

    def test_resolve_invoke_http_info_calls_ensure_client(self):
        creds = _make_credentials()
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        plugin = RealK8sSandboxPlugin(creds, cm)

        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = _make_pod(pod_ip="1.2.3.4")

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            plugin.resolve_invoke_http_info("sts--0", 8080, "/health", "default")

        cm.get_or_create_client.assert_called_once_with(creds)

    def test_invoke_http_in_device_calls_ensure_client(self):
        creds = _make_credentials()
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        plugin = RealK8sSandboxPlugin(creds, cm)

        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = _make_pod(pod_ip="1.2.3.4")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.content = b""
        mock_httpx_client = MagicMock()
        mock_httpx_client.request.return_value = mock_response

        with (
            patch.object(_mod, "CoreV1Api", return_value=mock_core_api),
            patch("httpx.Client", return_value=mock_httpx_client),
        ):
            plugin.invoke_http_in_device("sts--0", "GET", 8080, "/api", "default")

        cm.get_or_create_client.assert_called_once_with(creds)

    def test_create_proxy_configmap_calls_ensure_client(self):
        creds = _make_credentials()
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        plugin = RealK8sSandboxPlugin(creds, cm)

        mock_core_api = MagicMock()

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            plugin._create_proxy_configmap("cm-1", "default", "yaml")

        cm.get_or_create_client.assert_called_once_with(creds)

    def test_patch_proxy_configmap_calls_ensure_client(self):
        creds = _make_credentials()
        cm = MagicMock()
        cm.get_or_create_client.return_value = MagicMock()
        plugin = RealK8sSandboxPlugin(creds, cm)

        mock_core_api = MagicMock()

        with patch.object(_mod, "CoreV1Api", return_value=mock_core_api):
            plugin._patch_proxy_configmap("cm-1", "default", "yaml")

        cm.get_or_create_client.assert_called_once_with(creds)
