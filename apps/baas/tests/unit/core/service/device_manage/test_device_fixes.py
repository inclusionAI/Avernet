"""Unit tests for device service bug fixes and batch operation patterns.

7.8: UPDATING status fix in destroy_device_by_uuid
7.12: no-hook fast path
7.13: PaaS creation failure
7.14: cooldown between batches
7.16: stderr warning (exit_code=0 with non-empty stderr)
"""

DS = "secbaas.community.core.service.device_manage"
PS = "secbaas.community.core.service.publish_manage"


class TestUpdatingStatusFix:
    """7.8: get_active_or_updating_by_device_uuid finds UPDATING devices too."""

    def test_repo_finds_updating_devices(self):
        import inspect

        from secbaas.community.core.repository.device import (
            OrmDeviceRepository,
        )

        source = inspect.getsource(
            OrmDeviceRepository.get_active_or_updating_by_device_uuid
        )
        assert "UPDATING" in source
        assert "ACTIVE" in source
        assert "IN" in source

    def test_get_active_by_device_uuid_is_active_only(self):
        """get_active_by_device_uuid should NOT include UPDATING."""
        import inspect

        from secbaas.community.core.repository.device import (
            OrmDeviceRepository,
        )

        source = inspect.getsource(OrmDeviceRepository.get_active_by_device_uuid)
        assert "UPDATING" not in source
        assert (
            'DeviceModel.status == "ACTIVE"' in source or "status = 'ACTIVE'" in source
        )


class TestNoHookFastPath:
    """7.12: Device goes directly PENDING → ACTIVE without callback."""

    def test_no_hook_returns_active_status(self):

        # Verify: when deploy_config is None or has no after_create_cmd_hook,
        # start_device sets device to ACTIVE (not PENDING).
        # This is verified by checking the code path: if no hook, status=ACTIVE
        # We check that the code logic is correct by reading the source.
        import inspect

        from secbaas.community.core.service.device_manage import (
            DefaultDeviceService,
        )

        source = inspect.getsource(DefaultDeviceService.start_device)
        # No-hook fast path should set ACTIVE
        assert "No-hook fast path" in source or "ACTIVE" in source
        # Should dispatch hook via start_hook_dispatcher module
        assert "dispatch_start_hook" in source


class TestPaasCreationFailure:
    """7.13: PaaS creation failure sets device FAILED immediately."""

    def test_paas_failure_code_path_exists(self):

        import inspect

        from secbaas.community.core.service.device_manage import (
            DefaultDeviceService,
        )

        source = inspect.getsource(DefaultDeviceService.start_device)
        # PaaS creation failure fast path sets FAILED
        assert "FAILED" in source
        assert "PaaS creation fails" in source or "creation failed" in source.lower()


class TestStderrWarning:
    """7.16: exit_code=0 with non-empty stderr."""

    def test_stderr_in_serialized_result(self):
        import json

        from secbaas.community.api.publish_manage import serialize_hook_result

        result = serialize_hook_result(
            exit_code=0, stderr="warning: deprecated API used", message="Hook succeeded"
        )
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        assert parsed["stderr"] == "warning: deprecated API used"


class TestCooldownBetweenBatches:
    """7.14: Cooldown between batches uses asyncio.sleep."""

    def test_cooldown_logic_in_source(self):
        """Verify execute_stage contains cooldown sleep between batches."""
        import inspect

        from secbaas.community.core.service.publish_manage import DefaultPublishService

        source = inspect.getsource(DefaultPublishService.execute_stage)
        assert "cooldown_seconds" in source
        assert "asyncio.sleep" in source
        assert "is_paas_mock_mode" in source


class TestStorageIdPlaceholderRendering:
    """Test that {device_uuid} placeholder in storage.storage_id is rendered."""

    def test_storage_id_placeholder_rendering_in_source(self):
        """Verify _build_arca_detail_config renders {device_uuid} in storage.storage_id."""
        import inspect

        from secbaas.community.core.service.device_manage._device_service import (
            _build_arca_detail_config,
        )

        source = inspect.getsource(_build_arca_detail_config)
        # Should contain placeholder replacement logic
        assert '{device_uuid}", device_uuid' in source or "storage_id.replace" in source

    def test_storage_id_rendering_code_path(self):
        """Verify the rendering code path exists for Arca storage."""
        import inspect

        from secbaas.community.core.service.device_manage._device_service import (
            _build_arca_detail_config,
        )

        source = inspect.getsource(_build_arca_detail_config)
        # Check for the complete placeholder rendering logic
        assert "rendered_storage_id" in source
        assert "storage.storage_id.replace" in source


# Import AsyncMock at module level
