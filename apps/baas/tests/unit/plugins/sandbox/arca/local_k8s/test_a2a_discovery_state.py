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
        assert shared.inflight == {}

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
        assert shared.inflight == {}
        assert shared.threads == {}

    @patch("kubernetes.client.CoreV1Api")
    def test_destroy_during_inflight_drops_late_write_back(
        self, mock_core_cls, mock_client
    ) -> None:
        """销毁落在发现窗口内:撤销的是旧任务**回写资格**而非额度——旧任务
        照常跑完并自行归还额度,其迟到回写按代际校验被拒收;真实等待线程
        结束(保存线程引用 join)后的断言才可信(评审指正:以状态位轮询
        当完成证据不牢)。"""
        shared = A2aUrlDiscoveryState()
        gate = threading.Event()
        started: list[str] = []

        def _blocked_read(**kwargs):
            started.append(kwargs.get("name", "?"))
            gate.wait(timeout=5.0)
            return _service_with_node_port(30097)

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            plugin = _build_plugin(mock_client, shared)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.side_effect = _blocked_read

            conn = plugin.resolve_ws_conn_info(
                paas_device_id="late-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert conn.a2a_base_url == ""

            key = ("default", "late-abc-svc")
            self._poll_until(lambda: key in shared.inflight)
            stale_thread = shared.threads[key]

            # Service 在发现查证途中被销毁:撤销回写资格,额度不提前归还
            shared.invalidate(key)
            assert key in shared.inflight, "invalidate 不得归还仍在执行任务的额度"

            # 旧任务在途期间同 key 再签发:按 in-flight 去重坐等,不重查
            again = plugin.resolve_ws_conn_info(
                paas_device_id="late-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert again.a2a_base_url == ""
            assert len(started) == 1

            # 旧任务携带旧快照返回:代际校验拒收,不复活已销毁设备条目
            gate.set()
            stale_thread.join(timeout=3.0)
            assert not stale_thread.is_alive()
            self._poll_until(lambda: key not in shared.inflight)
            assert key not in shared.node_port_cache
            assert len(started) == 1  # 迟到回写被拒收,全程只此一次查询

            # 旧任务归位后,新一轮签发重新发现并落账新事实
            gate.set()  # 幂等,后续读不再阻塞
            mock_core.read_namespaced_service.side_effect = None
            fresh = _service_with_node_port(30098)
            mock_core.read_namespaced_service.return_value = fresh
            refreshed = plugin.resolve_ws_conn_info(
                paas_device_id="late-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert refreshed.a2a_base_url == ""
            plugin.a2a_discovery_join("late-abc")
            landed = plugin.resolve_ws_conn_info(
                paas_device_id="late-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert landed.a2a_base_url == (
                "http://late-abc-svc.stable.alipay.net:30098"
            )

    @patch("kubernetes.client.CoreV1Api")
    def test_destroy_keeps_quota_bounded_concurrency(
        self, mock_core_cls, mock_client
    ) -> None:
        """销毁不归还额度(评审 P2-1):满额后在途任务未结束时销毁其设备,
        再签发其他 key 不得突破并发上限——实际并发恒 ≤ 8,额度只在任务
        真正结束时归还。"""
        shared = A2aUrlDiscoveryState()
        cap = _A2A_URL_DISCOVERY_MAX_CONCURRENCY
        gate = threading.Event()
        started: list[str] = []

        def _blocked_read(**kwargs):
            started.append(kwargs.get("name", "?"))
            gate.wait(timeout=5.0)
            return _service_with_node_port(30097)

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            plugins = [_build_plugin(mock_client, shared) for _ in range(cap + 4)]
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.side_effect = _blocked_read

            # 满额:cap 个设备并签,发现任务全部阻塞在 kube 查询上
            for idx in range(cap):
                plugins[idx].resolve_ws_conn_info(
                    paas_device_id=f"hold-{idx}-abc",
                    port=8080,
                    path="/api/openclaw/ws",
                )
            self._poll_until(lambda: len(started) == cap)

            # 销毁其中一个设备:额度仍在被(被撤销资格但仍在跑的)任务持有
            shared.invalidate(("default", "hold-0-abc-svc"))
            assert len(shared.inflight) == cap

            # 其他 key 的新签发:不突破上限,立即降级空地址
            degraded = plugins[cap].resolve_ws_conn_info(
                paas_device_id="extra-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert degraded.ws_url
            assert degraded.a2a_base_url == ""
            time.sleep(0.2)  # 若额度被误归还,此处第 9 个查询会漏出来
            assert len(started) == cap

            # 任务真正结束后额度归还,负缓存承载的"稍后再试"可恢复
            gate.set()
            self._poll_until(lambda: shared.inflight == {})
            shared.node_port_cache.pop(("default", "extra-abc-svc"), None)
            plugins[cap].resolve_ws_conn_info(
                paas_device_id="extra-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            self._poll_until(lambda: len(started) == cap + 1)

    @patch("kubernetes.client.CoreV1Api")
    def test_positive_cache_expires_and_revalidates_across_workers(
        self, mock_core_cls, mock_client
    ) -> None:
        """正缓存有限有效期(评审 P2-3 跨 worker):本进程 destroy() 不是
        唯一保障——正常删除落在别的 worker(DI Singleton 仅进程内共享)
        或带外时,本 worker 的旧快照至多陈旧一个 TTL,过期后不再被交付,
        转后台发现再验证;旧地址在过期后绝不返回。"""
        import time as _time

        # 独立的 worker A 状态 + 短 TTL 让过期在测试里可达
        worker_a = A2aUrlDiscoveryState(positive_ttl_s=0.15)

        reads: list[str] = []

        def _read(**kwargs):
            reads.append(kwargs.get("name", "?"))
            if len(reads) == 1:
                return _service_with_node_port(30095)
            # Service 已被其他 worker 删除:真实 kube 语义是 404,这里以
            # 异常模拟(插件会落负缓存)
            raise RuntimeError("404 not found")

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            plugin = _build_plugin(mock_client, worker_a)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.side_effect = _read

            first = plugin.resolve_ws_conn_info(
                paas_device_id="worker-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert first.a2a_base_url == ""
            plugin.a2a_discovery_join("worker-abc")
            key = ("default", "worker-abc-svc")

            within_ttl = plugin.resolve_ws_conn_info(
                paas_device_id="worker-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            # TTL 内旧快照仍交付(有界陈旧窗口,删除可能尚未发生)
            assert within_ttl.a2a_base_url == (
                "http://worker-abc-svc.stable.alipay.net:30095"
            )

            # 与其他 worker 的删除交错:worker A 不知情,靠 TTL 兜底
            _time.sleep(0.2)

            expired = plugin.resolve_ws_conn_info(
                paas_device_id="worker-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert expired.a2a_base_url == ""  # 过期旧值不再交付,零等待
            assert key not in worker_a.node_port_cache or isinstance(
                worker_a.node_port_cache.get(key), tuple
            )
            plugin.a2a_discovery_join("worker-abc")
            after = plugin.resolve_ws_conn_info(
                paas_device_id="worker-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            # 再验证结果(删除→负缓存)就位后,旧地址 30095 绝不再返回
            assert after.a2a_base_url == ""
            assert len(reads) == 2  # 全程恰好两次 kube 查询

    @patch("kubernetes.client.CoreV1Api")
    def test_expired_history_is_swept_on_next_schedule(
        self, mock_core_cls, mock_client
    ) -> None:
        """不再被访问的历史也不积累(评审 P2 回收清扫):acquire 时的全表
        过期清扫让历史设备条目随任何一次新调度出清——应用级缓存受
        「最近有效期窗口内的设备」约束,而非历史设备总量。"""
        history = A2aUrlDiscoveryState(positive_ttl_s=0.05)

        for idx in range(50):
            history.note_warm(("default", f"old-{idx}-svc"), 30100 + idx)
        time.sleep(0.15)  # 历史条目全部过期

        with patch.dict(os.environ, _LIFECYCLE_ENV, clear=True):
            plugin = _build_plugin(mock_client, history)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.return_value = _service_with_node_port(
                30099
            )

            assert len(history.node_port_cache) == 50  # 过期但未清扫前仍在
            conn = plugin.resolve_ws_conn_info(
                paas_device_id="sweep-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            assert conn.a2a_base_url == ""

            # 新调度触发的全表清扫:50 条历史出清,只剩本设备
            plugin.a2a_discovery_join("sweep-abc")
            assert set(history.node_port_cache) == {("default", "sweep-abc-svc")}
            assert history.inflight == {}
            assert history.threads == {}


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
