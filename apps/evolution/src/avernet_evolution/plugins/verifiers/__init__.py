"""部署层验证器实现（07 §3：执行器与评分器是插件，由组合根注入）。"""

from .rules import RULE_VALIDATION_MARKERS, build_rule_verifier, rule_cases

__all__ = ["RULE_VALIDATION_MARKERS", "build_rule_verifier", "rule_cases"]
