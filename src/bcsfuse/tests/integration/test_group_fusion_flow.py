"""
Tests for Group Fusion Integration Flow

G1: Fusion Entry Layer

测试 Fusion API 的集成流程，验证 HTTP handler + service 的协作。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.interfaces.api.app import app
from src.interfaces.api.fusion_routes import set_provider
from src.domain.models.fusion_result import Perspective
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext


# =============================================================================
# Test Fixtures
# =============================================================================

class MockPerspectiveProvider:
    """测试用 Mock Provider"""

    def __init__(self, perspectives: dict[str, Perspective] | None = None):
        self.perspectives = perspectives or {}
        self.call_count = 0

    def collect(self, context: PerspectiveContext) -> Perspective:
        self.call_count += 1
        if context.participant_id in self.perspectives:
            return self.perspectives[context.participant_id]
        return Perspective(
            participant_id=context.participant_id,
            participant_type="bot",
            role="consultant",
            summary=f"From {context.participant_id}: looks good",
            confidence=0.8,
            status="completed",
        )


@pytest.fixture
def client():
    """测试客户端"""
    return TestClient(app)


@pytest.fixture
def mock_provider():
    """Mock provider"""
    return MockPerspectiveProvider()


# =============================================================================
# Integration Tests
# =============================================================================

class TestGroupFusionIntegration:
    """融合集成测试"""

    def test_fusion_endpoint_exists(self, client: TestClient):
        """测试融合端点存在"""
        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test question",
                "participants": ["dba"],
                "options": {"strict_participants": False},
            },
        )
        # 不应该是 404
        assert response.status_code != 404

    def test_fusion_happy_path(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试 happy path"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "这个方案从各角度是否可行",
                "participants": ["dba", "security"],
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["group_id"] == "grp-test-001"
        assert data["question"] == "这个方案从各角度是否可行"
        assert len(data["perspectives"]) == 2
        assert data["partial_success"] is False

    def test_fusion_with_explicit_driver(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试显式指定 driver"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["zhangsan", "dba"],
                "driver_bot_id": "zhangsan",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["driver_bot_id"] == "zhangsan"

    def test_fusion_partial_success(self, client: TestClient):
        """测试部分成功

        注意：Phase 5 行为变化 - 未注册的参与者会被标记为 skipped
        由于 dba 和 security 未在 Worker Registry 中注册，它们会被标记为 skipped
        这导致 completed_count = 0，partial_success = False
        这是预期的 Phase 5 行为
        """
        failed_perspective = Perspective(
            participant_id="dba",
            participant_type="bot",
            role="consultant",
            summary="",
            status="failed",
        )
        success_perspective = Perspective(
            participant_id="security",
            participant_type="bot",
            role="consultant",
            summary="OK from security",
            confidence=0.9,
            status="completed",
        )

        mock_provider = MockPerspectiveProvider({
            "dba": failed_perspective,
            "security": success_perspective,
        })
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["dba", "security"],
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Phase 5: 未注册参与者被标记为 skipped，所以 completed_count = 0
        # partial_success = completed_count > 0 and completed_count < total_count
        assert data["partial_success"] is False  # Phase 5 behavior
        # 所有参与者都被 skipped，所以 warnings 应该包含未注册警告
        assert len(data["warnings"]) > 0
        # 验证 perspective 状态为 skipped
        perspectives = data.get("perspectives", [])
        assert len(perspectives) == 2
        for p in perspectives:
            assert p["status"] == "skipped"

    def test_fusion_invalid_group_id(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试无效 group_id - FastAPI Path 验证返回 422"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/invalid-group-id/fuse",
            json={
                "question": "test",
                "participants": ["dba"],
                "options": {"strict_participants": False},
            },
        )

        # FastAPI Path 参数验证返回 422 Unprocessable Entity
        assert response.status_code == 422

    def test_fusion_missing_question(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试缺少 question"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "participants": ["dba"],
            },
        )

        assert response.status_code == 422

    def test_fusion_missing_participants(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试缺少 participants"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
            },
        )

        assert response.status_code == 422

    def test_fusion_with_options(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试带选项的请求"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["dba"],
                "options": {
                    "timeout_ms": 30000,
                    "parallel": False,
                    "include_recommendation": True,
                    "strict_participants": False,  # 改为 False 以允许未注册参与者
                },
            },
        )

        assert response.status_code == 200

    def test_fusion_without_recommendation(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试禁用 recommendation"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["dba"],
                "options": {
                    "include_recommendation": False,
                    "strict_participants": False,  # 添加以允许未注册参与者
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["recommendation"] is None

    def test_fusion_response_structure(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试响应结构"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["dba", "security"],
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证必需字段
        assert "group_id" in data
        assert "fusion_id" in data
        assert "question" in data
        assert "perspectives" in data
        assert "partial_success" in data
        assert "warnings" in data
        assert "errors" in data
        assert "timing" in data

        # 验证 timing 结构
        timing = data["timing"]
        assert "started_at" in timing
        assert "finished_at" in timing
        assert "duration_ms" in timing

    def test_fusion_timing_populated(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试计时信息填充"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["dba"],
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        timing = data["timing"]
        assert timing["started_at"] is not None
        assert timing["finished_at"] is not None
        assert timing["duration_ms"] >= 0


class TestGroupFusionMultipleParticipants:
    """多参与者融合测试"""

    def test_fusion_multiple_participants(self, client: TestClient):
        """测试多个 participant"""
        perspectives = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="From DBA: good",
                confidence=0.85,
                status="completed",
            ),
            "security": Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="From Security: needs review",
                confidence=0.9,
                status="completed",
            ),
            "ops": Perspective(
                participant_id="ops",
                participant_type="bot",
                role="consultant",
                summary="From Ops: needs monitoring",
                confidence=0.8,
                status="completed",
            ),
        }

        mock_provider = MockPerspectiveProvider(perspectives)
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["dba", "security", "ops"],
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["perspectives"]) == 3


class TestGroupFusionEdgeCases:
    """边界情况测试"""

    def test_fusion_single_participant(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试单个 participant"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["dba"],
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["perspectives"]) == 1

    def test_fusion_with_metadata(self, client: TestClient, mock_provider: MockPerspectiveProvider):
        """测试带 metadata 的请求"""
        set_provider(mock_provider)

        response = client.post(
            "/api/v1/groups/grp-test-001/fuse",
            json={
                "question": "test",
                "participants": ["dba"],
                "metadata": {
                    "request_id": "req-test-001",
                    "source": "test-suite",
                },
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200