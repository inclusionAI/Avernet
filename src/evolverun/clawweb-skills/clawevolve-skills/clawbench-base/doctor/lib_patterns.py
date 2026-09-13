"""
Pattern Detection Module for PinchBench Doctor.

Contains detectors for various problematic patterns in agent session transcripts.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import hashlib


class IssueSeverity(Enum):
    """Severity levels for detected issues."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Issue:
    """Represents a detected issue in the transcript."""
    issue_id: str
    pattern_id: str
    pattern_name: str
    severity: IssueSeverity
    task_id: str
    description: str
    tool_name: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    suggestion: str = ""
    raw_events: List[Dict] = field(default_factory=list)


class PatternDetector:
    """Main pattern detector that runs all registered pattern detectors."""

    def __init__(self):
        self.detectors = [
            RepeatedToolCallFailureDetector(),
            ParameterContextLossDetector(),
            MissingRequiredToolDetector(),
        ]

    def detect(
        self,
        events: List[Any],
        task_id: str,
        tool_calls: List[Dict],
    ) -> List[Issue]:
        """Run all pattern detectors on the transcript events."""
        issues = []

        for detector in self.detectors:
            try:
                detected = detector.detect(events, task_id, tool_calls)
                issues.extend(detected)
            except Exception as e:
                # Log but don't fail on detector errors
                import logging
                logging.warning(
                    "Pattern detector %s failed: %s",
                    detector.__class__.__name__,
                    e
                )

        return issues


class PatternDetectorBase(ABC):
    """Base class for pattern detectors."""

    @property
    @abstractmethod
    def pattern_id(self) -> str:
        """Unique identifier for this pattern."""
        pass

    @property
    @abstractmethod
    def pattern_name(self) -> str:
        """Human-readable name for this pattern."""
        pass

    @property
    @abstractmethod
    def severity(self) -> IssueSeverity:
        """Default severity level for this pattern."""
        pass

    @abstractmethod
    def detect(
        self,
        events: List[Any],
        task_id: str,
        tool_calls: List[Dict],
    ) -> List[Issue]:
        """Detect instances of this pattern in the transcript."""
        pass

    def _generate_issue_id(self, task_id: str, *args) -> str:
        """Generate a unique issue ID."""
        content = f"{task_id}-{self.pattern_id}-{'-'.join(str(a) for a in args)}"
        return hashlib.md5(content.encode()).hexdigest()[:8]


