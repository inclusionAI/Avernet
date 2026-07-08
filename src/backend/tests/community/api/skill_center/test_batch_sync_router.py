"""Tests for batch-sync API router — async task pattern.

覆盖异步批量同步的 API 层测试：
1. POST/GET 立即返回 task_id（status=running）
2. /status/{task_id} 轮询任务状态
3. 后台任务执行逻辑
4. 报告端点
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.skill_center.batch_sync import router as batch_sync_router, _tasks, _task_reports
from agentclaw.community.core.skill_center.services.git_sync import GitSyncService
from agentclaw.community.core.skill_center.services.skill_batch_sync_service import (
    SkillBatchSyncService,
)
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient


# Module paths for patching
_BATCH_SYNC_MODULE = "agentclaw.community.adapters.http.skill_center.batch_sync"


@pytest.fixture(autouse=True)
def clear_tasks():
    """每个测试前后清空 _tasks 和 _task_reports。"""
    _tasks.clear()
    _task_reports.clear()
    yield
    _tasks.clear()
    _task_reports.clear()


@pytest.fixture
def mock_batch_sync_svc():
    svc = MagicMock()
    svc.run = MagicMock(return_value=MagicMock(
        total=10, success=10, failed=0, skipped=0, results=[],
        trace_id="test-trace",
    ))
    svc._scan_skills = MagicMock(return_value=[
        MagicMock(name="skill-1"),
        MagicMock(name="skill-2"),
        MagicMock(name="skill-3"),
        MagicMock(name="skill-4"),
        MagicMock(name="skill-5"),
    ])
    # 每个 MagicMock.name 需要返回字符串
    for i, m in enumerate(svc._scan_skills.return_value):
        m.name = f"skill-{i + 1}"
    svc.get_default_skills_dir = MagicMock(return_value="/skills")
    return svc


@pytest.fixture
def mock_git_sync():
    git_sync = MagicMock()
    git_sync.sync = AsyncMock(return_value={"success": True})
    git_sync.bootstrap = AsyncMock(return_value={"success": True})
    return git_sync


@pytest.fixture
def mock_skill_center_client():
    return MagicMock()


def _attach(app, *, git_sync=None, svc=None, client=None):
    """Bind dependencies via test injector."""
    git_sync = git_sync if git_sync is not None else MagicMock()
    svc = svc if svc is not None else MagicMock()
    client = client if client is not None else MagicMock()

    class _M(Module):
        def configure(self, binder):
            from agentclaw.community.api.git_sync_service import GitSyncServiceProtocol
            from agentclaw.community.api.skill_batch_sync_service import SkillBatchSyncServiceProtocol
            binder.bind(GitSyncService, to=git_sync)
            binder.bind(GitSyncServiceProtocol, to=git_sync)
            binder.bind(SkillBatchSyncService, to=svc)
            binder.bind(SkillBatchSyncServiceProtocol, to=svc)
            binder.bind(SkillCenterClient, to=client)

    attach_injector(app, Injector([_M()]))


@pytest.fixture
def client(mock_batch_sync_svc, mock_git_sync, mock_skill_center_client):
    app = FastAPI()
    app.include_router(batch_sync_router)
    _attach(app, git_sync=mock_git_sync, svc=mock_batch_sync_svc, client=mock_skill_center_client)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /batch-sync — 立即返回 task_id
# ---------------------------------------------------------------------------

class TestBatchSyncPostReturnsTaskId:
    def test_post_returns_task_id_immediately(self, client):
        """POST 请求立即返回 task_id，不等待同步完成。"""
        with patch(f"{_BATCH_SYNC_MODULE}.asyncio.create_task"):
            resp = client.post("/api/v1/skill-center/batch-sync", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "running"
        assert "message" in data

    def test_post_with_batch_size_returns_task_id(self, client):
        """POST 带 batch_size 参数也立即返回 task_id。"""
        with patch(f"{_BATCH_SYNC_MODULE}.asyncio.create_task"):
            resp = client.post("/api/v1/skill-center/batch-sync", json={
                "batch_size": 100, "batch_index": 0
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert "task_id" in data

    def test_post_with_skill_codes(self, client):
        """POST 指定 skill_codes 也立即返回。"""
        with patch(f"{_BATCH_SYNC_MODULE}.asyncio.create_task"):
            resp = client.post("/api/v1/skill-center/batch-sync", json={
                "skill_codes": ["skill-a", "skill-b"],
                "force": True,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"


# ---------------------------------------------------------------------------
# GET /batch-sync — 同样返回 task_id
# ---------------------------------------------------------------------------

class TestBatchSyncGetReturnsTaskId:
    def test_get_returns_task_id(self, client):
        """GET 请求也返回 task_id。"""
        with patch(f"{_BATCH_SYNC_MODULE}.asyncio.create_task"):
            resp = client.get("/api/v1/skill-center/batch-sync")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"

    def test_get_with_params(self, client):
        """GET 带 query 参数。"""
        with patch(f"{_BATCH_SYNC_MODULE}.asyncio.create_task"):
            resp = client.get("/api/v1/skill-center/batch-sync?batch_size=50&batch_index=0&force=true")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"


# ---------------------------------------------------------------------------
# GET /batch-sync/status/{task_id}
# ---------------------------------------------------------------------------

class TestBatchSyncStatus:
    def test_status_running(self, client):
        """查询 running 状态的任务。"""
        _tasks["test-task-1"] = {"status": "running", "progress": "batch 1/5", "result": None, "error": ""}
        resp = client.get("/api/v1/skill-center/batch-sync/status/test-task-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "test-task-1"
        assert data["status"] == "running"
        assert data["progress"] == "batch 1/5"
        assert data["result"] is None
        assert data["error"] == ""

    def test_status_done(self, client):
        """查询已完成的任务。"""
        _tasks["test-task-2"] = {
            "status": "done",
            "progress": "completed (10 skills)",
            "result": {"success": True, "total": 10, "success_count": 10, "failed": 0},
            "error": "",
        }
        resp = client.get("/api/v1/skill-center/batch-sync/status/test-task-2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["result"] is not None
        assert data["result"]["success"] is True

    def test_status_error(self, client):
        """查询失败的任务。"""
        _tasks["test-task-3"] = {"status": "error", "progress": "", "result": None, "error": "Service init failed"}
        resp = client.get("/api/v1/skill-center/batch-sync/status/test-task-3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "Service init failed" in data["error"]

    def test_status_not_found(self, client):
        """查询不存在的任务 → 404。"""
        resp = client.get("/api/v1/skill-center/batch-sync/status/nonexistent")
        assert resp.status_code == 404

    def test_status_fallback_to_disk(self, client, tmp_path):
        """进程重启后 _tasks 丢失，从磁盘查找报告。"""
        import agentclaw.community.adapters.http.skill_center.batch_sync as bs_module
        original_dir = bs_module.REPORT_DIR
        bs_module.REPORT_DIR = tmp_path
        try:
            report_file = tmp_path / "disk-task.md"
            report_file.write_text("# Report", encoding="utf-8")
            resp = client.get("/api/v1/skill-center/batch-sync/status/disk-task")
            assert resp.status_code == 200
            assert resp.json()["status"] == "done"
        finally:
            bs_module.REPORT_DIR = original_dir


# ---------------------------------------------------------------------------
# GET /batch-sync/report/{task_id}
# ---------------------------------------------------------------------------

class TestBatchSyncReport:
    def test_report_from_memory(self, client, tmp_path):
        """从内存中获取报告路径。"""
        import agentclaw.community.adapters.http.skill_center.batch_sync as bs_module
        original_dir = bs_module.REPORT_DIR
        bs_module.REPORT_DIR = tmp_path
        try:
            report_file = tmp_path / "task-abc.md"
            report_file.write_text("# Sync Report\nAll good", encoding="utf-8")
            _task_reports["task-abc"] = str(report_file)
            resp = client.get("/api/v1/skill-center/batch-sync/report/task-abc")
            assert resp.status_code == 200
            data = resp.json()
            assert data["task_id"] == "task-abc"
            assert "All good" in data["content"]
        finally:
            bs_module.REPORT_DIR = original_dir

    def test_report_from_disk(self, client, tmp_path):
        """内存中没有，从磁盘查找。"""
        import agentclaw.community.adapters.http.skill_center.batch_sync as bs_module
        original_dir = bs_module.REPORT_DIR
        bs_module.REPORT_DIR = tmp_path
        try:
            report_file = tmp_path / "task-disk.md"
            report_file.write_text("# Disk Report", encoding="utf-8")
            resp = client.get("/api/v1/skill-center/batch-sync/report/task-disk")
            assert resp.status_code == 200
            assert "Disk Report" in resp.json()["content"]
        finally:
            bs_module.REPORT_DIR = original_dir

    def test_report_not_found(self, client):
        """报告不存在 → 404。"""
        resp = client.get("/api/v1/skill-center/batch-sync/report/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# _run_batch_sync_background — 后台任务逻辑
# ---------------------------------------------------------------------------

class TestRunBatchSyncBackground:
    @pytest.mark.asyncio
    async def test_background_task_success(self, mock_batch_sync_svc, mock_git_sync, tmp_path):
        """后台任务成功执行，更新 _tasks 状态。"""
        from agentclaw.community.adapters.http.skill_center.batch_sync import _run_batch_sync_background

        _tasks["bg-task-1"] = {"status": "running", "progress": "starting...", "result": None, "error": ""}
        with patch(f"{_BATCH_SYNC_MODULE}.generate_report"):
            with patch(f"{_BATCH_SYNC_MODULE}.REPORT_DIR", tmp_path):
                await _run_batch_sync_background(
                    "bg-task-1", None, False, None, None, 0,
                    mock_git_sync, mock_batch_sync_svc,
                )

        assert _tasks["bg-task-1"]["status"] == "done"
        assert _tasks["bg-task-1"]["result"] is not None

    @pytest.mark.asyncio
    async def test_background_task_batch_index_out_of_range(self, mock_batch_sync_svc, mock_git_sync):
        """batch_index 越界 → error 状态。"""
        from agentclaw.community.adapters.http.skill_center.batch_sync import _run_batch_sync_background

        _tasks["bg-task-3"] = {"status": "running", "progress": "starting...", "result": None, "error": ""}
        await _run_batch_sync_background(
            "bg-task-3", None, False, None, 2, 10,
            mock_git_sync, mock_batch_sync_svc,
        )

        assert _tasks["bg-task-3"]["status"] == "error"
        assert "out of range" in _tasks["bg-task-3"]["error"]

    @pytest.mark.asyncio
    async def test_background_task_sets_progress(self, mock_batch_sync_svc, mock_git_sync, tmp_path):
        """分批模式下设置 progress。"""
        from agentclaw.community.adapters.http.skill_center.batch_sync import _run_batch_sync_background

        _tasks["bg-task-4"] = {"status": "running", "progress": "starting...", "result": None, "error": ""}
        with patch(f"{_BATCH_SYNC_MODULE}.generate_report"):
            with patch(f"{_BATCH_SYNC_MODULE}.REPORT_DIR", tmp_path):
                await _run_batch_sync_background(
                    "bg-task-4", None, False, None, 2, 0,
                    mock_git_sync, mock_batch_sync_svc,
                )

        assert _tasks["bg-task-4"]["status"] == "done"
        assert "completed" in _tasks["bg-task-4"]["progress"].lower()


# ---------------------------------------------------------------------------
# _start_batch_sync_task — task 创建
# ---------------------------------------------------------------------------

class TestStartBatchSyncTask:
    def test_creates_task_in_tasks_dict(self, mock_batch_sync_svc, mock_git_sync):
        """_start_batch_sync_task 在 _tasks 中注册任务。"""
        from agentclaw.community.adapters.http.skill_center.batch_sync import _start_batch_sync_task

        with patch(f"{_BATCH_SYNC_MODULE}.asyncio.create_task"):
            result = _start_batch_sync_task(
                None, False, None, None, 0, mock_git_sync, mock_batch_sync_svc,
            )

        assert result.task_id
        assert result.status == "running"
        assert result.task_id in _tasks
        assert _tasks[result.task_id]["status"] == "running"
