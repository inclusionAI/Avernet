"""
Tests for Fusion API Contract

G1: Fusion Entry Layer

测试 Fusion API 的 schema 契约。
"""

from __future__ import annotations

import pytest


class TestFusionAPIContractModule:
    """模块存在性测试"""

    def test_fusion_routes_module_exists(self):
        """测试 fusion_routes 模块存在"""
        import importlib

        module = importlib.import_module("src.interfaces.api.fusion_routes")
        assert module is not None


class TestFuseRequestSchema:
    """FuseRequest Schema 测试"""

    def test_request_valid_json(self):
        """测试有效 JSON 请求"""
        from src.domain.models.fusion_request import FusionRequest

        data = {
            "question": "这个方案是否可行",
            "participants": ["zhangsan", "dba", "security"],
            "driver_bot_id": "zhangsan",
        }

        request = FusionRequest(**data)
        assert request.question == "这个方案是否可行"
        assert len(request.participants) == 3

    def test_request_with_options(self):
        """测试带 options 的请求"""
        from src.domain.models.fusion_request import FusionRequest

        data = {
            "question": "test",
            "participants": ["dba"],
            "options": {
                "timeout_ms": 30000,
                "parallel": False,
                "include_recommendation": True,
                "strict_participants": True,
            },
        }

        request = FusionRequest(**data)
        assert request.options.timeout_ms == 30000

    def test_request_with_metadata(self):
        """测试带 metadata 的请求"""
        from src.domain.models.fusion_request import FusionRequest

        data = {
            "question": "test",
            "participants": ["dba"],
            "metadata": {
                "request_id": "req-001",
                "source": "bcs-cli",
            },
        }

        request = FusionRequest(**data)
        assert request.metadata.request_id == "req-001"


class TestFuseResponseSchema:
    """FuseResponse Schema 测试"""

    def test_response_valid_json(self):
        """测试有效 JSON 响应"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )

        # 验证可以序列化为 dict
        data = result.model_dump()
        assert data["group_id"] == "grp-001"
        assert data["fusion_id"] == "fus-001"

    def test_response_with_perspectives(self):
        """测试带 perspectives 的响应"""
        from src.domain.models.fusion_result import FusionResult, Perspective, FusionTiming

        perspective = Perspective(
            participant_id="dba",
            participant_type="bot",
            role="consultant",
            summary="从数据库角度可行",
            confidence=0.85,
            status="completed",
        )

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[perspective],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )

        data = result.model_dump()
        assert len(data["perspectives"]) == 1
        assert data["perspectives"][0]["participant_id"] == "dba"

    def test_response_with_recommendation(self):
        """测试带 recommendation 的响应"""
        from src.domain.models.fusion_result import (
            FusionResult,
            Recommendation,
            FusionTiming,
        )

        recommendation = Recommendation(
            summary="方案可行",
            decision="yes",
            risks=[],
            next_actions=[],
        )

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            recommendation=recommendation,
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )

        data = result.model_dump()
        assert data["recommendation"] is not None
        assert data["recommendation"]["decision"] == "yes"


class TestErrorSchema:
    """错误响应 Schema 测试"""

    def test_error_response_structure(self):
        """测试错误响应结构"""
        from src.domain.models.fusion_result import FusionError

        error = FusionError(
            code="PARTICIPANT_NOT_FOUND",
            message="Participant not found: unknown",
        )

        data = error.model_dump()
        assert data["code"] == "PARTICIPANT_NOT_FOUND"
        assert "not found" in data["message"]

    def test_error_with_details(self):
        """测试带详情的错误"""
        from src.domain.models.fusion_result import FusionError

        error = FusionError(
            code="VALIDATION_ERROR",
            message="Validation failed",
            details=["participants must not be empty", "question is required"],
        )

        data = error.model_dump()
        assert len(data["details"]) == 2


class TestAPIEndpointContract:
    """API 端点契约测试"""

    def test_api_endpoint_exists(self):
        """测试 API 端点存在"""
        from src.interfaces.api.fusion_routes import router

        # 查找 fuse 端点
        routes = [route.path for route in router.routes]
        assert "/groups/{group_id}/fuse" in routes

    def test_api_endpoint_methods(self):
        """测试 API 端点方法"""
        from src.interfaces.api.fusion_routes import router

        # 查找 fuse 端点的 HTTP 方法
        for route in router.routes:
            if hasattr(route, "path") and route.path == "/groups/{group_id}/fuse":
                # FastAPI 路由会包含方法信息
                assert route.methods is not None
                assert "POST" in route.methods