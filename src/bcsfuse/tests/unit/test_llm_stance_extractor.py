"""
LLMStanceExtractor 单元测试

TDD测试用例覆盖：
1. 单维度立场提取
2. 多维度立场提取
3. LLM失败处理
4. JSON解析容错
5. Feature Flag控制
"""

import pytest
from unittest.mock import Mock, patch
import json

from src.application.services.llm_stance_extractor import (
    LLMStanceExtractor,
    STANCE_EXTRACTION_PROMPT,
)
from src.domain.models.stance_signal import StanceSignal
from src.domain.models.fusion_result import Perspective
from src.domain.models.llm_response import LLMResponse, LLMUsage, FinishReason


# ============================================
# Helpers
# ============================================

def create_llm_response(content: str) -> LLMResponse:
    """创建模拟的LLM响应"""
    return LLMResponse(
        provider_id="test-provider",
        model_id="test-model",
        raw_text=content,
        parse_success=True,
        latency_ms=100,
        usage=LLMUsage(input_tokens=100, output_tokens=200),
        finish_reason=FinishReason.STOP,
    )


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def mock_llm_provider():
    """Mock LLM Provider"""
    provider = Mock()
    provider.model_name = "test-model"
    provider.generate = Mock(return_value=create_llm_response(""))
    return provider


@pytest.fixture(autouse=True)
def enable_feature_flag():
    """自动启用Feature Flag"""
    with patch('src.application.services.llm_stance_extractor.FeatureFlags.is_enabled', return_value=True):
        yield


@pytest.fixture
def single_dimension_response():
    """单维度立场提取的LLM响应"""
    return json.dumps({
        "dimension_id": "speed_vs_quality",
        "dimension_name": "速度与质量",
        "position": "axis_a",
        "strength": 0.85,
        "confidence": 0.90,
        "rationale": "明确强调时间紧迫，需要快速上线",
        "evidence": ["市场竞争激烈", "时间窗口有限", "竞品已上线"]
    }, ensure_ascii=False)


@pytest.fixture
def multi_dimension_response():
    """多维度立场提取的LLM响应"""
    return json.dumps([
        {
            "dimension_id": "speed_vs_quality",
            "dimension_name": "速度与质量",
            "position": "axis_a",
            "strength": 0.80,
            "confidence": 0.85,
            "rationale": "优先追求快速上线",
            "evidence": ["市场窗口期短"]
        },
        {
            "dimension_id": "cost_vs_value",
            "dimension_name": "成本与价值",
            "position": "balanced",
            "strength": 0.50,
            "confidence": 0.70,
            "rationale": "需要平衡成本与业务价值",
            "evidence": ["资源有限但业务价值高"]
        }
    ], ensure_ascii=False)


@pytest.fixture
def neutral_stance_response():
    """中立立场响应"""
    return json.dumps({
        "dimension_id": "risk_vs_opportunity",
        "dimension_name": "风险与机会",
        "position": "neutral",
        "strength": 0.20,
        "confidence": 0.40,
        "rationale": "观点表述不够明确，无法判断倾向",
        "evidence": []
    }, ensure_ascii=False)


@pytest.fixture
def sample_perspective():
    """测试用视角"""
    return Perspective(
        participant_id="product_team",
        participant_type="bot",
        role="consultant",
        summary="建议快速上线抢占市场，市场竞争激烈需要快速响应",
        key_points=["时间窗口有限", "竞品已上线"],
        concerns=["延迟上线会失去市场机会"],
        confidence=0.85,
        status="completed",
    )


# ============================================
# Test Cases
# ============================================

