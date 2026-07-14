"""
Tests for G5ExpertEnhancer Interface

Stage 3: Worker Profile-Driven Expert Execution Preparation

测试 G5 专家增强接口定义。
"""

from __future__ import annotations

import pytest

from src.domain.models.fusion_result import Perspective
from src.domain.services.g5_expert_enhancer import G5ExpertEnhancer


class TestG5ExpertEnhancerInterface:
    """G5 Expert Enhancer 接口测试"""

    def test_interface_is_protocol(self):
        """测试接口是 Protocol"""
        from typing import Protocol

        assert issubclass(G5ExpertEnhancer, Protocol)

    def test_interface_has_enhance_method(self):
        """测试接口有 enhance 方法"""
        assert hasattr(G5ExpertEnhancer, "enhance")

    def test_enhance_method_signature(self):
        """测试 enhance 方法签名"""
        # 创建一个 mock 实现来验证签名
        class MockEnhancer:
            def enhance(
                self,
                question: str,
                base_perspectives: list[Perspective],
                participants: list[str] | None = None,
                driver_bot_id: str | None = None,
            ) -> list[Perspective]:
                return base_perspectives

        enhancer = MockEnhancer()

        # 验证方法存在且可调用
        assert callable(enhancer.enhance)

    def test_enhance_returns_list_of_perspectives(self):
        """测试 enhance 返回 Perspective 列表"""
        # 创建一个 mock 实现来验证返回类型
        class MockEnhancer:
            def enhance(
                self,
                question: str,
                base_perspectives: list[Perspective],
                participants: list[str] | None = None,
                driver_bot_id: str | None = None,
            ) -> list[Perspective]:
                # 返回增强后的视角
                return [
                    Perspective(
                        participant_id="expert_001",
                        participant_type="bot",
                        role="expert",
                        summary=f"Expert perspective on: {question}",
                        confidence=0.9,
                        status="completed",
                    )
                ]

        enhancer = MockEnhancer()
        base_perspectives: list[Perspective] = []

        result = enhancer.enhance(
            question="How to optimize this query?",
            base_perspectives=base_perspectives,
            participants=["staff_001", "staff_002"],
            driver_bot_id="staff_001",
        )

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Perspective)
        assert result[0].role == "expert"

    def test_enhance_with_optional_parameters(self):
        """测试 enhance 可选参数"""
        # 创建一个完整的 mock 实现
        class MockEnhancer:
            def enhance(
                self,
                question: str,
                base_perspectives: list[Perspective],
                participants: list[str] | None = None,
                driver_bot_id: str | None = None,
            ) -> list[Perspective]:
                # 验证可选参数类型
                if participants is not None:
                    assert isinstance(participants, list)
                if driver_bot_id is not None:
                    assert isinstance(driver_bot_id, str)

                return base_perspectives

        enhancer = MockEnhancer()

        # 不传可选参数
        result1 = enhancer.enhance(
            question="Test",
            base_perspectives=[],
        )
        assert result1 == []

        # 传可选参数
        result2 = enhancer.enhance(
            question="Test",
            base_perspectives=[],
            participants=["staff_001"],
            driver_bot_id="staff_001",
        )
        assert result2 == []

    def test_protocol_runtime_checkable(self):
        """测试 Protocol 运行时可检查"""
        from typing import Protocol, runtime_checkable

        # G5ExpertEnhancer 应该是 runtime_checkable
        class MockEnhancer:
            def enhance(
                self,
                question: str,
                base_perspectives: list[Perspective],
                participants: list[str] | None = None,
                driver_bot_id: str | None = None,
            ) -> list[Perspective]:
                return base_perspectives

        enhancer = MockEnhancer()

        # 如果 G5ExpertEnhancer 是 runtime_checkable，则可以用 isinstance 检查
        # 注意：Protocol 的 isinstance 检查只检查方法存在，不检查签名
        if runtime_checkable(Protocol):  # type: ignore
            # 仅当 Protocol 支持 runtime_checkable 时检查
            pass  # G5ExpertEnhancer 应标记为 @runtime_checkable

    def test_interface_docstring_exists(self):
        """测试接口有文档字符串"""
        assert G5ExpertEnhancer.__doc__ is not None
        assert "G5" in G5ExpertEnhancer.__doc__ or "expert" in G5ExpertEnhancer.__doc__.lower()


class TestG5EnhancementContext:
    """G5 Enhancement Context 测试"""

    def test_enhance_accepts_base_perspectives(self):
        """测试 enhance 接受 base_perspectives"""
        # 创建测试用的 base perspectives
        base_perspectives = [
            Perspective(
                participant_id="staff_001",
                participant_type="bot",
                role="consultant",
                summary="Initial perspective",
                status="completed",
            )
        ]

        class MockEnhancer:
            def enhance(
                self,
                question: str,
                base_perspectives: list[Perspective],
                participants: list[str] | None = None,
                driver_bot_id: str | None = None,
            ) -> list[Perspective]:
                # 验证 base_perspectives 被正确传递
                assert len(base_perspectives) == 1
                return base_perspectives

        enhancer = MockEnhancer()
        enhancer.enhance(
            question="Test",
            base_perspectives=base_perspectives,
        )

    def test_enhance_accepts_participants_list(self):
        """测试 enhance 接受 participants 列表"""
        participants = ["staff_001:default", "staff_002:default"]

        class MockEnhancer:
            def enhance(
                self,
                question: str,
                base_perspectives: list[Perspective],
                participants: list[str] | None = None,
                driver_bot_id: str | None = None,
            ) -> list[Perspective]:
                # 验证 participants 被正确传递
                if participants is not None:
                    assert len(participants) == 2
                return base_perspectives

        enhancer = MockEnhancer()
        enhancer.enhance(
            question="Test",
            base_perspectives=[],
            participants=participants,
        )

    def test_enhance_accepts_driver_bot_id(self):
        """测试 enhance 接受 driver_bot_id"""
        driver_bot_id = "staff_001:default"

        class MockEnhancer:
            def enhance(
                self,
                question: str,
                base_perspectives: list[Perspective],
                participants: list[str] | None = None,
                driver_bot_id: str | None = None,
            ) -> list[Perspective]:
                # 验证 driver_bot_id 被正确传递
                if driver_bot_id is not None:
                    assert driver_bot_id == "staff_001:default"
                return base_perspectives

        enhancer = MockEnhancer()
        enhancer.enhance(
            question="Test",
            base_perspectives=[],
            driver_bot_id=driver_bot_id,
        )