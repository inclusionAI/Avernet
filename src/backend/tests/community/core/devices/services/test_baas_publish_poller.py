"""Tests for BaasPublishPoller — 轮询 BaaS publish 状态、触发 PENDING→ACTIVE."""
from unittest.mock import MagicMock
import time
import pytest

from agentclaw.community.core.devices.services.baas_publish_poller import BaasPublishPoller


@pytest.fixture
def fake_baas():
    """Fake BaasService — 可配置 publish 状态序列。"""
    m = MagicMock()
    m.get_publish_progress.return_value = {"status": "SUCCESS"}
    return m


@pytest.fixture
def fake_device_service():
    """Fake DeviceService — 验证 report_device_alive / _mark_service_start_failed 是否被调。"""
    m = MagicMock()
    return m


def test_poll_success_triggers_report_device_alive(fake_baas, fake_device_service):
    poller = BaasPublishPoller(
        baas_service=fake_baas,
        device_service_provider=lambda: fake_device_service,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=1.0,
    )

    poller.start(publish_id="pub-001", device_id="dev-001", binding_id=42)

    # wait for thread to finish (small budget)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if fake_device_service.report_device_alive.called:
            break
        time.sleep(0.02)

    fake_device_service.report_device_alive.assert_called_once_with(
        device_id="dev-001", token="", skip_token_check=True
    )
    fake_device_service._mark_service_start_failed.assert_not_called()


def test_poll_failed_marks_service_start_failed(fake_baas, fake_device_service):
    fake_baas.get_publish_progress.return_value = {"status": "FAILED"}

    poller = BaasPublishPoller(
        baas_service=fake_baas,
        device_service_provider=lambda: fake_device_service,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=1.0,
    )
    poller.start(publish_id="pub-002", device_id="dev-002", binding_id=43)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if fake_device_service._mark_service_start_failed.called:
            break
        time.sleep(0.02)

    fake_device_service._mark_service_start_failed.assert_called_once()
    call_kwargs = fake_device_service._mark_service_start_failed.call_args.kwargs
    assert call_kwargs["binding_id"] == 43
    assert "FAILED" in call_kwargs["error"]
    fake_device_service.report_device_alive.assert_not_called()


def test_poll_timeout_marks_service_start_failed(fake_baas, fake_device_service):
    fake_baas.get_publish_progress.return_value = {"status": "PENDING"}

    poller = BaasPublishPoller(
        baas_service=fake_baas,
        device_service_provider=lambda: fake_device_service,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.05,  # 极短超时
    )
    poller.start(publish_id="pub-003", device_id="dev-003", binding_id=44)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if fake_device_service._mark_service_start_failed.called:
            break
        time.sleep(0.02)

    fake_device_service._mark_service_start_failed.assert_called_once()
    assert "timeout" in fake_device_service._mark_service_start_failed.call_args.kwargs["error"].lower()


def test_poll_progress_exception_retries(fake_baas, fake_device_service):
    """get_publish_progress 抛异常时继续下一轮，最终 SUCCESS。"""
    fake_baas.get_publish_progress.side_effect = [
        RuntimeError("transient network error"),
        {"status": "SUCCESS"},
    ]

    poller = BaasPublishPoller(
        baas_service=fake_baas,
        device_service_provider=lambda: fake_device_service,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=1.0,
    )
    poller.start(publish_id="pub-004", device_id="dev-004", binding_id=45)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if fake_device_service.report_device_alive.called:
            break
        time.sleep(0.02)

    fake_device_service.report_device_alive.assert_called_once()
    assert fake_baas.get_publish_progress.call_count == 2  # 第一次抛、第二次成功


def test_poll_success_with_alive_failure_calls_fallback(fake_baas, fake_device_service):
    """report_device_alive 失败时调用 _mark_alive_active_fallback。"""
    fake_device_service.report_device_alive.side_effect = RuntimeError("alive boom")

    poller = BaasPublishPoller(
        baas_service=fake_baas,
        device_service_provider=lambda: fake_device_service,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=1.0,
    )
    poller.start(publish_id="pub-005", device_id="dev-005", binding_id=46)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if fake_device_service._mark_alive_active_fallback.called:
            break
        time.sleep(0.02)

    fake_device_service._mark_alive_active_fallback.assert_called_once_with(binding_id=46)


def test_poll_multiple_pending_then_success_calls_alive_once(fake_baas, fake_device_service):
    fake_baas.get_publish_progress.side_effect = [
        {"status": "PENDING"},
        {"status": "PENDING"},
        {"status": "SUCCESS"},
    ]
    poller = BaasPublishPoller(
        baas_service=fake_baas,
        device_service_provider=lambda: fake_device_service,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=1.0,
    )
    poller.start(publish_id="pub-006", device_id="dev-006", binding_id=47)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if fake_device_service.report_device_alive.called:
            break
        time.sleep(0.02)

    assert fake_device_service.report_device_alive.call_count == 1
    assert fake_baas.get_publish_progress.call_count == 3