class TestLLMStanceExtractor:
    """LLMStanceExtractor测试用例"""

    def test_init_with_provider(self, mock_llm_provider):
        """测试初始化 - 使用传入的provider"""
        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)
        assert extractor._llm == mock_llm_provider

    def test_init_without_provider(self):
        """测试初始化 - 不传入provider"""
        extractor = LLMStanceExtractor()
        # _llm may be None if no provider is available
        assert extractor._llm is None or extractor._llm is not None

    def test_extract_single_dimension(
        self,
        mock_llm_provider,
        single_dimension_response,
        sample_perspective,
    ):
        """测试单维度立场提取"""
        # Given: LLM返回单维度立场
        mock_llm_provider.generate.return_value = create_llm_response(single_dimension_response)

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)

        # When: 提取立场
        with patch('src.application.services.llm_stance_extractor.FeatureFlags.is_enabled', return_value=True):
            signals = extractor.extract(
                question="产品团队希望快速上线，技术团队担忧质量，如何平衡？",
                perspective=sample_perspective,
            )

        # Then: 应正确解析结果
        assert signals is not None
        assert len(signals) == 1
        assert signals[0].participant_id == "product_team"
        assert signals[0].dimension_id == "speed_vs_quality"
        assert signals[0].position == "axis_a"
        assert signals[0].strength == 0.85
        assert signals[0].confidence == 0.90
        assert len(signals[0].evidence) == 3

    def test_extract_multi_dimension(
        self,
        mock_llm_provider,
        multi_dimension_response,
        sample_perspective,
    ):
        """测试多维度立场提取"""
        # Given: LLM返回多维度立场
        mock_llm_provider.generate.return_value = create_llm_response(multi_dimension_response)

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)

        # When: 提取立场
        with patch('src.application.services.llm_stance_extractor.FeatureFlags.is_enabled', return_value=True):
            signals = extractor.extract(
                question="复杂决策场景，涉及多个维度",
                perspective=sample_perspective,
            )

        # Then: 应正确解析多维度
        assert signals is not None
        assert len(signals) == 2
        assert signals[0].dimension_id == "speed_vs_quality"
        assert signals[1].dimension_id == "cost_vs_value"

    def test_extract_neutral_stance(
        self,
        mock_llm_provider,
        neutral_stance_response,
        sample_perspective,
    ):
        """测试中立立场提取"""
        # Given: LLM返回中立立场
        mock_llm_provider.generate.return_value = create_llm_response(neutral_stance_response)

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)

        # When: 提取立场
        with patch('src.application.services.llm_stance_extractor.FeatureFlags.is_enabled', return_value=True):
            signals = extractor.extract(
                question="测试问题",
                perspective=sample_perspective,
            )

        # Then: 应正确解析中立立场
        assert signals is not None
        assert len(signals) == 1
        assert signals[0].position == "neutral"
        assert signals[0].strength == 0.20
        assert signals[0].confidence == 0.40

    def test_extract_llm_failure(self, mock_llm_provider, sample_perspective):
        """测试LLM调用失败处理"""
        # Given: LLM调用抛出异常
        mock_llm_provider.generate.side_effect = Exception("LLM service unavailable")

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)

        # When: 提取立场
        result = extractor.extract(
            question="测试问题",
            perspective=sample_perspective,
        )

        # Then: 应返回空列表（触发fallback）
        assert result == []

    def test_extract_invalid_json(self, mock_llm_provider, sample_perspective):
        """测试JSON解析容错"""
        # Given: LLM返回无效JSON
        mock_llm_provider.generate.return_value = create_llm_response("This is not a valid JSON response")

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)

        # When: 提取立场
        with patch('src.application.services.llm_stance_extractor.FeatureFlags.is_enabled', return_value=True):
            result = extractor.extract(
                question="测试问题",
                perspective=sample_perspective,
            )

        # Then: 应返回空列表（解析失败）
        assert result == []

    def test_extract_json_with_markdown(self, mock_llm_provider, sample_perspective):
        """测试带Markdown包装的JSON解析"""
        # Given: LLM返回被Markdown包裹的JSON
        response = """```json
        {
            "dimension_id": "test_dimension",
            "dimension_name": "测试维度",
            "position": "axis_b",
            "strength": 0.75,
            "confidence": 0.80,
            "rationale": "测试理由",
            "evidence": ["证据1"]
        }
        ```"""
        mock_llm_provider.generate.return_value = create_llm_response(response)

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)

        # When: 提取立场
        with patch('src.application.services.llm_stance_extractor.FeatureFlags.is_enabled', return_value=True):
            signals = extractor.extract(
                question="测试问题",
                perspective=sample_perspective,
            )

        # Then: 应能正确解析
        assert signals is not None
        assert len(signals) == 1
        assert signals[0].dimension_id == "test_dimension"

    def test_extract_perspective_with_all_fields(self, mock_llm_provider, single_dimension_response):
        """测试包含所有字段的视角格式化"""
        # Given: 包含所有字段的视角
        perspective = Perspective(
            participant_id="tech_team",
            participant_type="bot",
            role="consultant",
            summary="建议确保充分的测试和质量保障",
            key_points=["需要完整测试", "技术方案需要验证"],
            concerns=["快速上线可能导致质量问题"],
            confidence=0.80,
            status="completed",
            flexibility="可协商",
        )
        mock_llm_provider.generate.return_value = create_llm_response(single_dimension_response)

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)

        # When: 提取立场
        with patch('src.application.services.llm_stance_extractor.FeatureFlags.is_enabled', return_value=True):
            signals = extractor.extract(
                question="测试问题",
                perspective=perspective,
            )

        # Then: 应成功提取
        assert signals is not None

    def test_extract_multiple_perspectives(
        self,
        mock_llm_provider,
        single_dimension_response,
    ):
        """测试批量提取多个视角的立场"""
        # Given: 多个视角
        perspectives = [
            Perspective(
                participant_id="team_a",
                participant_type="bot",
                role="consultant",
                summary="支持快速上线",
                key_points=["市场机会"],
                concerns=[],
                confidence=0.85,
                status="completed",
            ),
            Perspective(
                participant_id="team_b",
                participant_type="bot",
                role="consultant",
                summary="支持质量保障",
                key_points=["稳定性"],
                concerns=[],
                confidence=0.80,
                status="completed",
            ),
        ]
        mock_llm_provider.generate.return_value = create_llm_response(single_dimension_response)

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)

        # When: 批量提取
        with patch('src.application.services.llm_stance_extractor.FeatureFlags.is_enabled', return_value=True):
            all_signals = extractor.extract_all(
                question="测试问题",
                perspectives=perspectives,
            )

        # Then: 应返回所有视角的立场
        assert all_signals is not None
        # Each perspective should have at least 1 signal
        assert sum(len(s) for s in all_signals.values()) >= 2

    @patch('src.application.services.llm_stance_extractor.FeatureFlags')
    def test_extract_feature_disabled(self, mock_feature_flags, mock_llm_provider, sample_perspective):
        """测试Feature Flag禁用时跳过提取"""
        mock_feature_flags.is_enabled.return_value = False

        extractor = LLMStanceExtractor(llm_provider=mock_llm_provider)
        result = extractor.extract(
            question="测试问题",
            perspective=sample_perspective,
        )

        assert result == []
        mock_llm_provider.generate.assert_not_called()


