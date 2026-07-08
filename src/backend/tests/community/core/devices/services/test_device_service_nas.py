"""Unit tests for DeviceService NAS-related changes (Phase 2).

覆盖：
- _get_storage_mode() — 存储模式查询
- apply_device() NAS 分支 — 跳过 _setup_directory，调用 _do_allocate_nas
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.devices.services.device_service import DeviceService, LOCAL_DEVICE_PROVIDER
from agentclaw.community.core.devices.models import (
    AllocatedDevice,
    DeviceBindingStatus,
    OperatorContext,
    NasMappingInfo,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(repo=None, oss_record_repo=None) -> DeviceService:
    repo = repo or MagicMock()
    return DeviceService(
        repository=repo,
        bot_query=MagicMock(),
        bot_sync=MagicMock(),
        oss_record_repo=oss_record_repo or MagicMock(),
        mcp_sync=MagicMock(),
    )


def _make_operator(staff_id: str = "u001") -> OperatorContext:
    return OperatorContext(
        staff_id=staff_id,
        staff=staff_id,
        nick_name="User",
        operator_name="User",
        tenant_id="default",
    )


def _make_record(binding_id: int = 1) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=binding_id,
        entity_id="u001",
        entity_type="staff",
        device_id="dev001",
        device_provider=LOCAL_DEVICE_PROVIDER,
        env="dev",
        device_props={"callback_token": "tok"},
        status=DeviceBindingStatus.PENDING.value,
        apply_reason=None,
        applied_by="u001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


# ===========================================================================
# _get_storage_mode 测试
# ===========================================================================


class TestGetStorageMode:
    """DeviceService._get_storage_mode()"""

    def _svc(self, record=None, side_effect=None):
        record_repo = MagicMock()
        if side_effect is not None:
            record_repo.get_record.side_effect = side_effect
        else:
            record_repo.get_record.return_value = record
        return _make_service(oss_record_repo=record_repo)

    def test_returns_oss_when_no_record(self):
        mode = self._svc(record=None)._get_storage_mode("u001", "bot001")
        assert mode == "oss"

    def test_returns_oss_when_status_is_oss(self):
        mode = self._svc(record={"storage_status": "oss"})._get_storage_mode("u001", "bot001")
        assert mode == "oss"

    def test_returns_nas_when_status_is_nas(self):
        mode = self._svc(record={"storage_status": "nas"})._get_storage_mode("u001", "bot001")
        assert mode == "nas"

    def test_raises_when_status_is_switching(self):
        svc = self._svc(record={"storage_status": "switching"})
        with pytest.raises(RuntimeError, match="正在迁移中"):
            svc._get_storage_mode("u001", "bot001")

    def test_raises_when_status_is_failed(self):
        svc = self._svc(record={"storage_status": "failed"})
        with pytest.raises(RuntimeError, match="迁移失败"):
            svc._get_storage_mode("u001", "bot001")

    def test_fallback_to_oss_on_query_error(self):
        """repo.get_record 抛异常时 fallback 到 'oss'（表不存在等情况）。"""
        svc = self._svc(side_effect=Exception("table not found"))
        mode = svc._get_storage_mode("u001", "bot001")
        assert mode == "oss"


# ===========================================================================
# apply_device NAS 分支测试
# ===========================================================================


class TestApplyDeviceNasBranch:
    """apply_device() 的 NAS 分支：跳过 _setup_directory，调用 _do_allocate_nas。"""

    def _setup_repo(self, binding_id: int = 1):
        """构造带完整 mock 的 repo。"""
        repo = MagicMock()
        repo.get_existing_binding.return_value = None
        repo.get_released_binding.return_value = None
        repo.insert_binding.return_value = binding_id
        repo.get_by_id.return_value = _make_record(binding_id)
        return repo

    def test_nas_mode_skips_setup_directory(self):
        """NAS 模式：_setup_directory 不被调用。"""
        repo = self._setup_repo()
        svc = _make_service(repo)

        allocated = AllocatedDevice(
            device_id="dev_nas_001",
            device_provider="arca",
            device_props={"client_id": "dev_nas_001", "sandbox_id": "sb001", "storage_mode": "nas"},
        )

        with patch.object(svc, "_get_storage_mode", return_value="nas"), \
             patch.object(svc, "_setup_directory") as mock_setup, \
             patch.object(svc, "_do_allocate_nas", return_value=allocated), \
             patch.object(svc, "_validate_and_generate_device_id",
                          return_value=("dev_nas_001", "bot001", None)), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            svc.apply_device(
                apply_reason="test",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
                bot_id="bot001",
            )

        mock_setup.assert_not_called()

    def test_nas_mode_calls_do_allocate_nas(self):
        """NAS 模式：_do_allocate_nas 被调用（而非 _do_allocate）。"""
        repo = self._setup_repo()
        svc = _make_service(repo)

        allocated = AllocatedDevice(
            device_id="dev_nas_001",
            device_provider="arca",
            device_props={"client_id": "dev_nas_001", "sandbox_id": "sb001", "storage_mode": "nas"},
        )

        with patch.object(svc, "_get_storage_mode", return_value="nas"), \
             patch.object(svc, "_do_allocate") as mock_oss_alloc, \
             patch.object(svc, "_do_allocate_nas", return_value=allocated) as mock_nas_alloc, \
             patch.object(svc, "_validate_and_generate_device_id",
                          return_value=("dev_nas_001", "bot001", None)), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            svc.apply_device(
                apply_reason="test",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
                bot_id="bot001",
            )

        mock_nas_alloc.assert_called_once()
        mock_oss_alloc.assert_not_called()

    def test_nas_allocate_receives_correct_params(self):
        """_do_allocate_nas 接收到正确的 bolt_id, device_id 参数。"""
        repo = self._setup_repo()
        svc = _make_service(repo)

        allocated = AllocatedDevice(
            device_id="dev_nas_001",
            device_provider="arca",
            device_props={"client_id": "dev_nas_001", "sandbox_id": "sb001", "storage_mode": "nas"},
        )

        with patch.object(svc, "_get_storage_mode", return_value="nas"), \
             patch.object(svc, "_do_allocate_nas", return_value=allocated) as mock_nas, \
             patch.object(svc, "_validate_and_generate_device_id",
                          return_value=("dev_nas_001", "bot001", None)), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            svc.apply_device(
                apply_reason="test",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
                bot_id="bot001",
            )

        kwargs = mock_nas.call_args.kwargs
        assert kwargs["device_id"] == "dev_nas_001"
        assert kwargs["bolt_id"] == "bot001"
        assert kwargs["entity_id"] == "u001"

    def test_oss_mode_calls_setup_directory_and_do_allocate(self):
        """OSS 模式（默认）：_setup_directory + _do_allocate 被调用。"""
        repo = self._setup_repo()
        svc = _make_service(repo)

        allocated = AllocatedDevice(
            device_id="dev_oss_001",
            device_provider=LOCAL_DEVICE_PROVIDER,
            device_props={"client_id": "dev_oss_001"},
        )
        nas_mappings = [NasMappingInfo(
            nas_remote="/oss/path",
            nas_local="/home/admin/nfs",
            nas_permission="READ_WRITE",
        )]

        with patch.object(svc, "_get_storage_mode", return_value="oss"), \
             patch.object(svc, "_setup_directory", return_value=nas_mappings) as mock_setup, \
             patch.object(svc, "_do_allocate", return_value=allocated) as mock_oss, \
             patch.object(svc, "_do_allocate_nas") as mock_nas, \
             patch.object(svc, "_validate_and_generate_device_id",
                          return_value=("dev_oss_001", "bot001", None)), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            svc.apply_device(
                apply_reason="test",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
                bot_id="bot001",
            )

        mock_setup.assert_called_once()
        mock_oss.assert_called_once()
        mock_nas.assert_not_called()

    def test_nas_mode_empty_nas_mappings_in_device_props(self):
        """NAS 模式下 device_props.nas_mappings 应为空列表。"""
        repo = self._setup_repo()
        svc = _make_service(repo)

        allocated = AllocatedDevice(
            device_id="dev_nas_001",
            device_provider="arca",
            device_props={"client_id": "dev_nas_001", "sandbox_id": "sb001", "storage_mode": "nas"},
        )

        captured_props = {}

        def capture_insert(**kwargs):
            captured_props.update(kwargs.get("device_props", {}))
            return 1

        repo.insert_binding.side_effect = capture_insert

        with patch.object(svc, "_get_storage_mode", return_value="nas"), \
             patch.object(svc, "_do_allocate_nas", return_value=allocated), \
             patch.object(svc, "_validate_and_generate_device_id",
                          return_value=("dev_nas_001", "bot001", None)), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            svc.apply_device(
                apply_reason="test",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
                bot_id="bot001",
            )

        import json
        nas_mappings_in_props = json.loads(captured_props.get("nas_mappings", "[]"))
        assert nas_mappings_in_props == []


class TestDoAllocateNasHook:
    """DeviceService._do_allocate_nas() — 基类默认实现。"""

    def test_raises_not_implemented(self):
        """基类默认实现抛出 NotImplementedError。"""
        svc = _make_service()
        with pytest.raises(NotImplementedError, match="does not support NAS storage mode"):
            svc._do_allocate_nas(
                entity_id="u001",
                entity_type="staff",
                bolt_id="bot001",
                device_id="dev001",
                env="dev",
            )