class RepeatedToolCallFailureDetector(PatternDetectorBase):
    """
    P001: Detect repeated tool call failures.

    Pattern: The same tool is called multiple times with similar parameters,
    and at least one call fails. This indicates the agent is struggling
    to get the tool call right.
    """

    pattern_id = "P001"
    pattern_name = "repeated_tool_call_failure"
    severity = IssueSeverity.HIGH

    # Minimum number of failed calls to trigger this pattern
    MIN_FAILURES = 2
    # Maximum time gap (in seconds) to consider calls as related
    MAX_TIME_GAP = 120

    def detect(
        self,
        events: List[Any],
        task_id: str,
        tool_calls: List[Dict],
    ) -> List[Issue]:
        """Detect repeated tool call failures."""
        issues = []

        # Group tool calls by tool name
        calls_by_tool: Dict[str, List[Dict]] = {}
        for tc in tool_calls:
            tool_name = tc.get("tool_name", "unknown")
            if tool_name not in calls_by_tool:
                calls_by_tool[tool_name] = []
            calls_by_tool[tool_name].append(tc)

        # Analyze each tool's calls
        for tool_name, calls in calls_by_tool.items():
            if len(calls) < 2:
                continue

            # Separate failed and successful calls
            failed_calls = [tc for tc in calls if tc.get("is_error")]
            successful_calls = [tc for tc in calls if not tc.get("is_error")]

            # Skip if no failures
            if not failed_calls:
                continue

            # Check for repeated failures with similar parameters
            repeated_failures = self._find_repeated_failures(failed_calls)

            for failure_group in repeated_failures:
                if len(failure_group) >= self.MIN_FAILURES:
                    issue = self._create_issue(
                        task_id=task_id,
                        tool_name=tool_name,
                        failed_calls=failure_group,
                        successful_calls=successful_calls,
                    )
                    issues.append(issue)

        return issues

    def _find_repeated_failures(self, failed_calls: List[Dict]) -> List[List[Dict]]:
        """Find groups of repeated failures with similar parameters."""
        if not failed_calls:
            return []

        groups = []
        current_group = [failed_calls[0]]

        for i in range(1, len(failed_calls)):
            current = failed_calls[i]
            previous = current_group[-1]

            # Check if calls are related (similar parameters)
            if self._are_calls_related(previous, current):
                current_group.append(current)
            else:
                if len(current_group) >= self.MIN_FAILURES:
                    groups.append(current_group)
                current_group = [current]

        if len(current_group) >= self.MIN_FAILURES:
            groups.append(current_group)

        return groups

    def _are_calls_related(self, call1: Dict, call2: Dict) -> bool:
        """Check if two calls are related (similar parameters)."""
        args1 = call1.get("arguments", {})
        args2 = call2.get("arguments", {})

        # Same tool, check parameter similarity
        # Consider related if they have the same parameter keys
        keys1 = set(args1.keys())
        keys2 = set(args2.keys())

        if keys1 != keys2:
            return False

        # Check if at least some parameters are the same
        same_params = sum(
            1 for k in keys1
            if args1.get(k) == args2.get(k)
        )

        return same_params >= len(keys1) * 0.5

    def _create_issue(
        self,
        task_id: str,
        tool_name: str,
        failed_calls: List[Dict],
        successful_calls: List[Dict],
    ) -> Issue:
        """Create an issue for repeated failures."""
        # Extract error messages from failed calls
        error_messages = []
        for tc in failed_calls:
            result = tc.get("result", "")
            if result:
                # Truncate long error messages
                error_messages.append(result[:200] if len(result) > 200 else result)

        # Analyze what changed in successful calls
        success_hint = ""
        if successful_calls:
            success_args = successful_calls[0].get("arguments", {})
            failed_args = failed_calls[0].get("arguments", {}) if failed_calls else {}

            # Find parameters that differ
            diff_params = []
            for key in success_args:
                if success_args.get(key) != failed_args.get(key):
                    diff_params.append(key)

            if diff_params:
                success_hint = f"成功调用中修正的参数: {', '.join(diff_params)}"

        # Determine root cause
        root_cause = self._analyze_root_cause(tool_name, failed_calls, successful_calls)

        description = (
            f"工具 '{tool_name}' 连续调用失败 {len(failed_calls)} 次。"
            f"错误信息: {error_messages[0] if error_messages else '未知错误'}"
        )

        suggestion = self._generate_suggestion(tool_name, root_cause, failed_calls)

        return Issue(
            issue_id=self._generate_issue_id(task_id, tool_name, len(failed_calls)),
            pattern_id=self.pattern_id,
            pattern_name=self.pattern_name,
            severity=self.severity,
            task_id=task_id,
            description=description,
            tool_name=tool_name,
            evidence={
                "failure_count": len(failed_calls),
                "success_count": len(successful_calls),
                "error_messages": error_messages[:3],  # Keep top 3
                "failed_parameters": [
                    tc.get("arguments") for tc in failed_calls[:3]
                ],
                "successful_parameters": (
                    successful_calls[0].get("arguments") if successful_calls else None
                ),
                "root_cause": root_cause,
            },
            suggestion=suggestion,
            raw_events=failed_calls[:5],  # Keep some raw events for debugging
        )

    def _analyze_root_cause(
        self,
        tool_name: str,
        failed_calls: List[Dict],
        successful_calls: List[Dict],
    ) -> str:
        """Analyze the root cause of the failures."""
        if not failed_calls:
            return "未知"

        first_failed_args = failed_calls[0].get("arguments", {})

        # Check for null/missing parameters
        null_params = [k for k, v in first_failed_args.items() if v is None]
        if null_params:
            return f"参数缺失或为空: {', '.join(null_params)}"

        # Check for empty string parameters
        empty_params = [k for k, v in first_failed_args.items() if v == ""]
        if empty_params:
            return f"参数值为空字符串: {', '.join(empty_params)}"

        # Check for wrong type (simplified)
        wrong_type_hints = []
        for key, value in first_failed_args.items():
            if isinstance(value, str):
                # Check if it looks like it should be a number
                if value.isdigit():
                    wrong_type_hints.append(f"{key} 可能需要数字类型")
        if wrong_type_hints:
            return f"参数类型可能有误: {'; '.join(wrong_type_hints)}"

        # Compare with successful call
        if successful_calls:
            success_args = successful_calls[0].get("arguments", {})
            for key in first_failed_args:
                if first_failed_args.get(key) != success_args.get(key):
                    return f"参数 '{key}' 值不正确: '{first_failed_args.get(key)}' 应为 '{success_args.get(key)}'"

        return "参数值不符合预期"

    def _generate_suggestion(
        self,
        tool_name: str,
        root_cause: str,
        failed_calls: List[Dict],
    ) -> str:
        """Generate a suggestion for fixing this issue."""
        suggestions = []

        # Base suggestion based on root cause
        if "参数缺失" in root_cause or "为空" in root_cause:
            suggestions.append(
                f"在 '{tool_name}' 的 skill 描述中:"
            )
            suggestions.append(
                "  1. 明确说明所有必填参数及其来源"
            )
            suggestions.append(
                "  2. 添加参数示例，特别是从其他工具返回值获取参数的情况"
            )

        elif "参数类型" in root_cause:
            suggestions.append(
                f"在 '{tool_name}' 的 skill 描述中添加参数类型说明"
            )

        elif "值不正确" in root_cause:
            suggestions.append(
                f"在 '{tool_name}' 的 skill 描述中:"
            )
            suggestions.append(
                "  1. 提供正确的参数值示例"
            )
            suggestions.append(
                "  2. 说明参数取值的约束条件"
            )

        else:
            suggestions.append(
                f"建议优化 '{tool_name}' 工具描述，提供更清晰的参数说明和示例"
            )

        # Add context about previous tool if available
        suggestions.append(
            "\n建议: 在工具返回结果的说明中，用明确标注字段名，方便后续工具引用。"
        )

        return "\n".join(suggestions)