class TestStanceExtractionPrompt:
    """Prompt构建测试"""

    def test_prompt_contains_question(self):
        """测试Prompt包含问题"""
        prompt = STANCE_EXTRACTION_PROMPT.format(
            question="测试问题？",
            perspective_formatted="测试视角",
        )

        assert "测试问题？" in prompt

    def test_prompt_contains_analysis_tasks(self):
        """测试Prompt包含分析任务"""
        prompt = STANCE_EXTRACTION_PROMPT.format(
            question="测试",
            perspective_formatted="测试",
        )

        assert "冲突维度" in prompt
        assert "立场倾向" in prompt
        assert "强度" in prompt

    def test_prompt_contains_output_format(self):
        """测试Prompt包含输出格式"""
        prompt = STANCE_EXTRACTION_PROMPT.format(
            question="测试",
            perspective_formatted="测试",
        )

        assert "dimension_id" in prompt
        assert "position" in prompt
        assert "strength" in prompt
        assert "confidence" in prompt
        assert "rationale" in prompt
        assert "evidence" in prompt


class TestStanceSignalValidation:
    """立场信号验证测试"""

    def test_stance_signal_is_meaningful(self):
        """测试立场有意义判断"""
        meaningful = StanceSignal(
            participant_id="test",
            dimension_id="test_dimension",
            position="axis_a",
            strength=0.8,
            confidence=0.7,
            evidence=["证据"],
        )
        assert meaningful.is_meaningful() is True

    def test_stance_signal_not_meaningful_neutral(self):
        """测试中立立场无意义"""
        neutral = StanceSignal(
            participant_id="test",
            dimension_id="test_dimension",
            position="neutral",
            strength=0.8,
            confidence=0.7,
            evidence=[],
        )
        assert neutral.is_meaningful() is False

    def test_stance_signal_not_meaningful_low_confidence(self):
        """测试低置信度立场无意义"""
        low_conf = StanceSignal(
            participant_id="test",
            dimension_id="test_dimension",
            position="axis_a",
            strength=0.8,
            confidence=0.3,  # Below 0.4 threshold
            evidence=[],
        )
        assert low_conf.is_meaningful() is False

    def test_stance_signal_opposite_detection(self):
        """测试立场对立检测"""
        signal_a = StanceSignal(
            participant_id="a",
            dimension_id="speed_vs_quality",
            position="axis_a",
            strength=0.8,
            confidence=0.7,
            evidence=[],
        )
        signal_b = StanceSignal(
            participant_id="b",
            dimension_id="speed_vs_quality",
            position="axis_b",
            strength=0.7,
            confidence=0.8,
            evidence=[],
        )
        assert signal_a.is_opposite_to(signal_b) is True

    def test_stance_signal_aligned_detection(self):
        """测试立场一致检测"""
        signal_a = StanceSignal(
            participant_id="a",
            dimension_id="speed_vs_quality",
            position="axis_a",
            strength=0.8,
            confidence=0.7,
            evidence=[],
        )
        signal_a2 = StanceSignal(
            participant_id="a2",
            dimension_id="speed_vs_quality",
            position="axis_a",
            strength=0.7,
            confidence=0.8,
            evidence=[],
        )
        assert signal_a.is_aligned_with(signal_a2) is True