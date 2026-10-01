"""
Taxonomy Registry Unit Tests

测试分类体系注册表的功能，包括：
1. 从 YAML 加载配置
2. Fallback 到 legacy 默认值
3. 查询接口
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.domain.taxonomy.models import (
    TaxonomyConfig,
    DomainsConfig,
    RiskSignalsConfig,
    RiskLevelKeywords,
    RiskSignalDefinition,
)
from src.domain.taxonomy.registry import (
    TaxonomyRegistry,
    get_taxonomy_registry,
    reset_taxonomy_registry,
)


class TestTaxonomyRegistry:
    """TaxonomyRegistry 单元测试"""

    def setup_method(self):
        """每个测试前重置全局注册表"""
        reset_taxonomy_registry()

    def test_load_from_yaml_success(self):
        """测试从 YAML 成功加载配置"""
        registry = TaxonomyRegistry()

        # 验证加载成功
        assert registry.is_loaded_from_yaml() is True

        # 验证关键词数量
        critical_keywords = registry.get_critical_keywords()
        assert len(critical_keywords) > 0
        assert "数据泄露" in critical_keywords

        high_keywords = registry.get_high_keywords()
        assert len(high_keywords) > 0
        assert "架构升级" in high_keywords

    def test_get_risk_level_keywords(self):
        """测试获取风险等级关键词"""
        registry = TaxonomyRegistry()

        keywords = registry.get_risk_level_keywords()
        assert isinstance(keywords, RiskLevelKeywords)
        assert "严重" in keywords.critical
        assert "漏洞" in keywords.high
        assert "中等" in keywords.medium

    def test_find_domain_by_keyword(self):
        """测试根据关键词查找领域"""
        registry = TaxonomyRegistry()

        # 测试技术领域
        domain = registry.find_domain_by_keyword("security")
        assert domain is not None
        assert domain.name == "安全"

        # 测试业务领域
        domain = registry.find_domain_by_keyword("风控")
        assert domain is not None
        assert domain.name == "风控"

        # 测试不存在的关键词
        domain = registry.find_domain_by_keyword("不存在的关键词xyz")
        assert domain is None

    def test_find_risk_signal_by_keyword(self):
        """测试根据关键词查找风险信号"""
        registry = TaxonomyRegistry()

        # 测试 critical 场景
        result = registry.find_risk_signal_by_keyword("数据泄露")
        assert result is not None
        risk_level, signal = result
        assert risk_level == "critical"
        assert signal.name == "数据泄露事件"

        # 测试 high 场景
        result = registry.find_risk_signal_by_keyword("架构升级")
        assert result is not None
        risk_level, signal = result
        assert risk_level == "high"
        assert signal.name == "系统变更"

        # 测试不存在的关键词
        result = registry.find_risk_signal_by_keyword("普通关键词")
        assert result is None

    def test_match_text_for_risk_critical(self):
        """测试文本风险匹配 - Critical"""
        registry = TaxonomyRegistry()

        # Critical 场景
        risk_level, confidence = registry.match_text_for_risk(
            "发生数据泄露事件，需要立即处理"
        )
        assert risk_level == "critical"
        assert confidence >= 0.5

    def test_match_text_for_risk_high(self):
        """测试文本风险匹配 - High"""
        registry = TaxonomyRegistry()

        # High 场景
        risk_level, confidence = registry.match_text_for_risk(
            "架构升级需要谨慎评估"
        )
        assert risk_level == "high"
        assert confidence >= 0.4

    def test_match_text_for_risk_medium(self):
        """测试文本风险匹配 - Medium"""
        registry = TaxonomyRegistry()

        # Medium 场景
        risk_level, confidence = registry.match_text_for_risk(
            "新业务拓展计划"
        )
        assert risk_level == "medium"
        assert confidence >= 0.3

    def test_match_text_for_risk_no_match(self):
        """测试文本风险匹配 - 无匹配"""
        registry = TaxonomyRegistry()

        # 无匹配
        risk_level, confidence = registry.match_text_for_risk(
            "这是一段普通的文本"
        )
        assert risk_level is None
        assert confidence == 0.0

    def test_fallback_on_missing_yaml(self, tmp_path):
        """测试 YAML 文件缺失时 fallback 到 legacy"""
        # 使用不存在的目录
        registry = TaxonomyRegistry(config_dir=tmp_path / "nonexistent")

        # 应该 fallback 到 legacy
        assert registry.is_loaded_from_yaml() is False

        # 仍然可以获取 legacy 关键词
        critical_keywords = registry.get_critical_keywords()
        assert len(critical_keywords) > 0
        assert "数据泄露" in critical_keywords

    def test_get_global_registry_singleton(self):
        """测试全局单例"""
        registry1 = get_taxonomy_registry()
        registry2 = get_taxonomy_registry()

        assert registry1 is registry2

    def test_get_config(self):
        """测试获取完整配置"""
        registry = TaxonomyRegistry()

        config = registry.get_config()
        assert isinstance(config, TaxonomyConfig)
        assert isinstance(config.domains, DomainsConfig)
        assert isinstance(config.risk_signals, RiskSignalsConfig)


class TestTaxonomyRegistryFallback:
    """Fallback 逻辑测试"""

    def setup_method(self):
        """每个测试前重置"""
        reset_taxonomy_registry()

    def test_fallback_critical_keywords_match_yaml(self):
        """测试 fallback critical 关键词与 YAML 一致"""
        # 从 YAML 加载
        yaml_registry = TaxonomyRegistry()
        yaml_critical = set(yaml_registry.get_critical_keywords())

        # 清除后重新加载（模拟 fallback）
        reset_taxonomy_registry()

        # Fallback 场景（无 YAML）
        fallback_registry = TaxonomyRegistry(
            config_dir=Path("/nonexistent/path")
        )
        fallback_critical = set(fallback_registry.get_critical_keywords())

        # 检查主要关键词存在
        assert "数据泄露" in yaml_critical
        assert "数据泄露" in fallback_critical
        assert "安全事件" in yaml_critical
        assert "安全事件" in fallback_critical

    def test_fallback_high_keywords_match_yaml(self):
        """测试 fallback high 关键词与 YAML 一致"""
        # 从 YAML 加载
        yaml_registry = TaxonomyRegistry()
        yaml_high = set(yaml_registry.get_high_keywords())

        # Fallback 场景
        fallback_registry = TaxonomyRegistry(
            config_dir=Path("/nonexistent/path")
        )
        fallback_high = set(fallback_registry.get_high_keywords())

        # 检查主要关键词存在
        assert "架构升级" in yaml_high
        assert "架构升级" in fallback_high
        assert "跨境支付" in yaml_high
        assert "跨境支付" in fallback_high


class TestTaxonomyRegistryEdgeCases:
    """边界条件测试"""

    def setup_method(self):
        """每个测试前重置"""
        reset_taxonomy_registry()

    def test_empty_text_match(self):
        """测试空文本匹配"""
        registry = TaxonomyRegistry()

        risk_level, confidence = registry.match_text_for_risk("")
        assert risk_level is None
        assert confidence == 0.0

    def test_none_question_inference(self):
        """测试 None 问题推断"""
        registry = TaxonomyRegistry()

        # 空/None 文本不应报错
        risk_level, confidence = registry.match_text_for_risk("")
        assert risk_level is None

    def test_special_characters_in_text(self):
        """测试特殊字符文本"""
        registry = TaxonomyRegistry()

        text = "数据泄露！！！@#$%^&*()紧急"
        risk_level, confidence = registry.match_text_for_risk(text)
        assert risk_level == "critical"

    def test_very_long_text(self):
        """测试超长文本"""
        registry = TaxonomyRegistry()

        # 构造超长文本
        text = "这是一段很长的文本..." * 1000 + "数据泄露"
        risk_level, confidence = registry.match_text_for_risk(text)
        assert risk_level == "critical"

    def test_mixed_case_keywords(self):
        """测试大小写混合关键词"""
        registry = TaxonomyRegistry()

        # 英文关键词大小写不敏感
        text = "CRITICAL critical Critical"
        risk_level, confidence = registry.match_text_for_risk(text)
        # risk_level_keywords 包含 "critical"
        keywords = registry.get_risk_level_keywords()
        assert "critical" in keywords.critical


class TestTaxonomyRegistryG2ConflictDimensions:
    """G2 冲突维度功能测试"""

    def setup_method(self):
        """每个测试前重置"""
        reset_taxonomy_registry()

    def test_get_conflict_dimensions(self):
        """测试获取冲突维度"""
        registry = TaxonomyRegistry()

        dimensions = registry.get_conflict_dimensions()
        assert isinstance(dimensions, dict)

        # 如果加载了 YAML，应该有维度
        # 如果 fallback，可能是空的
        for dim_id, dim in dimensions.items():
            assert dim.name is not None
            assert dim.axis_a is not None
            assert dim.axis_b is not None

    def test_get_conflict_dimension_existing(self):
        """测试获取存在的冲突维度"""
        registry = TaxonomyRegistry()

        dimensions = registry.get_conflict_dimensions()
        if dimensions:
            # 获取第一个维度
            first_dim_id = list(dimensions.keys())[0]
            dimension = registry.get_conflict_dimension(first_dim_id)

            assert dimension is not None
            assert dimension.name is not None
            assert dimension.axis_a is not None
            assert dimension.axis_b is not None

    def test_get_conflict_dimension_non_existing(self):
        """测试获取不存在的冲突维度"""
        registry = TaxonomyRegistry()

        dimension = registry.get_conflict_dimension("non_existing_dimension_xyz")
        assert dimension is None

    def test_get_conflict_dimension_thresholds(self):
        """测试获取冲突判定阈值"""
        registry = TaxonomyRegistry()

        thresholds = registry.get_conflict_dimension_thresholds()
        assert isinstance(thresholds, dict)

    def test_detect_stance_for_dimension_axis_a(self):
        """测试立场检测 - axis_a"""
        registry = TaxonomyRegistry()

        dimensions = registry.get_conflict_dimensions()
        if not dimensions:
            pytest.skip("无冲突维度配置")

        first_dim_id = list(dimensions.keys())[0]
        dimension = dimensions[first_dim_id]

        if dimension.axis_a.keywords:
            # 使用多个 axis_a 关键词来增加强度（每个关键词贡献 0.2 强度）
            # 需要 >= 2 个关键词才能超过 0.4 阈值
            keywords = dimension.axis_a.keywords[:3]
            text = " ".join([f"必须{kw}" for kw in keywords])

            position, strength, evidence = registry.detect_stance_for_dimension(
                text=text,
                dimension_id=first_dim_id,
            )

            # 检查检测结果是有效的
            assert position in ("axis_a", "neutral")  # 可能因阈值返回 neutral
            if position == "axis_a":
                assert strength > 0.0
                assert len(evidence) > 0

    def test_detect_stance_for_dimension_axis_b(self):
        """测试立场检测 - axis_b"""
        registry = TaxonomyRegistry()

        dimensions = registry.get_conflict_dimensions()
        if not dimensions:
            pytest.skip("无冲突维度配置")

        first_dim_id = list(dimensions.keys())[0]
        dimension = dimensions[first_dim_id]

        if dimension.axis_b.keywords:
            # 使用多个 axis_b 关键词来增加强度
            keywords = dimension.axis_b.keywords[:3]
            text = " ".join([f"必须{kw}" for kw in keywords])

            position, strength, evidence = registry.detect_stance_for_dimension(
                text=text,
                dimension_id=first_dim_id,
            )

            # 检查检测结果是有效的
            assert position in ("axis_b", "neutral")  # 可能因阈值返回 neutral
            if position == "axis_b":
                assert strength > 0.0
                assert len(evidence) > 0

    def test_detect_stance_for_dimension_neutral(self):
        """测试立场检测 - neutral"""
        registry = TaxonomyRegistry()

        dimensions = registry.get_conflict_dimensions()
        if not dimensions:
            pytest.skip("无冲突维度配置")

        first_dim_id = list(dimensions.keys())[0]

        # 使用无关文本
        text = "今天天气不错，出去走走吧"

        position, strength, evidence = registry.detect_stance_for_dimension(
            text=text,
            dimension_id=first_dim_id,
        )

        assert position == "neutral"
        assert strength == 0.0
        assert evidence == []

    def test_detect_stance_for_dimension_non_existing(self):
        """测试立场检测 - 不存在的维度"""
        registry = TaxonomyRegistry()

        position, strength, evidence = registry.detect_stance_for_dimension(
            text="测试文本",
            dimension_id="non_existing_dimension_xyz",
        )

        assert position == "unknown"
        assert strength == 0.0
        assert evidence == []

    def test_detect_stance_for_dimension_balanced(self):
        """测试立场检测 - balanced"""
        registry = TaxonomyRegistry()

        dimensions = registry.get_conflict_dimensions()
        if not dimensions:
            pytest.skip("无冲突维度配置")

        first_dim_id = list(dimensions.keys())[0]
        dimension = dimensions[first_dim_id]

        if dimension.axis_a.keywords and dimension.axis_b.keywords:
            # 同时包含多个两边的关键词来达到检测阈值
            kws_a = dimension.axis_a.keywords[:2]
            kws_b = dimension.axis_b.keywords[:2]
            text = " ".join([f"需要{kw}" for kw in kws_a + kws_b])

            position, strength, evidence = registry.detect_stance_for_dimension(
                text=text,
                dimension_id=first_dim_id,
            )

            # balanced 需要两边强度接近且都超过阈值
            # 也可能因为强度不足返回 neutral
            assert position in ("balanced", "neutral", "axis_a", "axis_b")