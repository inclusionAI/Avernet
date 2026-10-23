"""A2A 发现状态的跨请求生命周期(review P2:实例级状态不跨请求)。

生产签发链路是 ``PaasServiceFactory.create → local_k8s_plugin_factory._build
→ LocalK8sArcaSandboxPlugin(...)`` ——每次 WS 签发都 new 一个新插件实例(逐
请求注入 credentials)。发现缓存/负缓存/in-flight/并发额度若挂在插件实例
上就只能活一个请求:「首次为空、后续自然带上 A2A 地址」在真实调用链中不
成立,进程级并发上限也随实例增殖失效。

这组测试只走真实工厂生命周期:共享 ``A2aUrlDiscoveryState`` 经工厂烘焙进
每个实例,凭证仍然各是各的。"""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.device_manage import ArcaCredentials, ResourceSpecification
from secbaas.community.plugins.sandbox.arca.local_k8s import (
    LocalK8sArcaSandboxPlugin,
    local_k8s_plugin_factory,
)
from secbaas.community.plugins.sandbox.arca.local_k8s._a2a_discovery import (
    _A2A_URL_DISCOVERY_MAX_CONCURRENCY,
    A2aUrlDiscoveryState,
)

_TEMPLATE = "http://{service_name}.stable.alipay.net:{node_port}"
_LIFECYCLE_ENV = {
    "LOCAL_K8S_KUBECONFIG": "in-cluster",
    "LOCAL_K8S_NAMESPACE": "default",
    "LOCAL_K8S_IMAGE": "bot-runtime:latest",
    "LOCAL_K8S_CONTAINER_PORT": "8080",
    "LOCAL_K8S_A2A_URL_TEMPLATE": _TEMPLATE,
}

_DEVICES = 12  # > _A2A_URL_DISCOVERY_MAX_CONCURRENCY,超额走降级面


@pytest.fixture
def mock_client():
    return MagicMock()


def _make_credentials() -> ArcaCredentials:
    return ArcaCredentials(
        template_id=1,
        template_uuid="tpl-lifecycle",
        tenant_name="test-tenant",
        base_url="",
        api_key="",
    )


def _service_with_node_port(node_port: int) -> MagicMock:
    svc = MagicMock()
    svc.spec.ports = [MagicMock(node_port=node_port)]
    return svc


def _build_plugin(
    mock_client: MagicMock, shared: A2aUrlDiscoveryState
) -> LocalK8sArcaSandboxPlugin:
    """经真实工厂烘焙共享 state(而非直建插件,直建走默认独立 state)。

    ``arca_utils`` 与生产 wiring 一并烘焙:没有它 resolve_ws_conn_info 走
    out-of-cluster 分支,A2A 发现面完全不参与。"""
    build = local_k8s_plugin_factory(arca_utils=MagicMock(), discovery_state=shared)
    plugin = build(_make_credentials())
    plugin._client_manager.get_client = lambda *a, **kw: mock_client
    return plugin


