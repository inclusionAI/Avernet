"""
Tests for Markdown Parser

M2: Worker Profiling & Extraction

测试范围：
- 标题解析
- 列表解析
- 段落解析
- 空文档/噪音文档处理

注意：只做最小可用能力，不实现复杂 Markdown AST。
"""

from __future__ import annotations

import pytest


class TestMarkdownParser:
    """测试 MarkdownParser 基本功能"""

    def test_parser_importable(self):
        """测试 Parser 可导入"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        assert MarkdownParser is not None

    def test_parse_headings(self):
        """测试标题解析"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Main Title

## Section 1

### Subsection 1.1

#### Deep heading
"""
        parser = MarkdownParser(content)
        headings = parser.get_headings()

        assert len(headings) == 4
        assert headings[0]["level"] == 1
        assert headings[0]["text"] == "Main Title"
        assert headings[1]["level"] == 2
        assert headings[1]["text"] == "Section 1"
        assert headings[2]["level"] == 3
        assert headings[3]["level"] == 4

    def test_parse_heading_with_line_number(self):
        """测试标题带行号"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """# Title 1

Some content

## Title 2
"""
        parser = MarkdownParser(content)
        headings = parser.get_headings()

        assert headings[0]["line"] == 1
        assert headings[1]["line"] == 5

    def test_parse_lists(self):
        """测试列表解析"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Capabilities

- Item 1
- Item 2
- Item 3

## Other section

* Bullet item 1
* Bullet item 2
"""
        parser = MarkdownParser(content)
        lists = parser.get_lists()

        # 应该找到两个列表
        assert len(lists) == 2

        # 第一个列表
        first_list = lists[0]
        assert len(first_list["items"]) == 3
        assert first_list["items"][0] == "Item 1"
        assert first_list["items"][1] == "Item 2"
        assert first_list["items"][2] == "Item 3"

        # 第二个列表
        second_list = lists[1]
        assert len(second_list["items"]) == 2
        assert second_list["items"][0] == "Bullet item 1"

    def test_parse_list_with_heading_context(self):
        """测试列表带标题上下文"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Capabilities

- Test capability 1
- Test capability 2
"""
        parser = MarkdownParser(content)
        lists = parser.get_lists()

        assert lists[0]["heading_context"] == "Capabilities"

    def test_parse_paragraphs(self):
        """测试段落解析"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Section

This is paragraph 1.

This is paragraph 2.
It has multiple lines.

## Another Section

This is paragraph 3.
"""
        parser = MarkdownParser(content)
        paragraphs = parser.get_paragraphs()

        # 应该有 3 个段落（标题不算段落）
        assert len(paragraphs) == 3
        assert "paragraph 1" in paragraphs[0]["text"]
        assert "paragraph 2" in paragraphs[1]["text"]
        assert "paragraph 3" in paragraphs[2]["text"]

    def test_get_section_content(self):
        """测试获取指定节内容"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Capabilities

- Item 1
- Item 2

# Constraints

- Rule 1
- Rule 2
"""
        parser = MarkdownParser(content)

        capabilities_section = parser.get_section("Capabilities")
        assert "Item 1" in capabilities_section
        assert "Item 2" in capabilities_section
        assert "Rule 1" not in capabilities_section

        constraints_section = parser.get_section("Constraints")
        assert "Rule 1" in constraints_section
        assert "Item 1" not in constraints_section

    def test_get_section_not_found_returns_empty(self):
        """测试节不存在返回空字符串"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Capabilities

- Item 1
"""
        parser = MarkdownParser(content)

        result = parser.get_section("NonExistent")
        assert result == ""

    def test_empty_document(self):
        """测试空文档处理"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        parser = MarkdownParser("")

        assert parser.get_headings() == []
        assert parser.get_lists() == []
        assert parser.get_paragraphs() == []

    def test_whitespace_only_document(self):
        """测试仅含空白的文档"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        parser = MarkdownParser("   \n\t\n   ")

        assert parser.get_headings() == []
        assert parser.get_lists() == []

    def test_noisy_document_with_code_blocks(self):
        """测试包含代码块的噪音文档"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Code Example

