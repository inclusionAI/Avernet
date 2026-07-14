"""
Tests for G2 Conflict Alignment Flow

G2: Conflict Alignment Layer - 集成测试

测试 G2 HTTP/CLI 集成。
"""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from src.domain.models.fusion_result import Perspective
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
from src.interfaces.api.fusion_routes import router, set_provider
from fastapi import FastAPI


# =============================================================================
# Test Fixtures
# =============================================================================

class MockG2PerspectiveProvider(PerspectiveProvider):
    """G2 测试用的 Mock Provider"""

    def __init__(self, responses: dict[str, Perspective] = None):
        self._responses = responses or {}

    def collect(self, context: PerspectiveContext) -> Perspective:
        participant_id = context.participant_id
        if participant_id in self._responses:
            return self._responses[participant_id]

        # 默认响应
        return Perspective(
            participant_id=participant_id,
            participant_type="bot",
            role="consultant",
            summary=f"Default response for {participant_id}",
            status="completed",
        )


@pytest.fixture
def client():
    """创建测试客户端"""
    # 禁用参与者可用性检查，避免 registry store 检查
    os.environ["ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING"] = "false"

    # 重置 FeatureFlags 单例，使其重新读取环境变量
    from src.infra.config.feature_flags import FeatureFlags
    FeatureFlags.reset()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def g2_timeout_conflict_provider():
    """超时冲突场景的 Provider"""
    return MockG2PerspectiveProvider({
        "zhangsan": Perspective(
            participant_id="zhangsan",
            participant_type="bot",
            role="driver",
            summary="开发者视角：当前代码实现为60分钟超时",
            key_points=["兼容旧系统", "避免大规模重构"],
            concerns=["改造成本"],
            flexibility="愿意分阶段改造",
            status="completed",
        ),
        "lisi": Perspective(
            participant_id="lisi",
            participant_type="bot",
            role="consultant",
            summary="PM视角：PRD要求30分钟超时",
            key_points=["用户体验"],
            concerns=["用户等待焦虑"],
            flexibility="理解兼容性考虑",
            status="completed",
        ),
        "anquan": Perspective(
            participant_id="anquan",
            participant_type="bot",
            role="consultant",
            summary="安全视角：60分钟存在会话劫持风险",
            key_points=["安全合规"],
            concerns=["会话安全"],
            flexibility="如果必须60分钟，需加二次确认",
            status="completed",
        ),
    })


@pytest.fixture
def g2_consensus_provider():
    """共识场景的 Provider"""
    return MockG2PerspectiveProvider({
        "dev": Perspective(
            participant_id="dev",
            participant_type="bot",
            role="consultant",
            summary="方案可行，技术上没问题",
            status="completed",
        ),
        "pm": Perspective(
            participant_id="pm",
            participant_type="bot",
            role="consultant",
            summary="方案可行，符合产品需求",
            status="completed",
        ),
    })


# =============================================================================
# HTTP Tests
# =============================================================================

