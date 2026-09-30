"""
Tests for G5 Expert Diagnosis Flow

G5: Expert Diagnosis Layer - 集成测试

测试 G5 HTTP/CLI 集成。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.domain.models.fusion_result import Perspective
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
from src.interfaces.api.fusion_routes import router, set_provider
from fastapi import FastAPI


# =============================================================================
# Test Fixtures
# =============================================================================

class MockG5PerspectiveProvider(PerspectiveProvider):
    """G5 测试用的 Mock Provider"""

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
            role="expert",
            summary=f"Default expert response for {participant_id}",
            status="completed",
        )


@pytest.fixture
def client():
    """创建测试客户端"""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def g5_security_expert_provider():
    """安全专家场景的 Provider"""
    return MockG5PerspectiveProvider({
        "anquan": Perspective(
            participant_id="anquan",
            participant_type="bot",
            role="expert",
            summary="存在安全风险，建议上线前修复",
            key_points=["SQL注入风险", "权限控制不完善"],
            concerns=["用户数据可能泄露"],
            confidence=0.9,
            evidence=["审计报告显示存在未过滤的用户输入"],
            status="completed",
        ),
        "fawu": Perspective(
            participant_id="fawu",
            participant_type="bot",
            role="expert",
            summary="合规风险较高，需要补充协议",
            key_points=["用户协议缺失"],
            concerns=["可能违反数据保护法规"],
            confidence=0.85,
            evidence=["缺少隐私政策弹窗"],
            status="completed",
        ),
        "dba": Perspective(
            participant_id="dba",
            participant_type="bot",
            role="expert",
            summary="数据库设计合理，无重大风险",
            key_points=["索引完善", "备份策略健全"],
            concerns=[],
            confidence=0.95,
            evidence=["已验证备份恢复流程"],
            status="completed",
        ),
    })


@pytest.fixture
def g5_low_risk_provider():
    """低风险场景的 Provider"""
    return MockG5PerspectiveProvider({
        "tech": Perspective(
            participant_id="tech",
            participant_type="bot",
            role="expert",
            summary="技术方案可行，可以使用",
            key_points=["代码质量良好", "测试覆盖充分"],
            concerns=[],
            confidence=0.95,
            status="completed",
        ),
        "ops": Perspective(
            participant_id="ops",
            participant_type="bot",
            role="expert",
            summary="运维检查通过，可以上线",
            key_points=["监控完善", "灰度发布方案合理"],
            concerns=[],
            confidence=0.9,
            status="completed",
        ),
    })


@pytest.fixture
def g5_critical_risk_provider():
    """严重风险场景的 Provider"""
    return MockG5PerspectiveProvider({
        "security": Perspective(
            participant_id="security",
            participant_type="bot",
            role="expert",
            summary="存在严重安全漏洞，禁止上线",
            key_points=["远程代码执行漏洞", "敏感信息明文传输"],
            concerns=["系统可被完全控制"],
            confidence=0.99,
            evidence=["漏洞扫描报告 CRITICAL-001"],
            status="completed",
        ),
        "legal": Perspective(
            participant_id="legal",
            participant_type="bot",
            role="expert",
            summary="法律风险极高，需要立即整改",
            key_points=["违反用户协议", "数据跨境传输违规"],
            concerns=["可能面临重大罚款"],
            confidence=0.95,
            evidence=["法务审核意见书"],
            status="completed",
        ),
    })


# =============================================================================
# HTTP Tests
# =============================================================================

class TestG5HTTPEndpoint:
    """G5 HTTP 端点测试"""

    def test_g5_http_endpoint_exists(self, client: TestClient):
        """测试 G5 端点存在"""
        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )
        # 不应该是 404
        assert response.status_code != 404

    def test_g5_http_happy_path(self, client: TestClient, g5_security_expert_provider):
        """测试 G5 HTTP happy path"""
        set_provider(g5_security_expert_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "这个方案是否可以上线？",
                "participants": ["anquan", "fawu", "dba"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证基本字段
        assert data["group_id"] == "grp-expert-001"
        assert data["fusion_mode"] == "expert_diagnosis"
        assert data["question"] == "这个方案是否可以上线？"

        # 验证 G5 字段存在
        assert "risk_assessment" in data
        assert "critical_issues" in data
        assert "recommendations" in data
        assert "go_live_conditions" in data
        assert "summary" in data

        # 验证 perspectives
        assert len(data["perspectives"]) == 3

        # 验证 risk_assessment 结构
        if data["risk_assessment"]:
            assert "overall" in data["risk_assessment"]
            assert "categories" in data["risk_assessment"]

    def test_g5_http_with_driver_bot_id(self, client: TestClient, g5_security_expert_provider):
        """测试 G5 HTTP 指定 driver_bot_id"""
        set_provider(g5_security_expert_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan", "fawu"],
                "driver_bot_id": "anquan",
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["driver_bot_id"] == "anquan"

    def test_g5_http_partial_success(self, client: TestClient):
        """测试 G5 HTTP partial success"""
        partial_provider = MockG5PerspectiveProvider({
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="expert",
                summary="OK",
                status="completed",
            ),
            "security": Perspective(
                participant_id="security",
                participant_type="bot",
                role="expert",
                summary="",
                status="failed",
            ),
        })
        set_provider(partial_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["dba", "security"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["partial_success"] is True
        assert len(data["warnings"]) > 0

    def test_g5_http_low_risk_scenario(self, client: TestClient, g5_low_risk_provider):
        """测试 G5 HTTP 低风险场景"""
        set_provider(g5_low_risk_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "方案是否可以上线？",
                "participants": ["tech", "ops"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 低风险
        if data["risk_assessment"]:
            assert data["risk_assessment"]["overall"] == "low"

        # 关键问题少
        critical_count = len(data["critical_issues"])
        assert critical_count <= 1

    def test_g5_http_critical_risk_scenario(self, client: TestClient, g5_critical_risk_provider):
        """测试 G5 HTTP 严重风险场景"""
        set_provider(g5_critical_risk_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "这个方案是否可以上线？",
                "participants": ["security", "legal"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 高风险
        if data["risk_assessment"]:
            assert data["risk_assessment"]["overall"] in ["high", "critical"]

        # 关键问题多
        assert len(data["critical_issues"]) >= 1

        # 有 P0 建议
        priorities = [r["priority"] for r in data["recommendations"]]
        assert "P0" in priorities or "P1" in priorities


class TestG5ModeIsolation:
    """G1/G2/G5 模式隔离测试"""

    def test_g1_mode_still_works(self, client: TestClient, g5_low_risk_provider):
        """测试 G1 模式仍然可用"""
        set_provider(g5_low_risk_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["tech", "ops"],
                # fusion_mode 默认是 agent
            },
        )

        assert response.status_code == 200
        data = response.json()

        # G1 模式
        assert data["fusion_mode"] == "agent"
        # G5 字段为空或默认值
        assert data["risk_assessment"] is None
        assert data["critical_issues"] == []
        assert data["recommendations"] == []
        assert data["go_live_conditions"] == []

    def test_g2_mode_still_works(self, client: TestClient, g5_security_expert_provider):
        """测试 G2 模式仍然可用"""
        set_provider(g5_security_expert_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan", "fawu"],
                "fusion_mode": "conflict_alignment",
            },
        )

        assert response.status_code == 200
        data = response.json()

        # G2 模式
        assert data["fusion_mode"] == "conflict_alignment"
        # G2 字段存在，G5 字段为空
        assert "conflicts" in data
        assert "alignment_points" in data
        assert "key_insights" in data
        # G5 字段为空
        assert data["risk_assessment"] is None

    def test_g5_mode_with_expert_role(self, client: TestClient, g5_security_expert_provider):
        """测试 G5 模式专家角色"""
        set_provider(g5_security_expert_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证 perspective 的 role 是 expert
        for p in data["perspectives"]:
            assert p["role"] == "expert"

    def test_g1_g2_g5_mode_switch(self, client: TestClient, g5_security_expert_provider):
        """测试 G1/G2/G5 模式切换"""
        set_provider(g5_security_expert_provider)

        # G5 请求
        g5_response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan", "fawu"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )
        g5_data = g5_response.json()
        assert g5_data["fusion_mode"] == "expert_diagnosis"
        assert g5_data["risk_assessment"] is not None or len(g5_data["critical_issues"]) > 0

        # G2 请求
        g2_response = client.post(
            "/api/v1/groups/grp-expert-002/fuse",
            json={
                "question": "test",
                "participants": ["anquan", "fawu"],
                "fusion_mode": "conflict_alignment",
            },
        )
        g2_data = g2_response.json()
        assert g2_data["fusion_mode"] == "conflict_alignment"
        assert g2_data["risk_assessment"] is None

        # G1 请求
        g1_response = client.post(
            "/api/v1/groups/grp-expert-003/fuse",
            json={
                "question": "test",
                "participants": ["anquan", "fawu"],
                "fusion_mode": "agent",
            },
        )
        g1_data = g1_response.json()
        assert g1_data["fusion_mode"] == "agent"
        assert g1_data["risk_assessment"] is None
        assert g1_data["conflicts"] == []

    def test_g1_not_polluted_by_g5(self, client: TestClient, g5_critical_risk_provider):
        """测试 G1 不被 G5 污染"""
        set_provider(g5_critical_risk_provider)

        # 先发送 G5 请求
        client.post(
            "/api/v1/groups/grp-g5/fuse",
            json={
                "question": "G5 test",
                "participants": ["security", "legal"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        # 再发送 G1 请求
        g1_response = client.post(
            "/api/v1/groups/grp-g1/fuse",
            json={
                "question": "G1 test",
                "participants": ["security", "legal"],
                "fusion_mode": "agent",
            },
        )
        g1_data = g1_response.json()

        # G1 结果应该是 G1 模式
        assert g1_data["fusion_mode"] == "agent"
        assert g1_data["risk_assessment"] is None
        assert g1_data["critical_issues"] == []
        assert g1_data["recommendations"] == []
        assert g1_data["go_live_conditions"] == []


class TestG5ErrorScenarios:
    """G5 错误场景测试"""

    def test_invalid_fusion_mode(self, client: TestClient):
        """测试无效的 fusion_mode"""
        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan"],
                "fusion_mode": "invalid_mode",
            },
        )

        assert response.status_code == 422

    def test_missing_question(self, client: TestClient):
        """测试缺少 question"""
        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "participants": ["anquan"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 422

    def test_missing_participants(self, client: TestClient):
        """测试缺少 participants"""
        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 422

    def test_invalid_group_id(self, client: TestClient):
        """测试无效的 group_id"""
        response = client.post(
            "/api/v1/groups/invalid-group-id/fuse",
            json={
                "question": "test",
                "participants": ["anquan"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        # FastAPI Path 参数校验返回 422
        assert response.status_code in [400, 422]


class TestG5ResponseStructure:
    """G5 响应结构测试"""

    def test_response_has_all_g5_fields(self, client: TestClient, g5_security_expert_provider):
        """测试响应包含所有 G5 字段"""
        set_provider(g5_security_expert_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan", "fawu"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证所有必需字段
        required_fields = [
            "group_id", "fusion_id", "question", "perspectives",
            "partial_success", "warnings", "errors", "timing",
            "fusion_mode", "risk_assessment", "critical_issues",
            "recommendations", "go_live_conditions", "summary",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_perspective_has_g5_fields(self, client: TestClient, g5_security_expert_provider):
        """测试 Perspective 包含 G5 字段"""
        set_provider(g5_security_expert_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        perspective = data["perspectives"][0]
        # G5 perspective 字段
        assert "role" in perspective
        assert perspective["role"] == "expert"
        assert "key_points" in perspective
        assert "concerns" in perspective
        assert "confidence" in perspective
        assert "evidence" in perspective

    def test_risk_assessment_structure(self, client: TestClient, g5_security_expert_provider):
        """测试 RiskAssessment 结构"""
        set_provider(g5_security_expert_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan", "fawu", "dba"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        if data["risk_assessment"]:
            ra = data["risk_assessment"]
            assert "overall" in ra
            assert "categories" in ra
            # overall 值有效
            assert ra["overall"] in ["low", "medium", "high", "critical"]

    def test_critical_issue_structure(self, client: TestClient, g5_critical_risk_provider):
        """测试 CriticalIssue 结构"""
        set_provider(g5_critical_risk_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "这个方案是否可以上线？",
                "participants": ["security", "legal"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        if len(data["critical_issues"]) > 0:
            issue = data["critical_issues"][0]
            assert "issue" in issue
            assert "severity" in issue
            assert "domain" in issue
            assert "source" in issue
            # severity 值有效
            assert issue["severity"] in ["low", "medium", "high", "critical"]

    def test_recommendation_structure(self, client: TestClient, g5_critical_risk_provider):
        """测试 ExpertRecommendation 结构"""
        set_provider(g5_critical_risk_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["security", "legal"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        if len(data["recommendations"]) > 0:
            rec = data["recommendations"][0]
            assert "priority" in rec
            assert "action" in rec
            # priority 值有效
            assert rec["priority"] in ["P0", "P1", "P2"]
            # owner 和 domain 可选
            if rec.get("owner"):
                assert isinstance(rec["owner"], str)
            if rec.get("domain"):
                assert isinstance(rec["domain"], str)


class TestG5RiskAggregation:
    """G5 风险聚合测试"""

    def test_overall_risk_aggregation_critical(self, client: TestClient, g5_critical_risk_provider):
        """测试整体风险聚合 - critical"""
        set_provider(g5_critical_risk_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["security", "legal"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 有高危或严重问题
        if data["risk_assessment"]:
            # 整体风险应为 high 或 critical
            assert data["risk_assessment"]["overall"] in ["high", "critical"]

    def test_overall_risk_aggregation_low(self, client: TestClient, g5_low_risk_provider):
        """测试整体风险聚合 - low"""
        set_provider(g5_low_risk_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["tech", "ops"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 低风险
        if data["risk_assessment"]:
            assert data["risk_assessment"]["overall"] == "low"

    def test_category_mapping(self, client: TestClient, g5_security_expert_provider):
        """测试领域风险映射"""
        set_provider(g5_security_expert_provider)

        response = client.post(
            "/api/v1/groups/grp-expert-001/fuse",
            json={
                "question": "test",
                "participants": ["anquan", "fawu", "dba"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证 categories 映射
        if data["risk_assessment"] and data["risk_assessment"]["categories"]:
            categories = data["risk_assessment"]["categories"]
            # 每个领域都是有效的风险等级
            for domain, level in categories.items():
                assert level in ["low", "medium", "high", "critical"]


# =============================================================================
# CLI Tests (via fuse_command)
# =============================================================================

class TestG5CLI:
    """G5 CLI 测试"""

    def test_g5_cli_command_signature(self):
        """测试 G5 CLI 命令签名"""
        from src.interfaces.cli.bcs_cli import fuse_command, get_default_service
        from src.infra.providers.stub_perspective_provider import StubPerspectiveProvider
        from src.application.services.group_fusion_service import GroupFusionService

        # 使用 stub provider
        provider = StubPerspectiveProvider()
        service = GroupFusionService(provider=provider)

        result = fuse_command(
            group_id="grp-expert-cli-001",
            question="CLI test",
            participants=["anquan"],
            fusion_mode="expert_diagnosis",
            service=service,
        )

        assert result.fusion_mode == "expert_diagnosis"
        assert result.group_id == "grp-expert-cli-001"

    def test_g5_cli_format_output(self):
        """测试 G5 CLI 格式化输出"""
        from src.interfaces.cli.bcs_cli import format_pretty_output
        from src.domain.models.fusion_result import FusionResult, Perspective, Recommendation, FusionTiming
        from src.domain.models.expert_risk_assessment import RiskLevel, RiskAssessment
        from src.domain.models.expert_diagnosis import Priority, CriticalIssue, ExpertRecommendation
        from datetime import datetime

        result = FusionResult(
            group_id="grp-expert-001",
            fusion_id="fusion-expert-001",
            question="这个方案是否可以上线？",
            perspectives=[
                Perspective(
                    participant_id="anquan",
                    participant_type="bot",
                    role="expert",
                    summary="存在安全风险",
                    status="completed",
                ),
            ],
            recommendation=Recommendation(
                summary="不建议直接上线",
                decision="conditional_yes",
            ),
            timing=FusionTiming(
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=100,
            ),
            partial_success=False,
            fusion_mode="expert_diagnosis",
            risk_assessment=RiskAssessment(
                overall=RiskLevel.HIGH,
                categories={"security": RiskLevel.HIGH},
            ),
            critical_issues=[
                CriticalIssue(
                    issue="SQL注入风险",
                    severity=RiskLevel.HIGH,
                    domain="security",
                    source="anquan",
                ),
            ],
            recommendations=[
                ExpertRecommendation(
                    priority=Priority.P0,
                    action="修复SQL注入漏洞",
                    owner="开发团队",
                    domain="security",
                ),
            ],
            go_live_conditions=["完成安全审计", "通过渗透测试"],
            summary="存在安全风险，建议修复后再上线",
        )

        output = format_pretty_output(result)

        # 验证输出包含 G5 特有内容
        assert "Risk Assessment" in output
        assert "Critical Issues" in output
        assert "Expert Recommendations" in output
        assert "Go-Live Conditions" in output
        assert "Summary" in output
        assert "P0" in output
        assert "SQL注入" in output or "SQL" in output