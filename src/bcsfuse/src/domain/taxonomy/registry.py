"""
Taxonomy Registry

分类体系注册表。

负责加载和查询分类配置，支持自动 fallback 到 legacy 默认值。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from src.domain.taxonomy.models import (
    ConflictDimensionAxis,
    ConflictDimensionDefinition,
    ConflictDimensionsConfig,
    DomainDefinition,
    DomainsConfig,
    RiskLevelKeywords,
    RiskSignalDefinition,
    RiskSignalsConfig,
    ScenarioDefinition,
    ScenariosConfig,
    TaxonomyConfig,
)

logger = logging.getLogger(__name__)

# 配置文件路径
DEFAULT_TAXONOMY_DIR = Path(__file__).parent.parent.parent.parent / "configs" / "taxonomy"


class TaxonomyRegistry:
    """
    分类体系注册表

    加载和管理分类配置，支持自动 fallback。

    使用方式：
        registry = TaxonomyRegistry()
        keywords = registry.get_critical_keywords()
        domain = registry.find_domain_by_keyword("安全")
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """
        初始化注册表

        Args:
            config_dir: 配置目录路径，默认使用 configs/taxonomy
        """
        self._config_dir = config_dir or DEFAULT_TAXONOMY_DIR
        self._config: Optional[TaxonomyConfig] = None
        self._load_or_fallback()

    def _load_or_fallback(self) -> None:
        """
        加载配置或 fallback 到 legacy 默认值

        如果配置文件缺失、格式错误或字段不合法，
        自动 fallback 到 legacy 硬编码关键词。
        """
        try:
            self._config = self._load_from_yaml()

            # 检查是否加载了有效配置（至少有风险信号）
            has_risk_signals = bool(
                self._config.risk_signals.critical_scenarios
                or self._config.risk_signals.high_scenarios
                or self._config.risk_signals.medium_scenarios
            )

            if not has_risk_signals:
                # 没有加载到任何风险信号，使用 legacy
                logger.warning(
                    "[TaxonomyRegistry] 未加载到风险信号配置，fallback 到 legacy"
                )
                self._config = self._get_legacy_defaults()
            else:
                logger.info(
                    "[TaxonomyRegistry] 配置加载成功: %d 领域, %d 场景, %d 风险信号",
                    len(self._config.domains.technical_domains)
                    + len(self._config.domains.business_domains),
                    len(self._config.scenarios.business_scenarios),
                    len(self._config.risk_signals.critical_scenarios)
                    + len(self._config.risk_signals.high_scenarios)
                    + len(self._config.risk_signals.medium_scenarios),
                )
        except (FileNotFoundError, yaml.YAMLError, Exception) as e:
            logger.warning(
                "[TaxonomyRegistry] 配置加载失败，fallback 到 legacy: %s",
                str(e),
            )
            self._config = self._get_legacy_defaults()

    def _load_from_yaml(self) -> TaxonomyConfig:
        """
        从 YAML 文件加载配置

        Returns:
            TaxonomyConfig: 加载的配置

        Raises:
            FileNotFoundError: 配置文件不存在
            yaml.YAMLError: YAML 格式错误
        """
        domains_path = self._config_dir / "domains.yaml"
        scenarios_path = self._config_dir / "scenarios.yaml"
        risk_signals_path = self._config_dir / "risk_signals.yaml"

        # 加载领域配置
        domains_config = DomainsConfig()
        if domains_path.exists():
            with open(domains_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data:
                    # 解析技术领域
                    if "technical_domains" in data:
                        domains_config.technical_domains = {
                            k: DomainDefinition(**v)
                            for k, v in data["technical_domains"].items()
                        }
                    # 解析业务领域
                    if "business_domains" in data:
                        domains_config.business_domains = {
                            k: DomainDefinition(**v)
                            for k, v in data["business_domains"].items()
                        }

        # 加载场景配置
        scenarios_config = ScenariosConfig()
        if scenarios_path.exists():
            with open(scenarios_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data:
                    # 解析业务场景
                    if "business_scenarios" in data:
                        scenarios_config.business_scenarios = {
                            k: ScenarioDefinition(**v)
                            for k, v in data["business_scenarios"].items()
                        }
                    # 解析风险权重
                    if "risk_weights" in data:
                        scenarios_config.risk_weights = data["risk_weights"]
                    # 解析场景优先级
                    if "scenario_priorities" in data:
                        scenarios_config.scenario_priorities = data["scenario_priorities"]

        # 加载风险信号配置
        risk_signals_config = RiskSignalsConfig()
        if risk_signals_path.exists():
            with open(risk_signals_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data:
                    # 解析严重场景
                    if "critical_scenarios" in data:
                        risk_signals_config.critical_scenarios = {
                            k: RiskSignalDefinition(**v)
                            for k, v in data["critical_scenarios"].items()
                        }
                    # 解析高风险场景
                    if "high_scenarios" in data:
                        risk_signals_config.high_scenarios = {
                            k: RiskSignalDefinition(**v)
                            for k, v in data["high_scenarios"].items()
                        }
                    # 解析中等风险场景
                    if "medium_scenarios" in data:
                        risk_signals_config.medium_scenarios = {
                            k: RiskSignalDefinition(**v)
                            for k, v in data["medium_scenarios"].items()
                        }
                    # 解析风险等级关键词
                    if "risk_level_keywords" in data:
                        risk_signals_config.risk_level_keywords = RiskLevelKeywords(
                            critical=data["risk_level_keywords"].get("critical", []),
                            high=data["risk_level_keywords"].get("high", []),
                            medium=data["risk_level_keywords"].get("medium", []),
                        )

        # 加载冲突维度配置
        conflict_dimensions_config = ConflictDimensionsConfig()
        conflict_dimensions_path = self._config_dir / "conflict_dimensions.yaml"
        if conflict_dimensions_path.exists():
            try:
                with open(conflict_dimensions_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if data:
                        # 解析冲突维度
                        if "dimensions" in data:
                            conflict_dimensions_config.dimensions = {
                                k: ConflictDimensionDefinition(
                                    name=v.get("name", k),
                                    description=v.get("description", ""),
                                    axis_a=ConflictDimensionAxis(**v.get("axis_a", {})),
                                    axis_b=ConflictDimensionAxis(**v.get("axis_b", {})),
                                )
                                for k, v in data["dimensions"].items()
                            }
                        # 解析阈值
                        if "thresholds" in data:
                            conflict_dimensions_config.thresholds = data["thresholds"]
                        logger.info(
                            "[TaxonomyRegistry] 加载了 %d 个冲突维度",
                            len(conflict_dimensions_config.dimensions),
                        )
            except (yaml.YAMLError, Exception) as e:
                logger.warning(
                    "[TaxonomyRegistry] conflict_dimensions 加载失败，fallback 到空配置: %s",
                    str(e),
                )
                # 保持空配置作为 fallback

        return TaxonomyConfig(
            domains=domains_config,
            scenarios=scenarios_config,
            risk_signals=risk_signals_config,
            conflict_dimensions=conflict_dimensions_config,
        )

    def _get_legacy_defaults(self) -> TaxonomyConfig:
        """
        获取 legacy 默认配置

        返回硬编码的默认关键词配置，作为 fallback。

        Returns:
            TaxonomyConfig: legacy 默认配置
        """
        # 从 expert_diagnosis_service.py 迁移的硬编码关键词
        critical_keywords = [
            "数据泄露",
            "信息泄露",
            "隐私泄露",
            "用户数据泄露",
            "用户泄露",
            "信息外泄",
            "数据被盗",
            "数据外泄",
            "数据暴露",
            "敏感数据泄露",
            "用户信息泄露",
            "安全事件",
            "安全事故",
            "安全漏洞",
            "被攻击",
            "系统被入侵",
            "数据被盗取",
            "遭受攻击",
            "监管函",
            "整改通知",
            "处罚",
            "立案调查",
            "资金损失",
            "资金风险",
            "资金安全",
        ]

        high_keywords = [
            "核心系统",
            "核心交易",
            "支付系统",
            "资金流转",
            "架构升级",
            "系统迁移",
            "技术升级",
            "数据库迁移",
            "java升级",
            "版本升级",
            "数据库切换",
            "技术栈升级",
            "跨境支付",
            "牌照申请",
            "监管准入",
            "合规准入",
            "跨境业务",
            "境外支付",
            "外汇支付",
            "国际支付",
            "金融牌照",
            "支付牌照",
            "准入评估",
            "反洗钱",
            "整改",
            "合规风险",
            "100万",
            "百万用户",
            "千万用户",
            "大规模",
            "组织架构调整",
            "人员优化",
            "大规模裁员",
            "股权融资",
            "并购",
            "投融资",
            "大促活动",
            "营销活动风险",
            "活动风险评审",
        ]

        medium_keywords = [
            "新业务",
            "新产品",
            "业务拓展",
            "大促",
            "双11",
            "618",
            "活动",
            "性能优化",
            "功能开发",
            "系统改造",
        ]

        risk_level_critical = [
            "严重",
            "critical",
            "高危",
            "紧急",
            "必须立即",
            "需要立即",
            "禁止上线",
        ]

        risk_level_high = [
            "注入",
            "漏洞",
            "缺失",
            "泄露",
            "攻击",
            "违规",
            "高",
            "high",
            "不通过",
            "不可行",
            "反对",
            "风险",
            "隐患",
            "威胁",
            "安全隐患",
        ]

        risk_level_medium = [
            "中等",
            "medium",
            "需要关注",
            "有条件",
            "待确认",
            "建议",
            "关注",
        ]

        # 构建 legacy 配置
        critical_scenarios = {
            "data_leakage": RiskSignalDefinition(
                name="数据泄露事件",
                description="用户数据或敏感信息泄露事件",
                keywords=critical_keywords[:11],  # 数据泄露相关
                weight=1.0,
            ),
            "security_incident": RiskSignalDefinition(
                name="安全事件",
                description="系统安全漏洞或遭受攻击事件",
                keywords=critical_keywords[11:18],  # 安全事件相关
                weight=1.0,
            ),
            "regulatory_penalty": RiskSignalDefinition(
                name="监管处罚",
                description="监管机构下达的处罚或整改要求",
                keywords=critical_keywords[18:22],  # 监管处罚相关
                weight=1.0,
            ),
            "fund_safety": RiskSignalDefinition(
                name="资金安全",
                description="涉及资金损失或资金安全风险",
                keywords=critical_keywords[22:],  # 资金安全相关
                weight=1.0,
            ),
        }

        high_scenarios = {
            "core_system": RiskSignalDefinition(
                name="核心系统",
                description="核心交易、支付、资金流转等关键系统",
                keywords=high_keywords[:4],
                weight=0.95,
            ),
            "system_migration": RiskSignalDefinition(
                name="系统变更",
                description="架构升级、技术栈迁移等重大变更",
                keywords=high_keywords[4:12],
                weight=0.9,
            ),
            "regulatory_access": RiskSignalDefinition(
                name="监管准入",
                description="跨境支付、牌照申请、合规准入场景",
                keywords=high_keywords[12:23],
                weight=0.95,
            ),
            "compliance_remediation": RiskSignalDefinition(
                name="合规整改",
                description="反洗钱、合规风险整改场景",
                keywords=high_keywords[23:26],
                weight=0.9,
            ),
            "large_scale_impact": RiskSignalDefinition(
                name="大规模影响",
                description="影响大量用户的场景",
                keywords=high_keywords[26:30],
                weight=0.85,
            ),
            "organization_change": RiskSignalDefinition(
                name="组织变革",
                description="组织架构调整、人员优化场景",
                keywords=high_keywords[30:33],
                weight=0.9,
            ),
            "investment_finance": RiskSignalDefinition(
                name="投融资交易",
                description="股权融资、并购等投融资场景",
                keywords=high_keywords[33:36],
                weight=0.9,
            ),
            "large_promotion": RiskSignalDefinition(
                name="大型活动",
                description="大促活动风险评审场景",
                keywords=high_keywords[36:],
                weight=0.85,
            ),
        }

        medium_scenarios = {
            "new_business": RiskSignalDefinition(
                name="新业务",
                description="新业务、新产品拓展场景",
                keywords=medium_keywords[:3],
                weight=0.6,
            ),
            "promotion_activity": RiskSignalDefinition(
                name="大促活动",
                description="常规大促、营销活动场景",
                keywords=medium_keywords[3:7],
                weight=0.55,
            ),
            "tech_improvement": RiskSignalDefinition(
                name="技术改造",
                description="性能优化、功能开发等技术改造场景",
                keywords=medium_keywords[7:],
                weight=0.5,
            ),
        }

        risk_level_keywords = RiskLevelKeywords(
            critical=risk_level_critical,
            high=risk_level_high,
            medium=risk_level_medium,
        )

        return TaxonomyConfig(
            domains=DomainsConfig(),
            scenarios=ScenariosConfig(),
            risk_signals=RiskSignalsConfig(
                critical_scenarios=critical_scenarios,
                high_scenarios=high_scenarios,
                medium_scenarios=medium_scenarios,
                risk_level_keywords=risk_level_keywords,
            ),
            conflict_dimensions=ConflictDimensionsConfig(),
        )

    # =====================================
    # 查询接口
    # =====================================

    def get_critical_keywords(self) -> list[str]:
        """
        获取所有严重风险关键词

        Returns:
            list[str]: 关键词列表
        """
        keywords = []
        for scenario in self._config.risk_signals.critical_scenarios.values():
            keywords.extend(scenario.keywords)
        return keywords

    def get_high_keywords(self) -> list[str]:
        """
        获取所有高风险关键词

        Returns:
            list[str]: 关键词列表
        """
        keywords = []
        for scenario in self._config.risk_signals.high_scenarios.values():
            keywords.extend(scenario.keywords)
        return keywords

    def get_medium_keywords(self) -> list[str]:
        """
        获取所有中等风险关键词

        Returns:
            list[str]: 关键词列表
        """
        keywords = []
        for scenario in self._config.risk_signals.medium_scenarios.values():
            keywords.extend(scenario.keywords)
        return keywords

    def get_risk_level_keywords(self) -> RiskLevelKeywords:
        """
        获取风险等级关键词配置

        Returns:
            RiskLevelKeywords: 风险等级关键词
        """
        return self._config.risk_signals.risk_level_keywords

    def find_domain_by_keyword(self, keyword: str) -> Optional[DomainDefinition]:
        """
        根据关键词查找领域

        Args:
            keyword: 搜索关键词

        Returns:
            Optional[DomainDefinition]: 匹配的领域定义，未找到返回 None
        """
        keyword_lower = keyword.lower()

        # 搜索技术领域
        for domain in self._config.domains.technical_domains.values():
            if any(kw.lower() == keyword_lower for kw in domain.keywords):
                return domain

        # 搜索业务领域
        for domain in self._config.domains.business_domains.values():
            if any(kw.lower() == keyword_lower for kw in domain.keywords):
                return domain

        return None

    def find_scenario_by_keyword(self, keyword: str) -> Optional[ScenarioDefinition]:
        """
        根据关键词查找场景

        Args:
            keyword: 搜索关键词

        Returns:
            Optional[ScenarioDefinition]: 匹配的场景定义，未找到返回 None
        """
        keyword_lower = keyword.lower()

        for scenario in self._config.scenarios.business_scenarios.values():
            if any(kw.lower() == keyword_lower for kw in scenario.keywords):
                return scenario

        return None

    def find_risk_signal_by_keyword(
        self, keyword: str
    ) -> Optional[tuple[str, RiskSignalDefinition]]:
        """
        根据关键词查找风险信号

        Args:
            keyword: 搜索关键词

        Returns:
            Optional[tuple[str, RiskSignalDefinition]]: (风险等级, 信号定义)，未找到返回 None
        """
        keyword_lower = keyword.lower()

        # 检查严重场景
        for signal_id, signal in self._config.risk_signals.critical_scenarios.items():
            if any(kw.lower() == keyword_lower for kw in signal.keywords):
                return ("critical", signal)

        # 检查高风险场景
        for signal_id, signal in self._config.risk_signals.high_scenarios.items():
            if any(kw.lower() == keyword_lower for kw in signal.keywords):
                return ("high", signal)

        # 检查中等风险场景
        for signal_id, signal in self._config.risk_signals.medium_scenarios.items():
            if any(kw.lower() == keyword_lower for kw in signal.keywords):
                return ("medium", signal)

        return None

    def match_text_for_risk(self, text: str) -> tuple[Optional[str], float]:
        """
        匹配文本中的风险信号

        Args:
            text: 待匹配文本

        Returns:
            tuple[Optional[str], float]: (风险等级, 置信度)
        """
        text_lower = text.lower()

        # 检查严重关键词
        critical_keywords = self.get_critical_keywords()
        critical_matches = sum(
            1 for kw in critical_keywords if kw.lower() in text_lower
        )
        if critical_matches > 0:
            confidence = min(1.0, critical_matches * 0.3 + 0.5)
            return ("critical", confidence)

        # 检查高风险关键词
        high_keywords = self.get_high_keywords()
        high_matches = sum(1 for kw in high_keywords if kw.lower() in text_lower)
        if high_matches > 0:
            confidence = min(1.0, high_matches * 0.2 + 0.4)
            return ("high", confidence)

        # 检查中等风险关键词
        medium_keywords = self.get_medium_keywords()
        medium_matches = sum(
            1 for kw in medium_keywords if kw.lower() in text_lower
        )
        if medium_matches > 0:
            confidence = min(0.8, medium_matches * 0.15 + 0.3)
            return ("medium", confidence)

        return (None, 0.0)

    def get_config(self) -> TaxonomyConfig:
        """
        获取完整配置

        Returns:
            TaxonomyConfig: 当前配置
        """
        return self._config

    def is_loaded_from_yaml(self) -> bool:
        """
        检查是否从 YAML 加载成功

        Returns:
            bool: 是否从 YAML 加载
        """
        # 检查是否有 YAML 特有的数据
        return bool(
            self._config.domains.technical_domains
            or self._config.domains.business_domains
            or self._config.scenarios.business_scenarios
        )

    # =====================================
    # G2 Conflict Dimensions 查询接口
    # =====================================

    def get_conflict_dimensions(self) -> dict[str, ConflictDimensionDefinition]:
        """
        获取所有冲突维度定义

        Returns:
            dict[str, ConflictDimensionDefinition]: 冲突维度字典
        """
        return self._config.conflict_dimensions.dimensions

    def get_conflict_dimension(self, dimension_id: str) -> Optional[ConflictDimensionDefinition]:
        """
        获取指定的冲突维度定义

        Args:
            dimension_id: 维度 ID

        Returns:
            Optional[ConflictDimensionDefinition]: 维度定义，未找到返回 None
        """
        return self._config.conflict_dimensions.dimensions.get(dimension_id)

    def get_conflict_dimension_thresholds(self) -> dict[str, float]:
        """
        获取冲突判定阈值

        Returns:
            dict[str, float]: 阈值配置
        """
        return self._config.conflict_dimensions.thresholds

    def detect_stance_for_dimension(
        self,
        text: str,
        dimension_id: str,
    ) -> tuple[Optional[str], float, list[str]]:
        """
        检测文本在指定维度上的立场

        Args:
            text: 待检测文本
            dimension_id: 维度 ID

        Returns:
            tuple[Optional[str], float, list[str]]:
                (轴向标签: axis_a/axis_b/balanced/neutral/unknown,
                 强度: 0.0-1.0,
                 匹配的关键词列表)
        """
        dimension = self.get_conflict_dimension(dimension_id)
        if not dimension:
            return ("unknown", 0.0, [])

        text_lower = text.lower()

        # 检查 axis_a 关键词匹配
        axis_a_keywords = dimension.axis_a.keywords
        axis_a_matches = [kw for kw in axis_a_keywords if kw.lower() in text_lower]
        axis_a_strength = min(1.0, len(axis_a_matches) * 0.2) if axis_a_matches else 0.0

        # 检查 axis_b 关键词匹配
        axis_b_keywords = dimension.axis_b.keywords
        axis_b_matches = [kw for kw in axis_b_keywords if kw.lower() in text_lower]
        axis_b_strength = min(1.0, len(axis_b_matches) * 0.2) if axis_b_matches else 0.0

        # 判定立场
        thresholds = self.get_conflict_dimension_thresholds()
        min_threshold = thresholds.get("min_confidence_threshold", 0.4)

        if axis_a_strength < min_threshold and axis_b_strength < min_threshold:
            # 两边都没有足够匹配
            return ("neutral", 0.0, [])
        elif abs(axis_a_strength - axis_b_strength) < 0.2 and axis_a_strength >= min_threshold:
            # 两边强度接近
            return ("balanced", (axis_a_strength + axis_b_strength) / 2, axis_a_matches + axis_b_matches)
        elif axis_a_strength > axis_b_strength:
            return ("axis_a", axis_a_strength, axis_a_matches)
        else:
            return ("axis_b", axis_b_strength, axis_b_matches)


# 全局单例
_registry: Optional[TaxonomyRegistry] = None


def get_taxonomy_registry() -> TaxonomyRegistry:
    """
    获取全局 TaxonomyRegistry 单例

    Returns:
        TaxonomyRegistry: 注册表实例
    """
    global _registry
    if _registry is None:
        _registry = TaxonomyRegistry()
    return _registry


def reset_taxonomy_registry() -> None:
    """
    重置全局注册表（用于测试）
    """
    global _registry
    _registry = None


__all__ = [
    "TaxonomyRegistry",
    "get_taxonomy_registry",
    "reset_taxonomy_registry",
]