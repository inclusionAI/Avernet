"""
StructuredOutputParser

LLM Gateway / Provider Layer

结构化输出解析器，用于解析 LLM 返回的 JSON 结构。

Stage 4 Phase 4 增强版:
- 增强的 JSON 提取（嵌套大括号匹配）
- Fenced JSON 代码块提取（```json ... ```）
- 详尽的日志记录
- Parse failure reason tracking

V2 增强:
- 支持纯 JSON、fenced JSON、普通 fenced block
- 支持前后带说明文字时提取首个有效 JSON object
- 增强"首个平衡大括号 JSON 提取"兜底逻辑
- 错误日志分型：fenced_json_parse_failed, balanced_json_not_found,
  json_decode_error, schema_validation_failed
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from src.domain.models.fusion_recommendation import FusionRecommendation

logger = logging.getLogger(__name__)


class ParseErrorType(Enum):
    """解析错误类型"""
    EMPTY_RESPONSE = "empty_response"
    FENCED_JSON_PARSE_FAILED = "fenced_json_parse_failed"
    BALANCED_JSON_NOT_FOUND = "balanced_json_not_found"
    JSON_DECODE_ERROR = "json_decode_error"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"


@dataclass
class ParseResult:
    """
    解析结果

    Attributes:
        success: 是否成功
        data: 解析后的数据（成功时）
        error_message: 错误消息（失败时）
        error_type: 错误类型（失败时）
        raw_text: 原始文本
        warnings: 警告列表
        extraction_method: 提取方法（用于调试）
    """

    success: bool
    data: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    error_type: Optional[ParseErrorType] = None
    raw_text: str = ""
    warnings: list[str] = field(default_factory=list)
    extraction_method: Optional[str] = None


class StructuredOutputParser:
    """
    结构化输出解析器

    解析 LLM 返回的文本为结构化 JSON 对象。

    特性：
    - 严格 JSON 解析
    - 自动提取 JSON 代码块
    - 字段验证
    - 友好的错误信息
    """

    @staticmethod
    def parse(raw_text: str, schema_name: Optional[str] = None) -> ParseResult:
        """
        解析原始文本为 JSON

        Args:
            raw_text: 原始响应文本
            schema_name: 期望的 schema 名称（如 FusionRecommendation）

        Returns:
            ParseResult: 解析结果
        """
        logger.debug("[PARSER] 开始解析 LLM 响应")
        logger.debug("[PARSER]   schema_name: %s", schema_name)
        logger.debug("[PARSER]   raw_text 长度: %d", len(raw_text) if raw_text else 0)

        if not raw_text or not raw_text.strip():
            logger.warning("[PARSER] ❌ 响应为空")
            return ParseResult(
                success=False,
                error_message="Empty response text",
                error_type=ParseErrorType.EMPTY_RESPONSE,
                raw_text=raw_text,
            )

        # 尝试提取 JSON（带追踪提取方法）
        logger.debug("[PARSER] 尝试提取 JSON...")
        json_text, extraction_method = StructuredOutputParser._extract_json_with_method(raw_text)

        if not json_text:
            logger.warning("[PARSER] ❌ 未找到有效 JSON")
            logger.warning("[PARSER]   raw_text 预览: %s", raw_text[:200] if len(raw_text) > 200 else raw_text)
            return ParseResult(
                success=False,
                error_message="No valid JSON found in response",
                error_type=ParseErrorType.BALANCED_JSON_NOT_FOUND,
                raw_text=raw_text,
                warnings=["Response does not contain valid JSON"],
            )

        logger.debug("[PARSER] 提取到 JSON，长度: %d, 方法: %s", len(json_text), extraction_method)

        # 解析 JSON
        try:
            data = json.loads(json_text)
            logger.debug("[PARSER] JSON 解析成功")
        except json.JSONDecodeError as e:
            logger.warning("[PARSER] ❌ JSON 解析失败: %s", str(e))
            logger.warning("[PARSER]   错误位置: %d", e.pos)
            logger.warning("[PARSER]   json_text 预览: %s", json_text[:200] if len(json_text) > 200 else json_text)
            return ParseResult(
                success=False,
                error_message=f"JSON parse error: {str(e)}",
                error_type=ParseErrorType.JSON_DECODE_ERROR,
                raw_text=raw_text,
                warnings=[f"Invalid JSON at position {e.pos}"],
                extraction_method=extraction_method,
            )

        # 如果指定了 schema，进行验证
        warnings: list[str] = []

        if schema_name == "FusionRecommendation":
            validation_result = StructuredOutputParser._validate_fusion_recommendation(data)
            if not validation_result[0]:
                return ParseResult(
                    success=False,
                    error_message=validation_result[1],
                    error_type=ParseErrorType.SCHEMA_VALIDATION_FAILED,
                    raw_text=raw_text,
                    warnings=validation_result[2],
                    extraction_method=extraction_method,
                )
            warnings.extend(validation_result[2])

        return ParseResult(
            success=True,
            data=data,
            raw_text=raw_text,
            warnings=warnings,
            extraction_method=extraction_method,
        )

    @staticmethod
    def parse_fusion_recommendation(raw_text: str) -> ParseResult:
        """
        解析 FusionRecommendation

        Args:
            raw_text: 原始响应文本

        Returns:
            ParseResult: 解析结果
        """
        result = StructuredOutputParser.parse(raw_text, "FusionRecommendation")

        if result.success and result.data:
            try:
                # 验证 Pydantic 模型
                FusionRecommendation.model_validate(result.data)
            except ValidationError as e:
                return ParseResult(
                    success=False,
                    error_message=f"Validation error: {str(e)}",
                    raw_text=raw_text,
                    warnings=["Data does not match FusionRecommendation schema"],
                )

        return result

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """
        从文本中提取 JSON（向后兼容方法）

        支持：
        - 纯 JSON
        - Markdown 代码块中的 JSON (```json ... ```)
        - 前后有其他文本的 JSON
        - 嵌套大括号的正确匹配

        Args:
            text: 原始文本

        Returns:
            提取的 JSON 字符串，或 None
        """
        result, _ = StructuredOutputParser._extract_json_with_method(text)
        return result

    @staticmethod
    def _extract_json_with_method(text: str) -> tuple[Optional[str], str]:
        """
        从文本中提取 JSON（带提取方法追踪）

        支持：
        - 纯 JSON（整个文本是 JSON）
        - Fenced JSON：```json ... ```
        - 普通 fenced block：``` ... ```
        - 前后带说明文字时提取首个有效 JSON object
        - 首个平衡大括号 JSON 提取兜底

        优先级：
        1. Markdown 代码块 (```json ... ```)
        2. 普通 fenced block (``` ... ```)
        3. 纯 JSON（整个文本是一个 JSON 对象）
        4. 嵌套大括号匹配提取第一个完整 JSON 对象

        Args:
            text: 原始文本

        Returns:
            tuple: (提取的 JSON 字符串, 提取方法名称)
        """
        text = text.strip()

        # 1. 尝试提取 fenced JSON 代码块（带 json 标签）
        fenced_json_pattern = r"```json\s*([\s\S]*?)```"
        match = re.search(fenced_json_pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if candidate.startswith("{"):
                # 尝试验证 JSON
                try:
                    json.loads(candidate)
                    logger.debug("[PARSER] 从 fenced JSON 代码块提取成功")
                    return candidate, "fenced_json"
                except json.JSONDecodeError as e:
                    logger.debug("[PARSER] fenced JSON 解析失败: %s, 尝试其他方法", str(e))
                    # 记录但仍尝试其他方法

        # 2. 尝试提取普通 fenced 代码块（不带标签或带其他标签）
        generic_fenced_pattern = r"```(?:\w*)\s*([\s\S]*?)```"
        for match in re.finditer(generic_fenced_pattern, text):
            candidate = match.group(1).strip()
            if candidate.startswith("{"):
                try:
                    json.loads(candidate)
                    logger.debug("[PARSER] 从普通 fenced 代码块提取成功")
                    return candidate, "fenced_block"
                except json.JSONDecodeError:
                    continue

        # 3. 尝试直接解析（整个文本是 JSON）
        if text.startswith("{"):
            try:
                json.loads(text)
                logger.debug("[PARSER] 整个文本是有效 JSON")
                return text, "pure_json"
            except json.JSONDecodeError:
                pass  # 继续尝试其他方法

        # 4. 使用嵌套大括号匹配提取第一个完整 JSON 对象
        json_obj = StructuredOutputParser._extract_balanced_json(text)
        if json_obj:
            logger.debug("[PARSER] 通过嵌套大括号匹配提取 JSON")
            return json_obj, "balanced_braces"

        # 5. 最后尝试：查找第一个 JSON 对象（可能嵌套不完整）
        first_brace = text.find("{")
        if first_brace != -1:
            # 尝试从第一个 { 开始找到一个可能的 JSON
            last_brace = text.rfind("}")
            if last_brace > first_brace:
                candidate = text[first_brace:last_brace + 1]
                try:
                    json.loads(candidate)
                    logger.debug("[PARSER] 通过首尾大括号提取 JSON")
                    return candidate, "first_last_brace"
                except json.JSONDecodeError:
                    pass

        logger.warning("[PARSER] 未找到任何有效 JSON")
        return None, "none"

    @staticmethod
    def _extract_balanced_json(text: str) -> Optional[str]:
        """
        使用大括号平衡算法提取第一个完整的 JSON 对象

        正确处理嵌套的大括号，适用于包含多个 JSON 对象或
        JSON 对象前后有其他文本的情况。

        Args:
            text: 原始文本

        Returns:
            提取的 JSON 字符串，或 None
        """
        # 找到第一个 {
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue

            if char == "\\" and in_string:
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if not in_string:
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        # 找到匹配的闭合大括号
                        candidate = text[start:i + 1]
                        # 验证是否是有效 JSON
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            # 不是有效 JSON，继续搜索下一个 {
                            next_start = text.find("{", start + 1)
                            if next_start != -1:
                                return StructuredOutputParser._extract_balanced_json(text[next_start:])
                            return None

        return None

    @staticmethod
    def _validate_fusion_recommendation(data: dict[str, Any]) -> tuple[bool, str, list[str]]:
        """
        验证 FusionRecommendation 数据

        Args:
            data: 解析后的数据

        Returns:
            tuple: (是否成功, 错误消息, 警告列表)
        """
        warnings: list[str] = []
        required_fields = ["summary", "decision", "reasoning", "risks", "missing_information", "next_actions", "confidence"]

        # 检查必填字段
        missing = [f for f in required_fields if f not in data]
        if missing:
            return False, f"Missing required fields: {', '.join(missing)}", warnings

        # 检查 decision 值
        valid_decisions = ["yes", "no", "conditional_yes", "needs_more_information"]
        if data["decision"] not in valid_decisions:
            warnings.append(f"Invalid decision value: {data['decision']}")

        # 检查 confidence 范围
        confidence = data.get("confidence")
        if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
            warnings.append(f"Invalid confidence value: {confidence}")

        # 检查列表字段
        list_fields = ["reasoning", "risks", "missing_information", "next_actions"]
        for field_name in list_fields:
            if not isinstance(data.get(field_name), list):
                warnings.append(f"Field {field_name} should be a list")

        return True, "", warnings


__all__ = [
    "StructuredOutputParser",
    "ParseResult",
    "ParseErrorType",
]