class ParameterContextLossDetector(PatternDetectorBase):
    """
    P002: Detect parameter context loss.

    Pattern: A tool is called with missing parameters (null/empty), but
    the value should have been available from a previous tool's result.
    """

    pattern_id = "P002"
    pattern_name = "parameter_context_loss"
    severity = IssueSeverity.HIGH

    # Common field mappings between tools
    # Format: (source_tool, source_field) -> (target_tool, target_param)
    KNOWN_MAPPINGS = [
        # YuQue MCP
        ("skylark_search", "docs[].id", "skylark_doc_detail", "doc_id"),
        ("skylark_search", "docs[].book_id", "skylark_doc_create", "book_id"),
        ("skylark_search", "docs[].slug", "skylark_doc_update", "slug"),
        ("skylark_user_info", "user_id", "skylark_book_create", "user_id"),
        ("skylark_user_groups", "groups[].id", "skylark_user_book_list", "group_id"),
        # Dataphin MCP
        ("retrieve_candidate_assets", "assetCandidates[].guid", "retrieve_meta_info", "tableGuid"),
        ("retrieve_candidate_assets", "assetCandidates[].guid", "retrieve_tables_knowledge", "tableGuids"),
        # Generic patterns
        (None, "id", None, "id"),
        (None, "doc_id", None, "doc_id"),
        (None, "book_id", None, "book_id"),
    ]

    def detect(
        self,
        events: List[Any],
        task_id: str,
        tool_calls: List[Dict],
    ) -> List[Issue]:
        """Detect parameter context loss."""
        issues = []

        # Build a map of available values from tool results
        available_values: Dict[str, Any] = {}  # field_name -> list of values
        tool_results: Dict[str, Dict] = {}  # tool_call_id -> result

        for tc in tool_calls:
            tool_name = tc.get("tool_name", "")
            args = tc.get("arguments", {})
            result = tc.get("result", "")
            is_error = tc.get("is_error", False)

            # Store result
            if result and not is_error:
                tool_results[tc.get("tool_call_id", "")] = {
                    "tool_name": tool_name,
                    "result": result,
                }

                # Extract values from result
                extracted = self._extract_values_from_result(tool_name, result)
                for field, values in extracted.items():
                    key = f"{tool_name}.{field}"
                    if key not in available_values:
                        available_values[key] = []
                    available_values[key].extend(values)

            # Check if this call has missing parameters that might be available
            if not is_error:  # Only check non-error calls for context loss
                missing_params = self._find_missing_parameters(tool_name, args)

                for param in missing_params:
                    # Check if value is available from previous tools
                    source = self._find_value_source(param, tool_name, available_values)

                    if source:
                        issue = Issue(
                            issue_id=self._generate_issue_id(task_id, tool_name, param),
                            pattern_id=self.pattern_id,
                            pattern_name=self.pattern_name,
                            severity=self.severity,
                            task_id=task_id,
                            description=(
                                f"工具 '{tool_name}' 参数 '{param}' 为空，"
                                f"但该值可从之前 '{source['tool']}' 的结果中获取"
                            ),
                            tool_name=tool_name,
                            evidence={
                                "missing_parameter": param,
                                "source_tool": source["tool"],
                                "source_field": source["field"],
                                "available_values": source["values"][:3],  # Keep top 3
                            },
                            suggestion=(
                                f"建议在 '{source['tool']}' 的描述中明确说明返回结果包含 "
                                f"'{source['field']}' 字段，并提示后续工具可使用该值"
                            ),
                        )
                        issues.append(issue)

        return issues

    def _extract_values_from_result(
        self,
        tool_name: str,
        result: str,
    ) -> Dict[str, List[Any]]:
        """Extract field values from a tool result."""
        extracted: Dict[str, List[Any]] = {}

        try:
            # Try to parse as JSON
            data = json.loads(result)
            self._extract_values_recursive(data, extracted, prefix="")
        except json.JSONDecodeError:
            # Not JSON, try regex patterns
            pass

        return extracted

    def _extract_values_recursive(
        self,
        data: Any,
        extracted: Dict[str, List[Any]],
        prefix: str,
    ):
        """Recursively extract values from parsed JSON."""
        if isinstance(data, dict):
            for key, value in data.items():
                field_name = f"{prefix}.{key}" if prefix else key
                if isinstance(value, (str, int, float, bool)):
                    if field_name not in extracted:
                        extracted[field_name] = []
                    extracted[field_name].append(value)
                elif isinstance(value, dict):
                    self._extract_values_recursive(value, extracted, field_name)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, (str, int, float, bool)):
                            if field_name not in extracted:
                                extracted[field_name] = []
                            extracted[field_name].append(item)
                        elif isinstance(item, dict):
                            self._extract_values_recursive(item, extracted, field_name)

    def _find_missing_parameters(
        self,
        tool_name: str,
        args: Dict,
    ) -> List[str]:
        """Find parameters that are null or empty."""
        missing = []
        for key, value in args.items():
            if value is None or value == "":
                missing.append(key)
        return missing

    def _find_value_source(
        self,
        param: str,
        target_tool: str,
        available_values: Dict[str, List[Any]],
    ) -> Optional[Dict]:
        """Find if a value is available from previous tool results."""
        # Check known mappings first
        for mapping in self.KNOWN_MAPPINGS:
            source_tool, source_field, map_target_tool, map_target_param = mapping

            if map_target_param == param or param in map_target_param:
                if target_tool == map_target_tool or map_target_tool is None:
                    # Look for matching source
                    for key, values in available_values.items():
                        key_tool, key_field = key.split(".", 1) if "." in key else (key, "")
                        if source_tool and source_tool == key_tool:
                            if source_field in key_field or key_field in source_field:
                                return {
                                    "tool": source_tool,
                                    "field": source_field,
                                    "values": values,
                                }

        # Generic fallback: look for matching field names
        for key, values in available_values.items():
            if "." in key:
                source_tool, source_field = key.split(".", 1)
            else:
                source_tool = "unknown"
                source_field = key

            # Check if field name matches parameter
            if param in source_field or source_field in param:
                if values:  # Has actual values
                    return {
                        "tool": source_tool,
                        "field": source_field,
                        "values": values,
                    }

        return None