class TestG2HTTPEndpoint:
    """G2 HTTP 端点测试"""

    def test_g2_http_endpoint_exists(self, client: TestClient):
        """测试 G2 端点存在"""
        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["dev"],
                "fusion_mode": "conflict_alignment",
                "options": {"strict_participants": False},  # 非严格模式，允许未注册参与者
            },
        )
        # 不应该是 404
        assert response.status_code != 404

    def test_g2_http_happy_path(self, client: TestClient, g2_timeout_conflict_provider):
        """测试 G2 HTTP happy path"""
        set_provider(g2_timeout_conflict_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "如何协调代码与PRD的超时时间冲突？",
                "participants": ["zhangsan", "lisi", "anquan"],
                "fusion_mode": "conflict_alignment",
                "options": {"strict_participants": False},  # 非严格模式，允许未注册参与者
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证基本字段
        assert data["group_id"] == "grp-fusion-001"
        assert data["fusion_mode"] == "conflict_alignment"
        assert data["question"] == "如何协调代码与PRD的超时时间冲突？"

        # 验证 G2 字段存在
        assert "conflicts" in data
        assert "alignment_points" in data
        assert "key_insights" in data

        # 验证 perspectives
        assert len(data["perspectives"]) == 3

        # 验证 conflicts 检测
        assert len(data["conflicts"]) >= 1

    def test_g2_http_with_driver_bot_id(self, client: TestClient, g2_timeout_conflict_provider):
        """测试 G2 HTTP 指定 driver_bot_id"""
        set_provider(g2_timeout_conflict_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["zhangsan", "lisi"],
                "driver_bot_id": "zhangsan",
                "fusion_mode": "conflict_alignment",
                "options": {"strict_participants": False},  # 非严格模式，允许未注册参与者
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["driver_bot_id"] == "zhangsan"

    def test_g2_http_partial_success(self, client: TestClient):
        """测试 G2 HTTP partial success"""
        partial_provider = MockG2PerspectiveProvider({
            "dev": Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="ok",
                status="completed",
            ),
            "security": Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="",
                status="failed",
            ),
        })
        set_provider(partial_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["dev", "security"],
                "fusion_mode": "conflict_alignment",
                "options": {"strict_participants": False},  # 非严格模式，允许未注册参与者
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["partial_success"] is True
        assert len(data["warnings"]) > 0

    def test_g2_http_consensus_scenario(self, client: TestClient, g2_consensus_provider):
        """测试 G2 HTTP 共识场景"""
        set_provider(g2_consensus_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "方案是否可行？",
                "participants": ["dev", "pm"],
                "fusion_mode": "conflict_alignment",
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 无冲突
        assert len(data["conflicts"]) == 0
        # 有对齐点
        assert len(data["alignment_points"]) >= 1


class TestG2G1ModeSwitch:
    """G1/G2 模式切换测试"""

    def test_g1_mode_still_works(self, client: TestClient, g2_consensus_provider):
        """测试 G1 模式仍然可用"""
        set_provider(g2_consensus_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["dev", "pm"],
                # fusion_mode 默认是 agent
            },
        )

        assert response.status_code == 200
        data = response.json()

        # G1 模式
        assert data["fusion_mode"] == "agent"
        # G2 字段为空
        assert data["conflicts"] == []
        assert data["alignment_points"] == []
        assert data["key_insights"] == []

    def test_explicit_g1_mode(self, client: TestClient, g2_consensus_provider):
        """测试显式指定 G1 模式"""
        set_provider(g2_consensus_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["dev", "pm"],
                "fusion_mode": "agent",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fusion_mode"] == "agent"

    def test_g1_g2_mode_switch(self, client: TestClient, g2_timeout_conflict_provider):
        """测试 G1/G2 模式切换"""
        set_provider(g2_timeout_conflict_provider)

        # G2 请求
        g2_response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["zhangsan", "lisi"],
                "fusion_mode": "conflict_alignment",
            },
        )
        g2_data = g2_response.json()
        assert g2_data["fusion_mode"] == "conflict_alignment"

        # G1 请求
        g1_response = client.post(
            "/api/v1/groups/grp-fusion-002/fuse",
            json={
                "question": "test",
                "participants": ["zhangsan", "lisi"],
                "fusion_mode": "agent",
            },
        )
        g1_data = g1_response.json()
        assert g1_data["fusion_mode"] == "agent"
        assert g1_data["conflicts"] == []

    def test_g1_not_polluted_by_g2(self, client: TestClient, g2_timeout_conflict_provider):
        """测试 G1 不被 G2 污染"""
        set_provider(g2_timeout_conflict_provider)

        # 先发送 G2 请求
        client.post(
            "/api/v1/groups/grp-g2/fuse",
            json={
                "question": "G2 test",
                "participants": ["zhangsan", "lisi"],
                "fusion_mode": "conflict_alignment",
            },
        )

        # 再发送 G1 请求
        g1_response = client.post(
            "/api/v1/groups/grp-g1/fuse",
            json={
                "question": "G1 test",
                "participants": ["zhangsan", "lisi"],
                "fusion_mode": "agent",
            },
        )
        g1_data = g1_response.json()

        # G1 结果应该是 G1 模式
        assert g1_data["fusion_mode"] == "agent"
        assert g1_data["conflicts"] == []
        assert g1_data["alignment_points"] == []
        assert g1_data["key_insights"] == []


