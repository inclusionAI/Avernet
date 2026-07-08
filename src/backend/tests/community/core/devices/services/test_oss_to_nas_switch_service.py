"""Unit tests for agentclaw.community.core.devices.services.oss_to_nas_switch_service."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_record(
    staff_no: str = "staff001",
    bot_id: str = "bot001",
    storage_status: str = "oss",
) -> dict:
    return {
        "id": 1,
        "staff_no": staff_no,
        "bot_id": bot_id,
        "bot_info": None,
        "env": "pre",
        "batch_no": "batch01",
        "sub_batch_no": "sub01",
        "storage_status": storage_status,
        "gmt_create": "2024-01-01T00:00:00",
        "gmt_modified": "2024-01-01T00:00:00",
    }


def _ctx(record_repo=None, bot_repo=None, bot_service=None):
    """Common kwargs for do_switch_one/do_rollback_one/batch_switch_with_concurrency."""
    return {
        "bot_repo": bot_repo or MagicMock(),
        "bot_service": bot_service or MagicMock(),
        "oss_to_nas_config": MagicMock(),
        "record_repo": record_repo or MagicMock(),
    }


# ---------------------------------------------------------------------------
# do_switch_one
# ---------------------------------------------------------------------------

class TestDoSwitchOne:
    def test_success_updates_status_to_nas(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        mock_bot_service = MagicMock()
        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "entity_type": "staff", "entity_id": "staff001", "active_engine": "openclaw",
        }
        mock_migration = MagicMock()
        mock_migration.migrate.return_value = True

        status_calls = []
        mock_record_repo = MagicMock()
        mock_record_repo.update_status.side_effect = lambda sn, bi, s, env=None: status_calls.append(s)

        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.OssToNasMigrationService",
                   return_value=mock_migration), \
             patch("agentclaw.community.utils.env_utils.get_current_env", return_value="pre"):
            result = asyncio.run(m.do_switch_one(
                "staff001", "bot001",
                **_ctx(record_repo=mock_record_repo, bot_repo=mock_bot_repo, bot_service=mock_bot_service),
            ))

        assert result["status"] == "success"
        assert status_calls[0] == "switching"
        assert status_calls[-1] == "nas"

    def test_migration_failure_marks_failed(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        mock_bot_service = MagicMock()
        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "entity_type": "staff", "entity_id": "staff001", "active_engine": "openclaw",
        }
        mock_migration = MagicMock()
        mock_migration.migrate.return_value = False

        status_calls = []
        mock_record_repo = MagicMock()
        mock_record_repo.update_status.side_effect = lambda sn, bi, s, env=None: status_calls.append(s)

        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.OssToNasMigrationService",
                   return_value=mock_migration), \
             patch("agentclaw.community.utils.env_utils.get_current_env", return_value="pre"):
            result = asyncio.run(m.do_switch_one(
                "staff001", "bot001",
                **_ctx(record_repo=mock_record_repo, bot_repo=mock_bot_repo, bot_service=mock_bot_service),
            ))

        assert result["status"] == "failed"
        assert "迁移失败" in result["error"]
        assert "failed" in status_calls

    def test_migration_uses_bot_entity_info(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        mock_bot_service = MagicMock()
        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "entity_type": "proj", "entity_id": "proj_99999", "active_engine": "moltis",
        }
        mock_migration = MagicMock()
        mock_migration.migrate.return_value = True

        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.OssToNasMigrationService",
                   return_value=mock_migration), \
             patch("agentclaw.community.utils.env_utils.get_current_env", return_value="pre"):
            asyncio.run(m.do_switch_one(
                "staff001", "bot001",
                **_ctx(bot_repo=mock_bot_repo, bot_service=mock_bot_service),
            ))

        mock_migration.migrate.assert_called_once_with(
            "pre", "proj", "proj_99999", "moltis", "bot001"
        )

    def test_bot_not_found_marks_failed(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_and_owner.return_value = None

        status_calls = []
        mock_record_repo = MagicMock()
        mock_record_repo.update_status.side_effect = lambda sn, bi, s, env=None: status_calls.append(s)

        result = asyncio.run(m.do_switch_one(
            "staff001", "bot001",
            **_ctx(record_repo=mock_record_repo, bot_repo=mock_bot_repo),
        ))

        assert result["status"] == "failed"
        assert "failed" in status_calls

    def test_first_step_is_switching(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        first_status = []

        def track_first(staff_no, bot_id, status, env=None):
            if not first_status:
                first_status.append(status)
            if status != "switching":
                raise RuntimeError("abort after first")

        mock_record_repo = MagicMock()
        mock_record_repo.update_status.side_effect = track_first
        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_and_owner.return_value = None

        asyncio.run(m.do_switch_one(
            "staff001", "bot001",
            **_ctx(record_repo=mock_record_repo, bot_repo=mock_bot_repo),
        ))

        assert first_status[0] == "switching"

    def test_stop_bot_failure_marks_failed(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "entity_type": "staff", "entity_id": "staff001", "active_engine": "openclaw",
        }
        mock_bot_service = MagicMock()
        mock_bot_service.stop_bot.side_effect = RuntimeError("device busy")

        status_calls = []
        mock_record_repo = MagicMock()
        mock_record_repo.update_status.side_effect = lambda sn, bi, s, env=None: status_calls.append(s)

        result = asyncio.run(m.do_switch_one(
            "staff001", "bot001",
            **_ctx(record_repo=mock_record_repo, bot_repo=mock_bot_repo, bot_service=mock_bot_service),
        ))

        assert result["status"] == "failed"
        assert "device busy" in result["error"]
        assert "failed" in status_calls

    def test_preserves_current_device_provider_when_starting_after_switch(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        mock_bot_service = MagicMock()
        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "entity_type": "staff",
            "entity_id": "staff001",
            "active_engine": "openclaw",
            "binding_id": 42,
        }
        mock_device_binding_repo = MagicMock()
        mock_device_binding_repo.get_by_id.return_value = SimpleNamespace(
            device_provider="baas"
        )
        mock_migration = MagicMock()
        mock_migration.migrate.return_value = True

        with patch(
            "agentclaw.community.core.devices.services.oss_to_nas_migration_service.OssToNasMigrationService",
            return_value=mock_migration,
        ), patch("agentclaw.community.utils.env_utils.get_current_env", return_value="pre"):
            result = asyncio.run(
                m.do_switch_one(
                    "staff001",
                    "bot001",
                    **_ctx(bot_repo=mock_bot_repo, bot_service=mock_bot_service),
                    device_binding_repo=mock_device_binding_repo,
                )
            )

        assert result["status"] == "success"
        mock_bot_service.start_bot.assert_called_once_with(
            bot_id="bot001",
            user_id="staff001",
            force_nas=True,
            device_provider="baas",
        )


# ---------------------------------------------------------------------------
# do_rollback_one
# ---------------------------------------------------------------------------

class TestDoRollbackOne:
    def test_preserves_current_device_provider_when_starting_after_rollback(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        mock_bot_service = MagicMock()
        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "entity_type": "staff",
            "entity_id": "staff001",
            "active_engine": "openclaw",
            "binding_id": 42,
        }
        mock_device_binding_repo = MagicMock()
        mock_device_binding_repo.get_by_id.return_value = SimpleNamespace(
            device_provider="baas"
        )
        mock_migration = MagicMock()
        mock_migration.migrate.return_value = True

        with patch(
            "agentclaw.community.core.devices.services.oss_to_nas_migration_service.OssToNasMigrationService",
            return_value=mock_migration,
        ), patch("agentclaw.community.utils.env_utils.get_current_env", return_value="pre"):
            result = asyncio.run(
                m.do_rollback_one(
                    "staff001",
                    "bot001",
                    **_ctx(bot_repo=mock_bot_repo, bot_service=mock_bot_service),
                    device_binding_repo=mock_device_binding_repo,
                )
            )

        assert result["status"] == "success"
        mock_bot_service.start_bot.assert_called_once_with(
            bot_id="bot001",
            user_id="staff001",
            device_provider="baas",
        )


# ---------------------------------------------------------------------------
# batch_switch_with_concurrency
# ---------------------------------------------------------------------------

class TestBatchSwitchWithConcurrency:
    def test_all_success_returns_correct_counts(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        records = [_make_record(staff_no=f"s{i}", bot_id=f"b{i}") for i in range(3)]

        async def mock_switch(staff_no, bot_id, env=None, **_kw):
            return {"staff_no": staff_no, "bot_id": bot_id, "status": "success"}

        with patch.object(m, "do_switch_one", side_effect=mock_switch):
            result = asyncio.run(m.batch_switch_with_concurrency(records, concurrency=2, **_ctx()))

        assert result["total"] == 3
        assert result["succeeded"] == 3
        assert result["failed"] == 0
        assert result["failed_details"] == []

    def test_partial_failure_counts_correctly(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        records = [
            _make_record(staff_no="s0", bot_id="b0"),
            _make_record(staff_no="s1", bot_id="b1"),
            _make_record(staff_no="s2", bot_id="b2"),
        ]

        async def mock_switch(staff_no, bot_id, env=None, **_kw):
            if bot_id == "b1":
                return {"staff_no": staff_no, "bot_id": bot_id, "status": "failed", "error": "oops"}
            return {"staff_no": staff_no, "bot_id": bot_id, "status": "success"}

        with patch.object(m, "do_switch_one", side_effect=mock_switch):
            result = asyncio.run(m.batch_switch_with_concurrency(records, concurrency=3, **_ctx()))

        assert result["total"] == 3
        assert result["succeeded"] == 2
        assert result["failed"] == 1
        assert len(result["failed_details"]) == 1

    def test_concurrency_limit_is_respected(self):
        import agentclaw.community.core.devices.services.oss_to_nas_switch_service as m

        records = [_make_record(staff_no=f"s{i}", bot_id=f"b{i}") for i in range(5)]
        call_order = []

        async def mock_switch(staff_no, bot_id, env=None, **_kw):
            call_order.append(bot_id)
            return {"staff_no": staff_no, "bot_id": bot_id, "status": "success"}

        with patch.object(m, "do_switch_one", side_effect=mock_switch):
            result = asyncio.run(m.batch_switch_with_concurrency(records, concurrency=1, **_ctx()))

        assert result["succeeded"] == 5
        assert len(call_order) == 5