class TestA2aDiscoverySharedLifecycle:
    @patch("kubernetes.client.CoreV1Api")
    def test_second_issuance_reuses_state_across_instances(
        self, mock_core_cls, mock_client
    ) -> None:
        """两次独立签发(两个插件实例)共享同一次发现结果:第一实例后台
        发现到的 NodePort,第二实例零 kube 查询直接交付。"""
        shared = A2aUrlDiscoveryState()

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            first_plugin = _build_plugin(mock_client, shared)
            second_plugin = _build_plugin(mock_client, shared)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.return_value = _service_with_node_port(
                30095
            )

            first = first_plugin.resolve_ws_conn_info(
                paas_device_id="cycle-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert first.a2a_base_url == ""  # 未就绪:空地址立即返回
            first_plugin.a2a_discovery_join("cycle-abc")

            second = second_plugin.resolve_ws_conn_info(
                paas_device_id="cycle-abc",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert second.a2a_base_url == ("http://cycle-abc-svc.stable.alipay.net:30095")
        assert mock_core.read_namespaced_service.call_count == 1

    @patch("kubernetes.client.CoreV1Api")
    def test_negative_cache_dedupes_across_instances(
        self, mock_core_cls, mock_client
    ) -> None:
        """负缓存 TTL 内跨请求不重查:第一实例的失败探查对第二实例同样
        生效——A2A 面照旧空串降级,零新查询、零新线程。"""
        shared = A2aUrlDiscoveryState()

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            first_plugin = _build_plugin(mock_client, shared)
            second_plugin = _build_plugin(mock_client, shared)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.side_effect = RuntimeError("api down")

            first = first_plugin.resolve_ws_conn_info(
                paas_device_id="neg-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            first_plugin.a2a_discovery_join("neg-abc")
            second = second_plugin.resolve_ws_conn_info(
                paas_device_id="neg-abc",
                port=8080,
                path="/api/openclaw/ws",
            )

            assert first.a2a_base_url == ""
            assert second.a2a_base_url == ""

            # 一次失败探查 + 第二个实例(新请求)零重复查询
            assert mock_core.read_namespaced_service.call_count == 1

    @patch("kubernetes.client.CoreV1Api")
    def test_concurrency_cap_bounds_total_discovery_across_instances(
        self, mock_core_cls, mock_client
    ) -> None:
        """多实例并发签发时,发现并发受进程级上限约束:超额请求立
        即降级(空地址 + 短负缓存),WS 签发不受影响;总量到顶后不再
        突破,容量释放 + 负缓存出清后可恢复。"""
        shared = A2aUrlDiscoveryState()
        started: list[str] = []
        gate = threading.Event()

        def _blocked_read(**kwargs):
            started.append(kwargs.get("name", "?"))
            gate.wait(timeout=5.0)
            return _service_with_node_port(30099)

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            plugins = [_build_plugin(mock_client, shared) for _ in range(_DEVICES)]
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.side_effect = _blocked_read

            conns = []
            for idx, plugin in enumerate(plugins):
                conns.append(
                    plugin.resolve_ws_conn_info(
                        paas_device_id=f"cap-{idx}-abc",
                        port=8080,
                        path="/api/openclaw/ws",
                    )
                )

            # WS 签发全部照常返回,无一被 A2A 发现拖住
            assert all(c.ws_url for c in conns)
            assert all(c.a2a_base_url == "" for c in conns)

            # 在途发现恰好打满并发上限;上限被共享约束,不随实例数增殖
            deadline = time.monotonic() + 2.0
            while len(started) < _A2A_URL_DISCOVERY_MAX_CONCURRENCY and (
                time.monotonic() < deadline
            ):
                time.sleep(0.01)
            time.sleep(0.2)  # 若有第 9 个调度漏网,在此暴露
            assert len(started) == _A2A_URL_DISCOVERY_MAX_CONCURRENCY

            # 超额设备落入负缓存(把"稍后再试"承载在数据里,不留 in-flight)
            degraded_keys = {
                ("default", f"cap-{idx}-abc-svc")
                for idx in range(
                    _A2A_URL_DISCOVERY_MAX_CONCURRENCY,
                    _DEVICES,
                )
            }
            for key in degraded_keys:
                entry = shared.node_port_cache.get(key)
                assert isinstance(entry, tuple) and entry[0] == "error", (
                    f"degraded key {key} missing negative cache: {entry}"
                )

            # 容量释放(在途发现完成释放额度)+ 负缓存出清后,超额设备恢复
            gate.set()
            for idx, plugin in enumerate(plugins):
                plugin.a2a_discovery_join(f"cap-{idx}-abc")
            for key in degraded_keys:
                shared.node_port_cache.pop(key, None)
            recovered_plugin = plugins[_A2A_URL_DISCOVERY_MAX_CONCURRENCY]
            recovered_plugin.resolve_ws_conn_info(
                paas_device_id=f"cap-{_A2A_URL_DISCOVERY_MAX_CONCURRENCY}-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            recovered_plugin.a2a_discovery_join(
                f"cap-{_A2A_URL_DISCOVERY_MAX_CONCURRENCY}-abc"
            )
            recovered = recovered_plugin.resolve_ws_conn_info(
                paas_device_id=f"cap-{_A2A_URL_DISCOVERY_MAX_CONCURRENCY}-abc",
                port=8080,
                path="/api/openclaw/ws",
            )

            assert recovered.a2a_base_url == (
                f"http://cap-{_A2A_URL_DISCOVERY_MAX_CONCURRENCY}-abc-svc"
                ".stable.alipay.net:30099"
            )


class TestA2aDiscoveryReclamation:
    """应用级共享状态的回收(review P2 回收机制)。

    状态提升为应用级后,历史必须受控:线程表不能只靠诊断 join 清出、
    缓存条目随 Service 生命周期失效、A2A 关闭的普通创建零状态落账。"""

    def _poll_until(self, predicate, deadline_s: float = 3.0) -> None:
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        # 到点后仍不满足,留给断言给出失败信息
        assert predicate(), "condition not reached within deadline"

    @patch("kubernetes.client.CoreV1Api")
    def test_finished_discovery_frees_thread_record_without_diagnostic_join(
        self, mock_core_cls, mock_client
    ) -> None:
        """自然收尾即清理:发现完成后线程记录与额度自行释放,不依赖
        诊断 seam(a2a_discovery_join),诊断清理缺席时 threads 表收敛。"""
        shared = A2aUrlDiscoveryState()

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            plugin = _build_plugin(mock_client, shared)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.return_value = _service_with_node_port(
                30096
            )

            conn = plugin.resolve_ws_conn_info(
                paas_device_id="converge-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert conn.a2a_base_url == ""  # 未就绪:空地址,后台发现出队

            key = ("default", "converge-abc-svc")
            # 不调用任何诊断 join——任务自然结束后记录必须自行收敛
            self._poll_until(lambda: isinstance(shared.node_port_cache.get(key), int))
            self._poll_until(lambda: key not in shared.threads)

            assert key not in shared.inflight
            assert shared.threads == {}

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_destroy_invalidates_history_and_cache_stays_bounded_across_batches(
        self, mock_core_cls, mock_apps_cls, mock_client
    ) -> None:
        """批次创建→销毁后状态受控:销毁 Service 即失效该设备的发现
        缓存与线程记录,历史设备总量不残留——按当前活跃设备量而非
        历史总量累积。"""
        shared = A2aUrlDiscoveryState()
        batches = 5

        with (
            patch.dict(
                os.environ,
                {
                    **_LIFECYCLE_ENV,
                    "LOCAL_K8S_OUTBOUND_RULE": "",
                },
                clear=True,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s"
                "._sandbox_plugin._resolve_kubeconfig",
                return_value="apiVersion: v1\nkind: Config",
            ),
        ):
            plugin = _build_plugin(mock_client, shared)
            mock_apps = MagicMock()
            mock_apps_cls.return_value = mock_apps
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core

            mock_pod = MagicMock()
            mock_pod.status.phase = "Running"
            mock_pod.metadata.name = "tpl-xyz-abc"
            mock_core.list_namespaced_pod.return_value.items = [mock_pod]

            for i in range(batches):
                mock_core.create_namespaced_service.return_value = (
                    _service_with_node_port(30090 + i)
                )
                sandbox = plugin.create_sync_sandbox(
                    template_id=f"tpl-batch-{i}",
                    envs={},
                    resource_spec=ResourceSpecification(cpu=1, memory=2),
                    image="bot-runtime:tag",
                )
                key = ("default", f"{sandbox.sandbox_id}-svc")
                # create 预热 + 历史负残留都在,批次中途最多一个活跃设备的记录
                assert isinstance(shared.node_port_cache[key], int)
                assert len(shared.node_port_cache) == 1

                sandbox.destroy()
                # 销毁即失效:该设备的缓存与线程记录不存活于应用级状态
                assert key not in shared.node_port_cache
                assert key not in shared.threads
                assert key not in shared.inflight

        assert shared.node_port_cache == {}
        assert shared.threads == {}
        assert shared.inflight == set()

    @patch("kubernetes.client.CoreV1Api")
    def test_a2a_disabled_creation_accumulates_no_discovery_state(
        self, mock_core_cls, mock_client
    ) -> None:
        """A2A 关闭(模板空)时,普通创建流程零发现状态落账:预热按
        模板门控,创建/销毁来回跑不动共享状态的分毫。"""
        shared = A2aUrlDiscoveryState()

        with (
            patch.dict(
                os.environ,
                {
                    **_LIFECYCLE_ENV,
                    "LOCAL_K8S_A2A_URL_TEMPLATE": "",
                },
                clear=True,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s"
                "._sandbox_plugin._resolve_kubeconfig",
                return_value="apiVersion: v1\nkind: Config",
            ),
        ):
            plugin = _build_plugin(mock_client, shared)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.create_namespaced_service.return_value = _service_with_node_port(
                30099
            )

            for i in range(100):
                plugin._create_service(
                    deployment_name=f"dead-{i}-abc", container_port=20003
                )

        assert shared.node_port_cache == {}
        assert shared.inflight == set()
        assert shared.threads == {}

    @patch("kubernetes.client.CoreV1Api")
    def test_destroy_during_inflight_discovery_drops_late_write_back(
        self, mock_core_cls, mock_client
    ) -> None:
        """销毁落在发现窗口内:迟到的发现回写不得复活已销毁设备的
        缓存条目(invalidate 与在途收尾按同一把锁协调)。"""
        shared = A2aUrlDiscoveryState()
        gate = threading.Event()

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            plugin = _build_plugin(mock_client, shared)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core

            def _blocked_read(**kwargs):
                gate.wait(timeout=5.0)
                return _service_with_node_port(30097)

            mock_core.read_namespaced_service.side_effect = _blocked_read

            conn = plugin.resolve_ws_conn_info(
                paas_device_id="late-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert conn.a2a_base_url == ""

            key = ("default", "late-abc-svc")
            self._poll_until(lambda: key in shared.inflight)

            # Service 在发现查证途中被销毁 → 生命周期失效先行
            shared.invalidate(key)
            gate.set()
            self._poll_until(
                lambda: key not in shared.inflight and key not in shared.threads
            )

            # 迟到回写被丢弃:已销毁设备的历史记录不复活
            assert key not in shared.node_port_cache


class TestCompositionWiring:
    """组合入口必须把共享 state 以 Singleton 注入 local_k8s 工厂。"""

    def test_container_bakes_shared_discovery_state_into_every_build(self):
        from secbaas.community.bootstrap.plugins import PluginContainer

        container = PluginContainer()
        container.config.from_dict(
            {"plugins": {"secret": "stub", "sandbox": {"arca": "local_k8s"}}}
        )
        build = container.arca_sandbox_plugin_factory()
        first = build(_make_credentials())
        second = build(_make_credentials())

        assert isinstance(first._a2a_discovery, A2aUrlDiscoveryState)
        # 每次签发一个新实例,但发现状态是同一份(进程级,非请求级)
        assert first is not second
        assert first._a2a_discovery is second._a2a_discovery
