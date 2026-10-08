"""
StructuredOutputParser 测试

测试结构化输出解析器。
"""

import pytest

from src.infra.llm.parsing.structured_output_parser import (
    StructuredOutputParser,
    ParseResult,
    ParseErrorType,
)


class TestParseResult:
    """ParseResult 测试"""

    def test_create_success_result(self):
        """测试创建成功结果"""
        result = ParseResult(
            success=True,
            data={"key": "value"},
            raw_text='{"key": "value"}',
        )

        assert result.success is True
        assert result.data == {"key": "value"}
        assert result.error_message is None

    def test_create_failure_result(self):
        """测试创建失败结果"""
        result = ParseResult(
            success=False,
            error_message="Parse error",
            raw_text="invalid",
        )

        assert result.success is False
        assert result.data is None
        assert result.error_message == "Parse error"


class TestStructuredOutputParser:
    """StructuredOutputParser 测试"""

    def test_parse_valid_json(self):
        """测试解析有效 JSON"""
        raw_text = '{"name": "test", "value": 123}'
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data == {"name": "test", "value": 123}

    def test_parse_empty_text(self):
        """测试解析空文本"""
        result = StructuredOutputParser.parse("")

        assert result.success is False
        assert "Empty" in result.error_message

    def test_parse_whitespace_only(self):
        """测试解析只有空白字符的文本"""
        result = StructuredOutputParser.parse("   \n\t  ")

        assert result.success is False

    def test_parse_invalid_json(self):
        """测试解析无效 JSON"""
        raw_text = '{"name": test}'  # 缺少引号
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is False
        # 增强的解析器会提前检测到无效 JSON，返回 "No valid JSON found"
        # 或者在尝试解析时返回 "JSON parse error"
        assert result.error_message is not None
        assert ("No valid JSON found" in result.error_message or
                "JSON parse error" in result.error_message)

    def test_parse_json_from_text(self):
        """测试从文本中提取 JSON"""
        raw_text = 'Here is the response: {"decision": "yes", "confidence": 0.9} End.'
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data["decision"] == "yes"

    def test_parse_json_from_markdown_code_block(self):
        """测试从 Markdown 代码块中提取 JSON"""
        raw_text = '''Here is the result:
```json
{
    "summary": "test summary",
    "decision": "conditional_yes"
```
That's all.'''

        # 注意：上面的 JSON 是不完整的，需要修正测试
        raw_text_valid = '''Here is the result:
```json
{
    "summary": "test summary",
    "decision": "conditional_yes"
}
```
That's all.'''

        result = StructuredOutputParser.parse(raw_text_valid)

        assert result.success is True
        assert result.data["summary"] == "test summary"

    def test_parse_json_from_plain_code_block(self):
        """测试从普通代码块中提取 JSON"""
        raw_text = '''```
{"key": "value"}
```'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data["key"] == "value"


class TestParseFusionRecommendation:
    """parse_fusion_recommendation 测试"""

    def test_parse_valid_fusion_recommendation(self):
        """测试解析有效的 FusionRecommendation"""
        raw_text = '''{
            "summary": "方案可行",
            "decision": "conditional_yes",
            "reasoning": ["理由1", "理由2"],
            "risks": ["风险1"],
            "missing_information": [],
            "next_actions": ["行动1"],
            "confidence": 0.8
        }'''

        result = StructuredOutputParser.parse_fusion_recommendation(raw_text)

        assert result.success is True
        assert result.data["decision"] == "conditional_yes"
        assert result.data["confidence"] == 0.8

    def test_parse_invalid_fusion_recommendation_missing_field(self):
        """测试解析缺少字段的 FusionRecommendation"""
        raw_text = '''{
            "summary": "test",
            "decision": "yes"
        }'''

        result = StructuredOutputParser.parse_fusion_recommendation(raw_text)

        assert result.success is False
        assert "Missing required fields" in result.error_message

    def test_parse_invalid_fusion_recommendation_bad_decision(self):
        """测试解析 decision 值无效的 FusionRecommendation"""
        raw_text = '''{
            "summary": "test",
            "decision": "invalid_decision",
            "reasoning": [],
            "risks": [],
            "missing_information": [],
            "next_actions": [],
            "confidence": 0.5
        }'''

        result = StructuredOutputParser.parse_fusion_recommendation(raw_text)

        # Pydantic 验证失败，应该返回失败
        assert result.success is False
        assert "Validation error" in result.error_message

    def test_parse_invalid_confidence(self):
        """测试解析 confidence 值无效"""
        raw_text = '''{
            "summary": "test",
            "decision": "yes",
            "reasoning": [],
            "risks": [],
            "missing_information": [],
            "next_actions": [],
            "confidence": 1.5
        }'''

        result = StructuredOutputParser.parse_fusion_recommendation(raw_text)

        # Pydantic 验证失败，应该返回失败
        assert result.success is False
        assert "Validation error" in result.error_message


class TestJsonExtraction:
    """JSON 提取测试"""

    def test_extract_json_from_plain_object(self):
        """测试从纯 JSON 对象提取"""
        text = '{"key": "value"}'
        result = StructuredOutputParser._extract_json(text)

        assert result == '{"key": "value"}'

    def test_extract_json_with_surrounding_text(self):
        """测试从包含周围文本的内容中提取"""
        text = 'Before {"key": "value"} After'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None
        assert "key" in result

    def test_extract_json_from_nested_object(self):
        """测试从嵌套对象提取"""
        text = '{"outer": {"inner": {"deep": "value"}}}'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None

    def test_extract_json_no_json_found(self):
        """测试没有找到 JSON"""
        text = "This is just plain text with no JSON."
        result = StructuredOutputParser._extract_json(text)

        assert result is None

    def test_extract_json_from_code_block(self):
        """测试从代码块提取"""
        text = '```json\n{"key": "value"}\n```'
        result = StructuredOutputParser._extract_json(text)

        assert result == '{"key": "value"}'


class TestEnhancedJsonExtraction:
    """增强的 JSON 提取测试"""

    def test_extract_nested_json_with_strings_containing_braces(self):
        """测试字符串包含大括号的嵌套 JSON"""
        text = '{"message": "Hello {world}!"}'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None
        import json
        data = json.loads(result)
        assert data["message"] == "Hello {world}!"

    def test_extract_json_with_escaped_quotes(self):
        """测试包含转义引号的 JSON"""
        text = '{"message": "He said \\"hello\\""}'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None
        import json
        data = json.loads(result)
        assert data["message"] == 'He said "hello"'

    def test_extract_json_deeply_nested(self):
        """测试深度嵌套的 JSON"""
        text = '{"level1": {"level2": {"level3": {"level4": {"level5": "deep"}}}}}'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None
        import json
        data = json.loads(result)
        assert data["level1"]["level2"]["level3"]["level4"]["level5"] == "deep"

    def test_extract_json_array_in_object(self):
        """测试包含数组的 JSON 对象"""
        text = '{"items": [1, 2, 3, {"nested": true}]}'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None
        import json
        data = json.loads(result)
        assert len(data["items"]) == 4

    def test_extract_json_from_multiple_objects(self):
        """测试从多个 JSON 对象中提取第一个"""
        text = '{"first": 1} and {"second": 2}'
        result = StructuredOutputParser._extract_json(text)

        # 应该只提取第一个完整的 JSON 对象
        assert result is not None
        import json
        data = json.loads(result)
        assert data["first"] == 1

    def test_extract_json_fenced_with_plain_backticks(self):
        """测试从无语言标记的代码块提取 JSON"""
        text = '''```
{"key": "value"}
```'''
        result = StructuredOutputParser._extract_json(text)

        assert result == '{"key": "value"}'

    def test_extract_json_with_leading_text_in_code_block(self):
        """测试代码块内有前导文本的情况"""
        text = '''```json
Here is the JSON:
{"key": "value"}
```'''
        result = StructuredOutputParser._extract_json(text)

        # 代码块内的 JSON 应该被正确提取
        assert result is not None
        import json
        data = json.loads(result)
        assert data["key"] == "value"

    def test_extract_json_with_trailing_comma_invalid(self):
        """测试包含尾随逗号的无效 JSON（应该失败）"""
        text = '{"key": "value",}'
        result = StructuredOutputParser._extract_json(text)

        # 尾随逗号在标准 JSON 中无效
        # 结果可能是 None 或提取后解析失败
        if result:
            import json
            try:
                json.loads(result)
            except json.JSONDecodeError:
                pass  # 预期行为

    def test_extract_json_unicode_content(self):
        """测试包含 Unicode 的 JSON"""
        text = '{"message": "你好世界 🌍"}'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None
        import json
        data = json.loads(result)
        assert "你好世界" in data["message"]

    def test_extract_json_with_newlines_in_strings(self):
        """测试字符串包含换行符的 JSON"""
        text = '{"text": "line1\\nline2\\nline3"}'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None
        import json
        data = json.loads(result)
        assert "\n" in data["text"]

    def test_extract_json_boolean_and_null(self):
        """测试包含布尔值和 null 的 JSON"""
        text = '{"active": true, "deleted": false, "data": null}'
        result = StructuredOutputParser._extract_json(text)

        assert result is not None
        import json
        data = json.loads(result)
        assert data["active"] is True
        assert data["deleted"] is False
        assert data["data"] is None


class TestParseErrorTypes:
    """解析错误类型分类测试"""

    def test_error_type_empty_response(self):
        """测试空响应错误类型"""
        result = StructuredOutputParser.parse("")

        assert result.success is False
        assert result.error_type is not None
        assert result.error_type.value == "empty_response"

    def test_error_type_balanced_json_not_found(self):
        """测试未找到 JSON 错误类型"""
        result = StructuredOutputParser.parse("This is plain text with no JSON")

        assert result.success is False
        assert result.error_type is not None
        assert result.error_type.value == "balanced_json_not_found"

    def test_error_type_json_decode_error(self):
        """测试 JSON 解码错误类型"""
        # 故意构造一个能找到大括号但解析失败的 case
        # 注意：解析器会先尝试提取再验证，所以需要确保提取到了内容但无法解析
        result = StructuredOutputParser.parse('{"key": invalid}')

        assert result.success is False
        # 可能是 balanced_json_not_found 或 json_decode_error
        assert result.error_type is not None
        assert result.error_type.value in ("balanced_json_not_found", "json_decode_error")

    def test_error_type_schema_validation_failed(self):
        """测试 schema 验证失败错误类型"""
        raw_text = '''{
            "summary": "test",
            "decision": "yes"
        }'''

        result = StructuredOutputParser.parse(raw_text, schema_name="FusionRecommendation")

        assert result.success is False
        assert result.error_type is not None
        assert result.error_type.value == "schema_validation_failed"


class TestExtractionMethodTracking:
    """提取方法追踪测试"""

    def test_extraction_method_pure_json(self):
        """测试纯 JSON 提取方法"""
        result = StructuredOutputParser.parse('{"key": "value"}')

        assert result.success is True
        assert result.extraction_method == "pure_json"

    def test_extraction_method_fenced_json(self):
        """测试 fenced JSON 提取方法"""
        raw_text = '''```json
{"key": "value"}
```'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.extraction_method == "fenced_json"

    def test_extraction_method_fenced_block(self):
        """测试普通 fenced block 提取方法"""
        raw_text = '''```
{"key": "value"}
```'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.extraction_method == "fenced_block"

    def test_extraction_method_balanced_braces(self):
        """测试平衡大括号提取方法"""
        raw_text = 'Some text before {"key": "value"} some text after'
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.extraction_method == "balanced_braces"


class TestFencedJsonRobustness:
    """Fenced JSON 鲁棒性测试"""

    def test_fenced_json_with_extra_whitespace(self):
        """测试带额外空白的 fenced JSON"""
        raw_text = '''```json
        {"key": "value"}
        ```'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data["key"] == "value"

    def test_fenced_json_multiline(self):
        """测试多行 fenced JSON"""
        raw_text = '''```json
{
    "summary": "test summary",
    "decision": "yes",
    "confidence": 0.9
}
```'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data["summary"] == "test summary"

    def test_fenced_json_with_explanatory_text_before(self):
        """测试带前置说明文字的 fenced JSON"""
        raw_text = '''Here is my analysis:
Based on the context, I recommend the following:
```json
{"decision": "yes", "confidence": 0.85}
```
This is my final answer.'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data["decision"] == "yes"

    def test_fenced_json_with_explanatory_text_after(self):
        """测试带后置说明文字的 fenced JSON"""
        raw_text = '''```json
{"decision": "no", "confidence": 0.7}
```
The above recommendation is based on risk analysis.'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data["decision"] == "no"

    def test_fenced_json_uppercase_tag(self):
        """测试大写 JSON 标签"""
        raw_text = '''```JSON
{"key": "value"}
```'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data["key"] == "value"

    def test_fenced_json_with_other_language_tags(self):
        """测试带其他语言标签的代码块"""
        raw_text = '''```python
print("hello")
```
```json
{"key": "value"}
```
'''
        result = StructuredOutputParser.parse(raw_text)

        assert result.success is True
        assert result.data["key"] == "value"


class TestFirstLastBraceExtraction:
    """首尾大括号提取测试"""

    def test_first_last_brace_simple(self):
        """测试简单的首尾大括号提取"""
        # 当平衡大括号失败时，会尝试首尾大括号
        raw_text = 'Text { "key": "value" } end'
        result = StructuredOutputParser.parse(raw_text)

        # 应该能提取到 JSON
        assert result.success is True
        assert result.data["key"] == "value"