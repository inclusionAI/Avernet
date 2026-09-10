"""BaasPublishPoller — 轮询 BaaS publish 状态并驱动 device 状态机。

复用自 DesktopBotService._poll_publish_progress 模式。供 LocalDeviceService、
后续 DesktopBotService 共用。

触发链：
    publish_id (BaaS) → 轮询 SUCCESS → device_service.report_device_alive(skip_token_check=True)
                          → 父类内自动 PENDING→ACTIVE + 发布 DeviceActivatedEvent
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Callable

from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_service import DeviceService
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

logger = get_logger()


class BaasPublishPoller:
    """后台 daemon thread 轮询 BaaS publish 进度。"""

    DEFAULT_POLL_INTERVAL_SECONDS = 5
    DEFAULT_POLL_TIMEOUT_SECONDS = 180

    def __init__(
        self,
        baas_service: "BaasService",
        device_service_provider: Callable[[], "DeviceService"],
        poll_interval_seconds: float | None = None,
        poll_timeout_seconds: float | None = None,
    ) -> None:
        # device_service_provider 用 lazy provider 是因为 DeviceService 自身要
        # 注入此 poller，直接传 DeviceService 会形成构造期循环依赖。
        self._baas_service = baas_service
        self._device_service_provider = device_service_provider
        self._poll_interval = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else self.DEFAULT_POLL_INTERVAL_SECONDS
        )
        self._poll_timeout = (
            poll_timeout_seconds
            if poll_timeout_seconds is not None
            else self.DEFAULT_POLL_TIMEOUT_SECONDS
        )
        self._stop_event = threading.Event()
        self._threads_lock = threading.Lock()
        self._threads: set[threading.Thread] = set()

    def start(self, *, publish_id: str, device_id: str, binding_id: int) -> None:
        """启动后台 thread 轮询，daemon=True 不阻塞进程退出。"""
        thread = threading.Thread(
            target=bind_current_avernet_tenant(self._run_tracked),
            args=(publish_id, device_id, binding_id),
            daemon=True,
            name=f"baas-poll-{publish_id}",
        )
        with self._threads_lock:
            if self._stop_event.is_set():
                raise RuntimeError("BaasPublishPoller is shut down")
            self._threads.add(thread)
            thread.start()

    def shutdown(self, *, timeout_seconds: float | None = None) -> bool:
        """Stop accepting work and wait for every active poller thread."""
        self._stop_event.set()
        deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        with self._threads_lock:
            threads = tuple(self._threads)
        for thread in threads:
            remaining = (
                max(0.0, deadline - time.monotonic())
                if deadline is not None
                else None
            )
            thread.join(remaining)
        with self._threads_lock:
            return not self._threads

    def _run_tracked(
        self, publish_id: str, device_id: str, binding_id: int
    ) -> None:
        try:
            self._poll(publish_id, device_id, binding_id)
        finally:
            with self._threads_lock:
                self._threads.discard(threading.current_thread())

    def _poll(self, publish_id: str, device_id: str, binding_id: int) -> None:
        try:
            start = time.monotonic()
            device_service = self._device_service_provider()

            while (time.monotonic() - start) < self._poll_timeout:
                if self._stop_event.wait(self._poll_interval):
                    return
                try:
                    progress = self._baas_service.get_publish_progress(publish_id)
                    status = (progress or {}).get("status", "")
                except Exception as e:
                    logger.warning(
                        f"[BaasPublishPoller] get_publish_progress failed (will retry): "
                        f"publish_id={publish_id} error={e}"
                    )
                    continue

                if self._stop_event.is_set():
                    return
                if status == "SUCCESS":
                    try:
                        device_service.report_device_alive(
                            device_id=device_id, token="", skip_token_check=True
                        )
                    except Exception as e:
                        logger.warning(
                            f"[BaasPublishPoller] report_device_alive failed, attempting fallback: "
                            f"device_id={device_id} error={e}"
                        )
                        fallback = getattr(
                            device_service, "_mark_alive_active_fallback", None
                        )
                        if callable(fallback):
                            try:
                                fallback(binding_id=binding_id)
                            except Exception as fallback_err:
                                logger.error(
                                    f"[BaasPublishPoller] ACTIVE_FALLBACK also failed: "
                                    f"binding_id={binding_id} error={fallback_err}"
                                )
                        else:
                            logger.warning(
                                "[BaasPublishPoller] device_service has no "
                                "_mark_alive_active_fallback helper; device may "
                                "remain PENDING"
                            )
                    return

                if status == "FAILED":
                    device_service._mark_service_start_failed(
                        binding_id=binding_id,
                        error=f"BaaS publish FAILED: publish_id={publish_id}",
                    )
                    return

            if self._stop_event.is_set():
                return
            # 循环退出 = 超时
            device_service._mark_service_start_failed(
                binding_id=binding_id,
                error=f"BaaS publish timeout after {self._poll_timeout}s: publish_id={publish_id}",
            )
        except Exception as e:
            logger.exception(
                f"[BaasPublishPoller] unhandled error in _poll thread: "
                f"publish_id={publish_id} device_id={device_id} error={e}"
            )
