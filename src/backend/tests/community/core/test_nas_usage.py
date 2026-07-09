"""Tests for NAS usage statistics service."""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from agentclaw.community.core.nas_usage import NasUsageService, CooldownError, get_nas_usage_service
from agentclaw.community.plugin_api.database import DatabasePlugin


class TestNasUsageService:
    """Tests for NasUsageService."""

    def test_singleton_instance(self):
        """Test that get_nas_usage_service returns a singleton."""
        service1 = get_nas_usage_service()
        service2 = get_nas_usage_service()
        assert service1 is service2

    def test_cooldown_check_none_minutes(self):
        """Test cooldown check with None minutes skips check."""
        service = NasUsageService()
        # Should not raise
        service.check_cooldown(None)
        service.check_cooldown(0)

    def test_cooldown_check_elapsed(self):
        """Test cooldown check when enough time has passed."""
        service = NasUsageService()

        # Mock database with old timestamp
        mock_result = MagicMock()
        mock_result.last_modified = datetime.now(timezone.utc) - timedelta(minutes=120)

        with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_session.execute.return_value.fetchone.return_value = mock_result
            mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_injector.return_value.get.return_value = mock_db

            # Should not raise - 60 minutes cooldown, last update was 120 minutes ago
            service.check_cooldown(60)

    def test_cooldown_check_not_elapsed(self):
        """Test cooldown check when not enough time has passed."""
        service = NasUsageService()

        # Mock database with recent timestamp
        mock_result = MagicMock()
        mock_result.last_modified = datetime.now(timezone.utc) - timedelta(minutes=30)

        with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_session.execute.return_value.fetchone.return_value = mock_result
            mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_injector.return_value.get.return_value = mock_db

            # Should raise - 60 minutes cooldown, last update was 30 minutes ago
            with pytest.raises(CooldownError) as exc_info:
                service.check_cooldown(60)

            assert exc_info.value.remaining_minutes > 29
            assert exc_info.value.remaining_minutes < 31

    def test_cooldown_check_no_records(self):
        """Test cooldown check when no records exist."""
        service = NasUsageService()

        # Mock database with no records
        mock_result = MagicMock()
        mock_result.last_modified = None

        with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_session.execute.return_value.fetchone.return_value = mock_result
            mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_injector.return_value.get.return_value = mock_db

            # Should not raise - no records means can proceed
            service.check_cooldown(60)

    def test_cooldown_check_naive_datetime_as_beijing(self):
        """Test cooldown check treats naive datetime as Beijing time (UTC+8) and converts to UTC."""
        service = NasUsageService()

        # Mock database with naive datetime from 120 minutes ago (Beijing time)
        # When we subtract 8 hours, it becomes 120 + 480 = 600 minutes ago in UTC
        # But we want elapsed to be 120 minutes, so we need Beijing time to be
        # 120 minutes ago from now in Beijing time zone
        mock_result = MagicMock()
        # Beijing time now - 120 minutes = UTC now + 8h - 120min = UTC now - 120min + 8h = UTC now + 360min
        # When converted to UTC: (UTC now + 360min) - 8h = UTC now - 120min
        # So elapsed should be 120 minutes
        naive_time_beijing = datetime.now(timezone.utc) + timedelta(hours=8) - timedelta(minutes=120)
        mock_result.last_modified = naive_time_beijing.replace(tzinfo=None)

        with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_session.execute.return_value.fetchone.return_value = mock_result
            mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_injector.return_value.get.return_value = mock_db

            # Should not raise - 60 minutes cooldown, last update was 120 minutes ago
            service.check_cooldown(60)

    def test_cooldown_check_aware_datetime_preserved(self):
        """Test cooldown check preserves timezone-aware datetime."""
        service = NasUsageService()

        # Mock database with timezone-aware datetime
        mock_result = MagicMock()
        mock_result.last_modified = datetime.now(timezone.utc) - timedelta(minutes=30)

        with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_session.execute.return_value.fetchone.return_value = mock_result
            mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_injector.return_value.get.return_value = mock_db

            # Should raise - aware datetime should be used directly
            with pytest.raises(CooldownError) as exc_info:
                service.check_cooldown(60)

            assert exc_info.value.remaining_minutes > 29
            assert exc_info.value.remaining_minutes < 31

    def test_get_recently_updated_dirs_none_minutes(self):
        """Test get_recently_updated_dirs with None minutes returns empty set."""
        service = NasUsageService()
        result = service.get_recently_updated_dirs(None)
        assert result == set()

        result = service.get_recently_updated_dirs(0)
        assert result == set()

    def test_get_recently_updated_dirs_with_minutes(self):
        """Test get_recently_updated_dirs filters correctly."""
        service = NasUsageService()

        # Mock database with directories
        mock_rows = [
            MagicMock(directory_name="dir1"),
            MagicMock(directory_name="dir2"),
        ]

        with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_session.execute.return_value.fetchall.return_value = mock_rows
            mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_injector.return_value.get.return_value = mock_db

            result = service.get_recently_updated_dirs(60)
            assert result == {"dir1", "dir2"}

    def test_trigger_already_running(self):
        """Test trigger returns False when already running."""
        service = NasUsageService()

        # Mock a running task
        service._disk_usage_task = MagicMock()
        service._disk_usage_task.done.return_value = False

        result = service.trigger_disk_usage_analysis()
        assert result is False

    @pytest.mark.asyncio
    async def test_run_disk_usage_analysis_target_not_found(self):
        """Test analysis handles missing target directory."""
        service = NasUsageService()

        with patch('agentclaw.community.core.nas_usage.service.Path') as mock_path:
            mock_path.return_value.exists.return_value = False

            await service._run_disk_usage_analysis()
            # Should complete without error

    @pytest.mark.asyncio
    async def test_run_disk_usage_analysis_no_dirs_to_process(self):
        """Test analysis handles case where all dirs are skipped."""
        service = NasUsageService()

        with patch('agentclaw.community.core.nas_usage.service.Path') as mock_path:
            mock_target = MagicMock()
            mock_target.exists.return_value = True
            mock_target.iterdir.return_value = []
            mock_path.return_value = mock_target

            with patch.object(service, 'get_recently_updated_dirs', return_value=set()):
                await service._run_disk_usage_analysis(skip_within_minutes=60)
                # Should complete without processing

    @pytest.mark.asyncio
    async def test_run_disk_usage_analysis_cooldown_abort(self):
        """Test analysis aborts when cooldown check fails after filtering."""
        service = NasUsageService()

        with patch('agentclaw.community.core.nas_usage.service.Path') as mock_path:
            mock_target = MagicMock()
            mock_target.exists.return_value = True
            mock_target.iterdir.return_value = [
                MagicMock(is_dir=MagicMock(return_value=True), name="dir1")
            ]
            mock_path.return_value = mock_target

            with patch.object(service, 'get_recently_updated_dirs', return_value=set()):
                with patch.object(service, 'check_cooldown', side_effect=CooldownError(30)):
                    await service._run_disk_usage_analysis(
                        skip_within_minutes=60,
                        cooldown_minutes=60
                    )
                    # Should abort without processing