```python
def hello():
    print("Hello, World!")
```

## After Code

- Item 1
"""
        parser = MarkdownParser(content)

        # 代码块不应被解析为段落
        headings = parser.get_headings()
        assert len(headings) == 2

        # 列表仍应被正确解析
        lists = parser.get_lists()
        assert len(lists) == 1
        assert lists[0]["items"][0] == "Item 1"

    def test_get_all_text_stripped(self):
        """测试获取纯文本"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Title

- List item
- Another item

Paragraph here.
"""
        parser = MarkdownParser(content)
        text = parser.get_all_text()

        assert "Title" in text
        assert "List item" in text
        assert "Paragraph here" in text
        assert "```" not in text  # 没有代码块标记

    def test_find_text_with_context(self):
        """测试查找文本并获取上下文"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Capabilities

Information Retrieval (expert)
Data Analysis (advanced)

## Details

This is a detail.
"""
        parser = MarkdownParser(content)

        # 查找 "Information Retrieval"
        result = parser.find_text("Information Retrieval")
        assert result is not None
        assert result["text"] == "Information Retrieval (expert)"
        assert result["heading_context"] == "Capabilities"
        assert result["line"] == 4

    def test_find_text_not_found(self):
        """测试查找不存在的文本"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = "# Test"
        parser = MarkdownParser(content)

        result = parser.find_text("NonExistent")
        assert result is None

    def test_parse_nested_list_indentation(self):
        """测试嵌套列表缩进解析"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# List

- Top level 1
  - Nested 1
  - Nested 2
- Top level 2
"""
        parser = MarkdownParser(content)
        lists = parser.get_lists()

        # 基础测试：应该能解析出列表
        assert len(lists) >= 1
        # 顶层列表项
        top_level_items = [item for item in lists[0]["items"] if not item.startswith("  ")]
        assert len(top_level_items) == 2


class TestMarkdownParserLineTracking:
    """测试 MarkdownParser 行号追踪"""

    def test_get_line_range_for_section(self):
        """测试获取节的行范围"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """# First

Content 1

# Second

Content 2
"""
        parser = MarkdownParser(content)

        line_range = parser.get_line_range_for_section("First")
        assert line_range is not None
        assert line_range["start"] == 1
        assert line_range["end"] == 4  # 不含下一节

    def test_get_line_range_for_last_section(self):
        """测试获取最后一节的行范围"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """# First

Content

# Last

Final content
"""
        parser = MarkdownParser(content)

        line_range = parser.get_line_range_for_section("Last")
        assert line_range is not None
        assert line_range["start"] == 5
        # 最后一节到文档结尾


class TestMarkdownParserEdgeCases:
    """测试 MarkdownParser 边界情况"""

    def test_multiple_blank_lines(self):
        """测试多个空行"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Title




Content here.
"""
        parser = MarkdownParser(content)
        paragraphs = parser.get_paragraphs()

        assert len(paragraphs) == 1
        assert "Content here" in paragraphs[0]["text"]

    def test_mixed_list_markers(self):
        """测试混合列表标记"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Mixed

- Dash item
* Star item
+ Plus item
"""
        parser = MarkdownParser(content)
        lists = parser.get_lists()

        # 所有列表项都应被解析
        all_items = []
        for lst in lists:
            all_items.extend(lst["items"])

        assert len(all_items) >= 3  # 至少 3 个项

    def test_inline_formatting_ignored(self):
        """测试内联格式被忽略"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Test

This has **bold** and *italic* text.
"""
        parser = MarkdownParser(content)
        paragraphs = parser.get_paragraphs()

        # 格式标记应被保留或去除（取决于实现）
        # 这里只确认能解析，不关心格式处理细节
        assert len(paragraphs) == 1

    def test_links_preserved_as_text(self):
        """测试链接作为文本保留"""
        from src.infra.parsers.markdown_parser import MarkdownParser

        content = """
# Links

See [documentation](https://example.com) for details.
"""
        parser = MarkdownParser(content)
        text = parser.get_all_text()

        # 链接文本应保留
        assert "documentation" in text