class TestG2ErrorScenarios:
    """G2 错误场景测试"""

    def test_invalid_fusion_mode(self, client: TestClient):
        """测试无效的 fusion_mode"""
        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["dev"],
                "fusion_mode": "invalid_mode",
            },
        )

        assert response.status_code == 422

    def test_missing_question(self, client: TestClient):
        """测试缺少 question"""
        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "participants": ["dev"],
                "fusion_mode": "conflict_alignment",
            },
        )

        assert response.status_code == 422

    def test_missing_participants(self, client: TestClient):
        """测试缺少 participants"""
        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "fusion_mode": "conflict_alignment",
            },
        )

        assert response.status_code == 422

    def test_invalid_group_id(self, client: TestClient):
        """测试无效的 group_id"""
        response = client.post(
            "/api/v1/groups/invalid-group-id/fuse",
            json={
                "question": "test",
                "participants": ["dev"],
                "fusion_mode": "conflict_alignment",
            },
        )

        # FastAPI Path 参数校验返回 422
        assert response.status_code in [400, 422]


class TestG2ResponseStructure:
    """G2 响应结构测试"""

    def test_response_has_all_g2_fields(self, client: TestClient, g2_timeout_conflict_provider):
        """测试响应包含所有 G2 字段"""
        set_provider(g2_timeout_conflict_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["zhangsan", "lisi"],
                "fusion_mode": "conflict_alignment",
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证所有必需字段
        required_fields = [
            "group_id", "fusion_id", "question", "perspectives",
            "partial_success", "warnings", "errors", "timing",
            "fusion_mode", "conflicts", "alignment_points", "key_insights",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_perspective_has_g2_fields(self, client: TestClient, g2_timeout_conflict_provider):
        """测试 Perspective 包含 G2 字段"""
        set_provider(g2_timeout_conflict_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "test",
                "participants": ["zhangsan"],
                "fusion_mode": "conflict_alignment",
            },
        )

        assert response.status_code == 200
        data = response.json()

        perspective = data["perspectives"][0]
        g2_fields = ["key_points", "concerns", "flexibility"]
        for field in g2_fields:
            assert field in perspective, f"Missing perspective field: {field}"

    def test_conflict_structure(self, client: TestClient, g2_timeout_conflict_provider):
        """测试 Conflict 结构"""
        set_provider(g2_timeout_conflict_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "如何协调代码与PRD的超时时间冲突？",
                "participants": ["zhangsan", "lisi", "anquan"],
                "fusion_mode": "conflict_alignment",
            },
        )

        assert response.status_code == 200
        data = response.json()

        if len(data["conflicts"]) > 0:
            conflict = data["conflicts"][0]
            assert "parties" in conflict
            assert "issue" in conflict
            assert "positions" in conflict
            assert "severity" in conflict

    def test_alignment_point_structure(self, client: TestClient, g2_consensus_provider):
        """测试 AlignmentPoint 结构"""
        set_provider(g2_consensus_provider)

        response = client.post(
            "/api/v1/groups/grp-fusion-001/fuse",
            json={
                "question": "方案是否可行？",
                "participants": ["dev", "pm"],
                "fusion_mode": "conflict_alignment",
            },
        )

        assert response.status_code == 200
        data = response.json()

        if len(data["alignment_points"]) > 0:
            alignment = data["alignment_points"][0]
            assert "summary" in alignment
            # participants 是可选的