"""
Tests for ProfileAnalyzerService

Unit tests for _parse_raw_response method
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock 依赖模块
mock_infra = MagicMock()
mock_authn = MagicMock()
sys.modules["infra"] = mock_infra
sys.modules["infra.llm"] = MagicMock()
sys.modules["infra.llm.prompts"] = MagicMock()
mock_expert_perspective = MagicMock()
mock_expert_perspective.USER_PROMPT_TEMPLATE = "test template"
sys.modules["infra.llm.prompts.expert_perspective_prompt"] = mock_expert_perspective
sys.modules["authn"] = mock_authn
sys.modules["authn.identity"] = MagicMock()

from src.application.services.profile_analyzer_service import (
    ProfileAnalysisResult,
    ProfileAnalyzerService,
)


class TestProfileAnalyzerServiceParseRawResponse:
    """ProfileAnalyzerService._parse_raw_response 测试"""

    @pytest.fixture
    def service(self):
        """创建服务实例（LLM Gateway 用 mock）"""
        from unittest.mock import MagicMock

        mock_gateway = MagicMock()
        return ProfileAnalyzerService(llm_gateway=mock_gateway)

    def test_parse_full_response(self, service):
        """测试完整响应解析"""
        raw_text = """# Agent Profile

## 职责定位

身份: 刻薄的AI助手，效率优先、质量天花板、工具自动化，Chaotic-good数字精灵Clawd
目标: 成为鸽王终结者、输出质量天花板、吃瓜第一名、让工具变成手脚

## 经验能力

核心能力:
  - 近线引擎: 精通Flink和Kafka流式处理，擅长实时特征聚合
  - 离线引擎: 熟练使用Spark和Hive进行PB级ETL开发
  - 大模型训练: 掌握预训练、SFT、RLHF全链路

## Skill能力

技能:
  - dima-for-teamclaw: TeamClaw团队的Dima工作项管理
  - bug-analyzer: Dima缺陷分析
  - locate-alarm-with-experience: 参考告警历史定位经验文档

## 能力标签

能力标签: 实时计算、特征工程、模型训练、问题排查、数据治理
"""

        result = service._parse_raw_response(raw_text)

        assert result.llm_success is True
        assert result.semantic_profile is not None
        assert "【职责定位】" in result.semantic_profile
        assert "【经验能力】" in result.semantic_profile
        assert "【Skill能力】" in result.semantic_profile
        assert "刻薄的AI助手" in result.semantic_profile
        assert result.capability_tags == [
            "实时计算",
            "特征工程",
            "模型训练",
            "问题排查",
            "数据治理",
        ]

    def test_parse_capability_tags_various_formats(self, service):
        """测试多种分隔符格式的能力标签解析"""
        # 测试顿号分隔
        assert service._extract_capability_tags(
            "## 能力标签\n\n能力标签: 实时计算、特征工程、模型训练"
        ) == ["实时计算", "特征工程", "模型训练"]

        # 测试中文逗号分隔
        assert service._extract_capability_tags(
            "## 能力标签\n\n能力标签：实时计算，特征工程，模型训练"
        ) == ["实时计算", "特征工程", "模型训练"]

        # 测试英文逗号分隔 + 空白
        assert service._extract_capability_tags(
            "## 能力标签\n\n能力标签:   实时计算  ,  特征工程  ,  模型训练"
        ) == ["实时计算", "特征工程", "模型训练"]

        # 测试无前缀格式
        assert service._extract_capability_tags(
            "## 能力标签\n\n实时计算, 特征工程, 模型训练"
        ) == ["实时计算", "特征工程", "模型训练"]

        # 测试空标签
        assert service._extract_capability_tags("## 能力标签\n\n能力标签:") == []

    def test_extract_section(self, service):
        """测试 section 提取（包括存在/不存在场景）"""
        raw_text = """## 职责定位

身份: 测试AI助手

## Skill能力

技能:
  - skill-a: 技能A
"""

        # 正常提取
        section = service._extract_section(raw_text, "职责定位")
        assert "测试AI助手" in section

        # Section 不存在
        assert service._extract_section(raw_text, "经验能力") is None

        # Case insensitive
        section = service._extract_section(raw_text, "[Ss]kill能力")
        assert "skill-a" in section

    def test_build_semantic_profile(self, service):
        """测试语义画像构建（完整/部分）"""
        # 完整场景
        raw_text = """## 职责定位

身份: AI助手

## 经验能力

核心能力:
  - 编程能力

## Skill能力

技能:
  - skill-a: 技能A
"""
        profile = service._build_semantic_profile(raw_text)
        assert "【职责定位】" in profile
        assert "【经验能力】" in profile
        assert "【Skill能力】" in profile

    def test_parse_structured_response(self, service):
        """测试结构化响应解析（正常/边界）"""
        # 正常场景
        data = {
            "semantic_profile": "这是一个测试画像",
            "capability_tags": ["实时计算", "特征工程"],
        }
        result = service._parse_structured_response(data)
        assert result.semantic_profile == "这是一个测试画像"
        assert result.capability_tags == ["实时计算", "特征工程"]

        # 缺少字段
        result = service._parse_structured_response({})
        assert result.semantic_profile is None
        assert result.capability_tags == []

        # 超长截断
        long_profile = "这是一个很长的描述。" * 200
        result = service._parse_structured_response({"semantic_profile": long_profile})
        assert len(result.semantic_profile) <= 503
        assert result.semantic_profile.endswith("...")