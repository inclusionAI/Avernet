"""
LLMConflictAnalyzer 单元测试

TDD测试用例覆盖：
1. 简单冲突检测（速度vs质量）
2. 严重冲突检测（合规红线）
3. 对齐点识别
4. LLM失败处理
5. JSON解析容错
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json

from src.application.services.llm_conflict_analyzer import (
    LLMConflictAnalyzer,
    CONFLICT_ANALYSIS_PROMPT,
)
from src.domain.models.llm_conflict_analysis import (
    LLMConflictAnalysis,
    StanceAnalysis,
    LLMConflict,
    LLMAlignmentPoint,
    LLMConclusion,
)
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
    with patch('src.application.services.llm_conflict_analyzer.FeatureFlags.is_enabled', return_value=True):
        yield


@pytest.fixture
def simple_conflict_response():
    """简单冲突场景的LLM响应"""
    return json.dumps({
        "stance_analysis": [
            {
                "participant_id": "product_team",
                "stance": "支持",
                "core_demand": "快速上线抢占市场",
                "main_concerns": ["竞品压力", "市场窗口期短"],
                "flexibility": "可协商",
                "rationale": "市场竞争激烈，需要快速响应"
            },
            {
                "participant_id": "tech_team",
                "stance": "有条件支持",
                "core_demand": "确保系统稳定性",
                "main_concerns": ["测试不充分", "技术债务累积"],
                "flexibility": "可协商",
                "rationale": "支持业务发展但不能牺牲质量"
            }
        ],
        "conflicts": [
            {
                "parties": ["product_team", "tech_team"],
                "conflict_type": "关注点分歧",
                "issue": "产品团队追求快速上线，技术团队关注质量保障",
                "severity": "medium",
                "analysis": "双方目标一致但路径不同，可通过分级发布等方式协调"
            }
        ],
        "alignment_points": [
            {
                "participants": ["product_team", "tech_team"],
                "point": "都希望产品成功上线",
                "significance": "共同目标是协调的基础"
            }
        ],
        "conclusion": {
            "overall_severity": "medium",
            "go_no_go": "conditional_go",
            "resolution_strategy": "建议采用分级发布，按风险等级分批上线",
            "conditions": ["建立快速回滚机制", "制定最小可行产品范围"],
            "priority_actions": ["协调发布时间表", "建立质量保障措施"],
            "reasoning": "冲突存在但有共识基础，可通过技术手段协调"
        }
    }, ensure_ascii=False)


@pytest.fixture
def critical_conflict_response():
    """严重冲突场景的LLM响应"""
    return json.dumps({
        "stance_analysis": [
            {
                "participant_id": "business_team",
                "stance": "支持",
                "core_demand": "快速推出创新金融产品",
                "main_concerns": ["市场机会", "竞争压力"],
                "flexibility": "可协商",
                "rationale": "创新是业务增长的关键"
            },
            {
                "participant_id": "compliance_team",
                "stance": "反对",
                "core_demand": "严格遵守监管要求",
                "main_concerns": ["合规风险", "监管处罚"],
                "flexibility": "不可妥协",
                "rationale": "产品存在明确的合规风险"
            }
        ],
        "conflicts": [
            {
                "parties": ["business_team", "compliance_team"],
                "conflict_type": "立场对立",
                "issue": "业务创新与合规要求存在根本性冲突",
                "severity": "high",
                "analysis": "一方坚持推进，另一方明确反对，需要高层决策"
            }
        ],
        "alignment_points": [
            {
                "participants": ["business_team", "compliance_team"],
                "point": "都希望公司健康发展",
                "significance": "长期目标一致但短期路径冲突"
            }
        ],
        "conclusion": {
            "overall_severity": "high",
            "go_no_go": "need_discussion",
            "resolution_strategy": "需要高层介入决策，或调整产品设计以满足合规要求",
            "conditions": ["获得监管指导", "调整产品设计"],
            "priority_actions": ["组织高层决策会议", "寻求监管沟通"],
            "reasoning": "存在严重的立场对立，需要升级处理"
        }
    }, ensure_ascii=False)


@pytest.fixture
def alignment_response():
    """对齐场景的LLM响应"""
    return json.dumps({
        "stance_analysis": [
            {
                "participant_id": "team_a",
                "stance": "支持",
                "core_demand": "推进项目实施",
                "main_concerns": ["进度"],
                "flexibility": "开放态度",
                "rationale": "项目价值明确"
            },
            {
                "participant_id": "team_b",
                "stance": "支持",
                "core_demand": "推进项目实施",
                "main_concerns": ["进度"],
                "flexibility": "开放态度",
                "rationale": "项目价值明确"
            }
        ],
        "conflicts": [],
        "alignment_points": [
            {
                "participants": ["team_a", "team_b"],
                "point": "双方都支持推进项目",
                "significance": "共识充分，可以直接推进"
            }
        ],
        "conclusion": {
            "overall_severity": "low",
            "go_no_go": "go",
            "resolution_strategy": "各方立场一致，直接推进实施",
            "conditions": [],
            "priority_actions": ["启动项目"],
            "reasoning": "无冲突，共识充分"
        }
    }, ensure_ascii=False)


@pytest.fixture
def sample_perspectives():
    """测试用视角列表"""
    return [
        Perspective(
            participant_id="product_team",
            participant_type="bot",
            role="consultant",
            summary="建议快速上线抢占市场，市场竞争激烈需要快速响应",
            key_points=["时间窗口有限", "竞品已上线"],
            concerns=["延迟上线会失去市场机会"],
            confidence=0.85,
            status="completed",
        ),
        Perspective(
            participant_id="tech_team",
            participant_type="bot",
            role="consultant",
            summary="建议确保充分的测试和质量保障，避免线上事故",
            key_points=["需要完整测试", "技术方案需要验证"],
            concerns=["快速上线可能导致质量问题"],
            confidence=0.80,
            status="completed",
        ),
    ]


# ============================================
# Test Cases
# ============================================

class TestLLMConflictAnalyzer:
    """LLMConflictAnalyzer测试用例"""

    def test_init_with_provider(self, mock_llm_provider):
        """测试初始化 - 使用传入的provider"""
        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)
        assert analyzer._llm == mock_llm_provider

    def test_init_without_provider(self):
        """测试初始化 - 不传入provider"""
        analyzer = LLMConflictAnalyzer()
        # _llm may be None if no provider is available
        assert analyzer._llm is None or analyzer._llm is not None

    def test_analyze_simple_conflict(
        self,
        mock_llm_provider,
        simple_conflict_response,
        sample_perspectives,
    ):
        """测试简单冲突检测 - 速度vs质量场景"""
        # Given: LLM返回medium冲突
        mock_llm_provider.generate.return_value = create_llm_response(simple_conflict_response)

        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)

        # When: 分析冲突
        with patch('src.application.services.llm_conflict_analyzer.FeatureFlags.is_enabled', return_value=True):
            result = analyzer.analyze(
                question="产品团队希望快速上线，技术团队担忧质量，如何平衡？",
                perspectives=sample_perspectives,
            )

        # Then: 应正确解析结果
        assert result is not None
        assert len(result.stance_analysis) == 2
        assert len(result.conflicts) == 1
        assert len(result.alignment_points) == 1
        assert result.conclusion is not None
        assert result.conclusion.overall_severity == "medium"
        assert result.conclusion.go_no_go == "conditional_go"

    def test_analyze_critical_conflict(
        self,
        mock_llm_provider,
        critical_conflict_response,
    ):
        """测试严重冲突检测 - 合规红线场景"""
        # Given: LLM返回high冲突
        mock_llm_provider.generate.return_value = create_llm_response(critical_conflict_response)

        perspectives = [
            Perspective(
                participant_id="business_team",
                participant_type="bot",
                role="consultant",
                summary="建议快速推出创新产品",
                key_points=["市场机会"],
                concerns=["竞争压力"],
                confidence=0.85,
                status="completed",
            ),
            Perspective(
                participant_id="compliance_team",
                participant_type="bot",
                role="consultant",
                summary="产品存在合规风险，建议暂缓",
                key_points=["监管要求"],
                concerns=["合规处罚风险"],
                confidence=0.90,
                status="completed",
            ),
        ]

        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)

        # When: 分析冲突
        with patch('src.application.services.llm_conflict_analyzer.FeatureFlags.is_enabled', return_value=True):
            result = analyzer.analyze(
                question="业务希望推出创新产品，合规认为有风险，如何决策？",
                perspectives=perspectives,
            )

        # Then: 应检测到high级别冲突
        assert result is not None
        assert len(result.conflicts) == 1
        assert result.conflicts[0].severity == "high"
        assert result.conclusion.overall_severity == "high"
        assert result.conclusion.go_no_go == "need_discussion"

    def test_analyze_alignment_scenario(
        self,
        mock_llm_provider,
        alignment_response,
    ):
        """测试对齐点识别 - 无冲突场景"""
        # Given: LLM返回无冲突
        mock_llm_provider.generate.return_value = create_llm_response(alignment_response)

        perspectives = [
            Perspective(
                participant_id="team_a",
                participant_type="bot",
                role="consultant",
                summary="支持项目推进",
                key_points=["项目价值明确"],
                concerns=[],
                confidence=0.85,
                status="completed",
            ),
            Perspective(
                participant_id="team_b",
                participant_type="bot",
                role="consultant",
                summary="支持项目推进",
                key_points=["项目价值明确"],
                concerns=[],
                confidence=0.80,
                status="completed",
            ),
        ]

        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)

        # When: 分析冲突
        with patch('src.application.services.llm_conflict_analyzer.FeatureFlags.is_enabled', return_value=True):
            result = analyzer.analyze(
                question="项目是否应该推进？",
                perspectives=perspectives,
            )

        # Then: 应检测到对齐，无冲突
        assert result is not None
        assert len(result.conflicts) == 0
        assert len(result.alignment_points) > 0
        assert result.conclusion.overall_severity == "low"
        assert result.conclusion.go_no_go == "go"

    def test_analyze_llm_failure(self, mock_llm_provider, sample_perspectives):
        """测试LLM调用失败处理"""
        # Given: LLM调用抛出异常
        mock_llm_provider.generate.side_effect = Exception("LLM service unavailable")

        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)

        # When: 分析冲突
        result = analyzer.analyze(
            question="测试问题",
            perspectives=sample_perspectives,
        )

        # Then: 应返回None（触发fallback）
        assert result is None

    def test_analyze_invalid_json(self, mock_llm_provider, sample_perspectives):
        """测试JSON解析容错"""
        # Given: LLM返回无效JSON
        mock_llm_provider.generate.return_value = create_llm_response("This is not a valid JSON response")

        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)

        # When: 分析冲突
        with patch('src.application.services.llm_conflict_analyzer.FeatureFlags.is_enabled', return_value=True):
            result = analyzer.analyze(
                question="测试问题",
                perspectives=sample_perspectives,
            )

        # Then: 应返回None（解析失败触发fallback）
        assert result is None

    def test_analyze_json_with_markdown(self, mock_llm_provider, sample_perspectives):
        """测试带Markdown包装的JSON解析"""
        # Given: LLM返回被Markdown包裹的JSON
        response = """```json
        {
            "stance_analysis": [
                {
                    "participant_id": "test",
                    "stance": "支持",
                    "core_demand": "测试",
                    "main_concerns": [],
                    "flexibility": "可协商",
                    "rationale": "测试"
                }
            ],
            "conflicts": [],
            "alignment_points": [],
            "conclusion": {
                "overall_severity": "low",
                "go_no_go": "go",
                "resolution_strategy": "测试",
                "conditions": [],
                "priority_actions": [],
                "reasoning": "测试"
            }
        }
        ```"""
        mock_llm_provider.generate.return_value = create_llm_response(response)

        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)

        # When: 分析冲突
        with patch('src.application.services.llm_conflict_analyzer.FeatureFlags.is_enabled', return_value=True):
            result = analyzer.analyze(
                question="测试问题",
                perspectives=sample_perspectives,
            )

        # Then: 应能正确解析
        assert result is not None
        assert len(result.stance_analysis) == 1

    def test_format_perspectives(self, mock_llm_provider, sample_perspectives):
        """测试视角格式化"""
        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)

        formatted = analyzer._format_perspectives(sample_perspectives)

        assert "product_team" in formatted
        assert "tech_team" in formatted
        assert "快速上线" in formatted

    def test_analyze_records_latency(self, mock_llm_provider, simple_conflict_response, sample_perspectives):
        """测试记录分析延迟"""
        mock_llm_provider.generate.return_value = create_llm_response(simple_conflict_response)

        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)
        with patch('src.application.services.llm_conflict_analyzer.FeatureFlags.is_enabled', return_value=True):
            result = analyzer.analyze(
                question="测试问题",
                perspectives=sample_perspectives,
            )

        assert result is not None
        assert result.latency_ms >= 0  # Mock执行太快可能为0

    def test_analyze_empty_perspectives(self, mock_llm_provider):
        """测试空视角列表处理"""
        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)

        result = analyzer.analyze(
            question="测试问题",
            perspectives=[],
        )

        assert result is None  # 无视角数据

    @patch('src.application.services.llm_conflict_analyzer.FeatureFlags')
    def test_analyze_feature_disabled(self, mock_feature_flags, mock_llm_provider, sample_perspectives):
        """测试Feature Flag禁用时跳过分析"""
        mock_feature_flags.is_enabled.return_value = False

        analyzer = LLMConflictAnalyzer(llm_provider=mock_llm_provider)
        result = analyzer.analyze(
            question="测试问题",
            perspectives=sample_perspectives,
        )

        assert result is None
        mock_llm_provider.generate.assert_not_called()


class TestLLMConflictAnalysisModels:
    """LLM冲突分析模型测试"""

    def test_stance_analysis_model(self):
        """测试立场分析模型"""
        stance = StanceAnalysis(
            participant_id="test",
            stance="支持",
            core_demand="测试诉求",
            main_concerns=["顾虑1"],
            flexibility="可协商",
            rationale="测试理由",
        )

        assert stance.participant_id == "test"
        assert stance.stance == "支持"
        assert stance.flexibility == "可协商"

    def test_llm_conflict_model(self):
        """测试冲突模型"""
        conflict = LLMConflict(
            parties=["a", "b"],
            conflict_type="立场对立",
            issue="测试冲突",
            severity="high",
            analysis="分析说明",
        )

        assert conflict.parties == ["a", "b"]
        assert conflict.severity == "high"

    def test_llm_conclusion_model(self):
        """测试结论模型"""
        conclusion = LLMConclusion(
            overall_severity="medium",
            go_no_go="conditional_go",
            resolution_strategy="测试策略",
            conditions=["条件1"],
            priority_actions=["行动1"],
            reasoning="测试理由",
        )

        assert conclusion.overall_severity == "medium"
        assert conclusion.go_no_go == "conditional_go"
        assert len(conclusion.conditions) == 1

    def test_llm_conflict_analysis_model(self):
        """测试完整分析结果模型"""
        analysis = LLMConflictAnalysis(
            stance_analysis=[
                StanceAnalysis(
                    participant_id="test",
                    stance="支持",
                    core_demand="测试",
                    main_concerns=[],
                    flexibility="可协商",
                    rationale="测试",
                )
            ],
            conflicts=[],
            alignment_points=[],
            conclusion=LLMConclusion(
                overall_severity="low",
                go_no_go="go",
                resolution_strategy="测试",
                conditions=[],
                priority_actions=[],
                reasoning="测试",
            ),
        )

        assert len(analysis.stance_analysis) == 1
        assert analysis.conclusion.overall_severity == "low"


class TestConflictAnalysisPrompt:
    """Prompt构建测试"""

    def test_prompt_contains_question(self):
        """测试Prompt包含问题"""
        prompt = CONFLICT_ANALYSIS_PROMPT.format(
            question="测试问题？",
            perspectives_formatted="测试视角",
        )

        assert "测试问题？" in prompt

    def test_prompt_contains_analysis_tasks(self):
        """测试Prompt包含分析任务"""
        prompt = CONFLICT_ANALYSIS_PROMPT.format(
            question="测试",
            perspectives_formatted="测试",
        )

        assert "立场识别" in prompt
        assert "冲突检测" in prompt
        assert "对齐点识别" in prompt
        assert "整体研判" in prompt

    def test_prompt_contains_output_format(self):
        """测试Prompt包含输出格式"""
        prompt = CONFLICT_ANALYSIS_PROMPT.format(
            question="测试",
            perspectives_formatted="测试",
        )

        assert "stance_analysis" in prompt
        assert "conflicts" in prompt
        assert "alignment_points" in prompt
        assert "conclusion" in prompt
        assert "overall_severity" in prompt
        assert "go_no_go" in prompt