class TestCooldownError:
    """Tests for CooldownError."""

    def test_cooldown_error_message(self):
        """Test CooldownError message formatting."""
        error = CooldownError(45.5)
        assert "45.5 minutes" in str(error)
        assert error.remaining_minutes == 45.5


class TestNasUsageServiceConcurrency:
    """Tests for concurrency control."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Test that semaphore limits concurrent du processes."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(2)  # Limit to 2

        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            call_count -= 1

        # Start 4 tasks with semaphore limiting to 2
        async def limited_process(sem):
            async with sem:
                await mock_process()

        tasks = [limited_process(semaphore) for _ in range(4)]
        await asyncio.gather(*tasks)

        # All should complete
        assert call_count == 0


class TestFileCountAnalysis:
    """Tests for file count analysis."""

    def test_file_count_running_property(self):
        """Test file_count_running property."""
        service = NasUsageService()
        assert service.file_count_running is False

        # Mock a running task
        service._file_count_task = MagicMock()
        service._file_count_task.done.return_value = False
        assert service.file_count_running is True

        # Mock a completed task
        service._file_count_task.done.return_value = True
        assert service.file_count_running is False

    def test_trigger_file_count_analysis_already_running(self):
        """Test trigger_file_count_analysis returns False when already running."""
        service = NasUsageService()

        # Mock a running task
        service._file_count_task = MagicMock()
        service._file_count_task.done.return_value = False

        result = service.trigger_file_count_analysis()
        assert result is False

    @pytest.mark.asyncio
    async def test_trigger_file_count_analysis_success(self):
        """Test trigger_file_count_analysis creates task and returns True."""
        service = NasUsageService()

        result = service.trigger_file_count_analysis(skip_within_minutes=30, cooldown_minutes=60)
        assert result is True
        assert service._file_count_task is not None

    def test_disk_usage_running_property(self):
        """Test disk_usage_running property with done=True."""
        service = NasUsageService()
        assert service.disk_usage_running is False

        # Mock a completed task
        service._disk_usage_task = MagicMock()
        service._disk_usage_task.done.return_value = True
        assert service.disk_usage_running is False

    @pytest.mark.asyncio
    async def test_trigger_disk_usage_analysis_success(self):
        """Test trigger_disk_usage_analysis creates task and returns True."""
        service = NasUsageService()

        result = service.trigger_disk_usage_analysis(skip_within_minutes=30, concurrency=4)
        assert result is True
        assert service._disk_usage_task is not None

    @pytest.mark.asyncio
    async def test_analyze_single_dir_size_success(self):
        """Test _analyze_single_dir_size with successful du output."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(1)

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"12345\t/home/admin/.merge_nas/test_dir\n", b"")
        mock_process.returncode = 0

        with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', return_value=mock_process):
            dir_path = MagicMock()
            dir_path.name = "test_dir"
            dir_path_str = MagicMock()
            dir_path.__str__ = lambda self: "/home/admin/.merge_nas/test_dir"

            result = await service._analyze_single_dir_size(dir_path, semaphore)
            assert result == ("test_dir", 12345)

    @pytest.mark.asyncio
    async def test_analyze_single_dir_size_failure(self):
        """Test _analyze_single_dir_size when du fails."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(1)

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"du: error")
        mock_process.returncode = 1

        with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', return_value=mock_process):
            dir_path = MagicMock()
            dir_path.name = "test_dir"
            dir_path.__str__ = lambda self: "/home/admin/.merge_nas/test_dir"

            result = await service._analyze_single_dir_size(dir_path, semaphore)
            assert result == ("test_dir", None)

    @pytest.mark.asyncio
    async def test_analyze_single_dir_size_exception(self):
        """Test _analyze_single_dir_size handles exception."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(1)

        with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', side_effect=Exception("boom")):
            dir_path = MagicMock()
            dir_path.name = "test_dir"

            result = await service._analyze_single_dir_size(dir_path, semaphore)
            assert result == ("test_dir", None)

    @pytest.mark.asyncio
    async def test_analyze_single_dir_size_empty_output(self):
        """Test _analyze_single_dir_size with empty output."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(1)

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.returncode = 0

        with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', return_value=mock_process):
            dir_path = MagicMock()
            dir_path.name = "test_dir"

            result = await service._analyze_single_dir_size(dir_path, semaphore)
            assert result == ("test_dir", None)

    @pytest.mark.asyncio
    async def test_analyze_single_dir_count_success(self):
        """Test _analyze_single_dir_count with successful find output."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(1)

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"file1\nfile2\nfile3\n", b"")
        mock_process.returncode = 0

        with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', return_value=mock_process):
            dir_path = MagicMock()
            dir_path.name = "test_dir"

            result = await service._analyze_single_dir_count(dir_path, semaphore)
            assert result == ("test_dir", 3)

    @pytest.mark.asyncio
    async def test_analyze_single_dir_count_empty(self):
        """Test _analyze_single_dir_count with no files."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(1)

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.returncode = 0

        with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', return_value=mock_process):
            dir_path = MagicMock()
            dir_path.name = "test_dir"

            result = await service._analyze_single_dir_count(dir_path, semaphore)
            assert result == ("test_dir", 0)

    @pytest.mark.asyncio
    async def test_analyze_single_dir_count_failure(self):
        """Test _analyze_single_dir_count when find fails."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(1)

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"find: error")
        mock_process.returncode = 1

        with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', return_value=mock_process):
            dir_path = MagicMock()
            dir_path.name = "test_dir"

            result = await service._analyze_single_dir_count(dir_path, semaphore)
            assert result == ("test_dir", None)

    @pytest.mark.asyncio
    async def test_analyze_single_dir_count_exception(self):
        """Test _analyze_single_dir_count handles exception."""
        service = NasUsageService()
        semaphore = asyncio.Semaphore(1)

        with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', side_effect=Exception("boom")):
            dir_path = MagicMock()
            dir_path.name = "test_dir"

            result = await service._analyze_single_dir_count(dir_path, semaphore)
            assert result == ("test_dir", None)

    @pytest.mark.asyncio
    async def test_write_size_to_db(self):
        """Test _write_size_to_db executes correct SQL."""
        service = NasUsageService()

        with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_injector.return_value.get.return_value = mock_db

            await service._write_size_to_db("test_dir", 12345)

            # Verify the SQL was executed
            assert mock_session.execute.called
            call_args = mock_session.execute.call_args
            assert "INSERT INTO ac_nas_usage_info" in str(call_args[0][0])

    @pytest.mark.asyncio
    async def test_write_count_to_db(self):
        """Test _write_count_to_db executes correct SQL."""
        service = NasUsageService()

        with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_injector.return_value.get.return_value = mock_db

            await service._write_count_to_db("test_dir", 100)

            # Verify the SQL was executed
            assert mock_session.execute.called
            call_args = mock_session.execute.call_args
            assert "INSERT INTO ac_nas_usage_info" in str(call_args[0][0])

    @pytest.mark.asyncio
    async def test_run_disk_usage_analysis_success(self):
        """Test _run_disk_usage_analysis processes directories successfully."""
        service = NasUsageService()

        mock_dir1 = MagicMock()
        mock_dir1.is_dir.return_value = True
        mock_dir1.name = "dir1"
        mock_dir2 = MagicMock()
        mock_dir2.is_dir.return_value = True
        mock_dir2.name = "dir2"

        mock_target = MagicMock()
        mock_target.exists.return_value = True
        mock_target.iterdir.return_value = [mock_dir1, mock_dir2]

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"100\t/path/dir1\n", b"")
        mock_process.returncode = 0

        with patch('agentclaw.community.core.nas_usage.service.Path', return_value=mock_target):
            with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', return_value=mock_process):
                with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
                    mock_db = MagicMock()
                    mock_session = MagicMock()
                    mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                    mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
                    mock_get_injector.return_value.get.return_value = mock_db

                    with patch.object(service, 'get_recently_updated_dirs', return_value=set()):
                        with patch.object(service, 'check_cooldown'):
                            await service._run_disk_usage_analysis(concurrency=2)
                            # Should process both directories

    @pytest.mark.asyncio
    async def test_run_file_count_analysis_target_not_found(self):
        """Test file count analysis handles missing target directory."""
        service = NasUsageService()

        with patch('agentclaw.community.core.nas_usage.service.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            await service._run_file_count_analysis()

    @pytest.mark.asyncio
    async def test_run_file_count_analysis_no_dirs(self):
        """Test file count analysis with no directories."""
        service = NasUsageService()

        mock_target = MagicMock()
        mock_target.exists.return_value = True
        mock_target.iterdir.return_value = []

        with patch('agentclaw.community.core.nas_usage.service.Path', return_value=mock_target):
            await service._run_file_count_analysis()

    @pytest.mark.asyncio
    async def test_run_file_count_analysis_cooldown_abort(self):
        """Test file count analysis aborts on cooldown."""
        service = NasUsageService()

        mock_dir = MagicMock()
        mock_dir.is_dir.return_value = True
        mock_dir.name = "dir1"

        mock_target = MagicMock()
        mock_target.exists.return_value = True
        mock_target.iterdir.return_value = [mock_dir]

        with patch('agentclaw.community.core.nas_usage.service.Path', return_value=mock_target):
            with patch.object(service, 'get_recently_updated_dirs', return_value=set()):
                with patch.object(service, 'check_cooldown', side_effect=CooldownError(30)):
                    await service._run_file_count_analysis(cooldown_minutes=60)

    @pytest.mark.asyncio
    async def test_run_file_count_analysis_success(self):
        """Test file count analysis processes directories successfully."""
        service = NasUsageService()

        mock_dir1 = MagicMock()
        mock_dir1.is_dir.return_value = True
        mock_dir1.name = "dir1"

        mock_target = MagicMock()
        mock_target.exists.return_value = True
        mock_target.iterdir.return_value = [mock_dir1]

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"file1\nfile2\n", b"")
        mock_process.returncode = 0

        with patch('agentclaw.community.core.nas_usage.service.Path', return_value=mock_target):
            with patch('agentclaw.community.core.nas_usage.service.asyncio.create_subprocess_exec', return_value=mock_process):
                with patch('agentclaw.community.core.nas_usage.service.get_app_injector') as mock_get_injector:
                    mock_db = MagicMock()
                    mock_session = MagicMock()
                    mock_db.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                    mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
                    mock_get_injector.return_value.get.return_value = mock_db

                    with patch.object(service, 'get_recently_updated_dirs', return_value=set()):
                        with patch.object(service, 'check_cooldown'):
                            await service._run_file_count_analysis(concurrency=2)

    @pytest.mark.asyncio
    async def test_run_disk_usage_analysis_with_skip(self):
        """Test analysis skips recently updated directories."""
        service = NasUsageService()

        mock_dir1 = MagicMock()
        mock_dir1.is_dir.return_value = True
        mock_dir1.name = "dir1"
        mock_dir2 = MagicMock()
        mock_dir2.is_dir.return_value = True
        mock_dir2.name = "dir2"

        mock_target = MagicMock()
        mock_target.exists.return_value = True
        mock_target.iterdir.return_value = [mock_dir1, mock_dir2]

        with patch('agentclaw.community.core.nas_usage.service.Path', return_value=mock_target):
            with patch.object(service, 'get_recently_updated_dirs', return_value={"dir1"}):
                with patch.object(service, 'check_cooldown'):
                    # Should only process dir2, not dir1
                    analysis_spy = AsyncMock(return_value=("dir2", 100))
                    with patch.object(service, '_analyze_single_dir_size', analysis_spy):
                        with patch.object(service, '_write_size_to_db', AsyncMock()):
                            await service._run_disk_usage_analysis(skip_within_minutes=60, concurrency=2)
                            # dir1 should be skipped
                            assert analysis_spy.call_count == 1