class MissingRequiredToolDetector(PatternDetectorBase):
    """
    P004: Detect missing required tool calls.

    Pattern: The task requires certain tools to be called, but some
    were not called. This is inferred from task definition or grading criteria.
    """

    pattern_id = "P004"
    pattern_name = "missing_required_tool"
    severity = IssueSeverity.MEDIUM

    # Common tool sequences for different task types
    TOOL_SEQUENCES = {
        "search_doc_detail": ["skylark_search", "skylark_doc_detail"],
        "search_doc_create": ["skylark_search", "skylark_doc_create"],
        "search_doc_update": ["skylark_search", "skylark_doc_detail", "skylark_doc_update"],
        "workflow_doc": ["skylark_search", "skylark_doc_detail", "skylark_doc_create", "skylark_book_toc"],
        "asset_workflow": ["retrieve_candidate_assets", "retrieve_meta_info", "retrieve_tables_knowledge"],
    }

    def detect(
        self,
        events: List[Any],
        task_id: str,
        tool_calls: List[Dict],
    ) -> List[Issue]:
        """Detect missing required tool calls."""
        issues = []

        # Get list of tools that were called
        called_tools = set()
        for tc in tool_calls:
            tool_name = tc.get("tool_name", "")
            if tool_name:
                # Normalize tool name (remove MCP prefix)
                normalized = self._normalize_tool_name(tool_name)
                called_tools.add(normalized)

        # Infer expected tools from task_id
        expected_tools = self._infer_expected_tools(task_id)

        # Find missing tools
        missing_tools = []
        for expected_seq in expected_tools:
            if expected_seq and not any(t in called_tools for t in expected_seq):
                missing_tools.append(expected_seq[0])  # Use first tool as representative

        if missing_tools:
            issue = Issue(
                issue_id=self._generate_issue_id(task_id, "missing"),
                pattern_id=self.pattern_id,
                pattern_name=self.pattern_name,
                severity=self.severity,
                task_id=task_id,
                description=(
                    f"任务可能缺少必要的工具调用: {', '.join(missing_tools)}"
                ),
                evidence={
                    "called_tools": list(called_tools),
                    "expected_tools": [seq[0] for seq in expected_tools if seq],
                    "missing_tools": missing_tools,
                },
                suggestion=(
                    f"建议检查任务定义，确保 Agent 了解需要调用: {', '.join(missing_tools)}"
                ),
            )
            issues.append(issue)

        return issues

    def _normalize_tool_name(self, tool_name: str) -> str:
        """Normalize tool name by removing MCP prefix."""
        # Remove common prefixes
        prefixes = ["mcp__", "skylarkmcpserver__", "YuQuemcp-server_v2__", "skylark_"]
        normalized = tool_name
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break
        return normalized

    def _infer_expected_tools(self, task_id: str) -> List[List[str]]:
        """Infer expected tools from task_id."""
        expected = []

        # Pattern matching on task_id
        task_lower = task_id.lower()

        if "search" in task_lower and "detail" in task_lower:
            expected.append(self.TOOL_SEQUENCES.get("search_doc_detail", []))
        if "search" in task_lower and "create" in task_lower:
            expected.append(self.TOOL_SEQUENCES.get("search_doc_create", []))
        if "search" in task_lower and "update" in task_lower:
            expected.append(self.TOOL_SEQUENCES.get("search_doc_update", []))
        if "workflow" in task_lower:
            expected.append(self.TOOL_SEQUENCES.get("workflow_doc", []))
        if "complex" in task_lower:
            expected.append(self.TOOL_SEQUENCES.get("workflow_doc", []))

        return [seq for seq in expected if seq]