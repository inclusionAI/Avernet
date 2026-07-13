"""
Markdown Parser

M2: Worker Profiling & Extraction

最小可用的 Markdown 解析器。

只实现以下能力：
- 标题解析
- 列表解析
- 段落解析
- 空文档/噪音文档基本处理

不实现复杂 Markdown AST，不为未来做过度抽象。
"""

from __future__ import annotations

import re
from typing import Any, Optional


class MarkdownParser:
    """
    最小可用 Markdown 解析器

    只提供基本的标题、列表、段落解析能力。
    """

    def __init__(self, content: str):
        """
        初始化解析器

        Args:
            content: Markdown 内容
        """
        self.content = content
        self._lines: list[str] = []
        self._parse()

    def _parse(self) -> None:
        """解析文档内容"""
        if not self.content or not self.content.strip():
            self._lines = []
            return

        self._lines = self.content.split("\n")

    def get_headings(self) -> list[dict[str, Any]]:
        """
        获取所有标题

        Returns:
            标题列表，每个标题包含：
                - level: 标题级别 (1-6)
                - text: 标题文本
                - line: 行号 (1-indexed)
        """
        headings = []
        heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$")

        for i, line in enumerate(self._lines, start=1):
            match = heading_pattern.match(line)
            if match:
                headings.append({
                    "level": len(match.group(1)),
                    "text": match.group(2).strip(),
                    "line": i,
                })

        return headings

    def get_lists(self) -> list[dict[str, Any]]:
        """
        获取所有列表

        Returns:
            列表列表，每个列表包含：
                - items: 列表项
                - heading_context: 最近的上级标题
                - start_line: 起始行号
        """
        lists: list[dict[str, Any]] = []
        current_items: list[str] = []
        current_heading: str | None = None
        current_start_line: int | None = None

        list_item_pattern = re.compile(r"^(\s*)[-*+]\s+(.+)$")

        # 先获取所有标题位置
        headings = {h["line"]: h["text"] for h in self.get_headings()}

        def flush_list() -> None:
            nonlocal current_items, current_start_line
            if current_items:
                lists.append({
                    "items": current_items,
                    "heading_context": current_heading,
                    "start_line": current_start_line,
                })
                current_items = []
                current_start_line = None

        for i, line in enumerate(self._lines, start=1):
            # 更新当前标题上下文
            if i in headings:
                current_heading = headings[i]
                # 遇到标题时结束当前列表
                flush_list()
                continue

            match = list_item_pattern.match(line)
            if match:
                indent = len(match.group(1))
                item_text = match.group(2).strip()

                # 如果当前没有列表，开始新列表
                if not current_items:
                    current_start_line = i
                    current_items = [item_text]
                else:
                    # 检查是否是连续的列表项（无空行分隔）
                    # 简化处理：只用缩进标识层级，用缩进标记嵌套
                    if indent >= 2:
                        current_items.append("  " + item_text)
                    else:
                        current_items.append(item_text)
            else:
                # 空行或非列表项，结束当前列表
                # 但只有空行才结束，其他内容（如标题）已经处理
                if line.strip() == "":
                    flush_list()

        # 处理最后一个列表
        flush_list()

        return lists

    def get_paragraphs(self) -> list[dict[str, Any]]:
        """
        获取所有段落

        段落是非标题、非列表、非代码块的连续文本。

        Returns:
            段落列表，每个段落包含：
                - text: 段落文本
                - heading_context: 最近的上级标题
                - start_line: 起始行号
        """
        paragraphs: list[dict[str, Any]] = []
        current_para_lines: list[str] = []
        current_para_start: int | None = None
        current_heading: str | None = None
        in_code_block = False

        heading_pattern = re.compile(r"^#{1,6}\s+")
        list_pattern = re.compile(r"^\s*[-*+]\s+")
        code_block_pattern = re.compile(r"^```")

        headings = {h["line"]: h["text"] for h in self.get_headings()}

        def flush_paragraph() -> None:
            nonlocal current_para_lines, current_para_start
            if current_para_lines:
                text = " ".join(current_para_lines).strip()
                if text:
                    paragraphs.append({
                        "text": text,
                        "heading_context": current_heading,
                        "start_line": current_para_start,
                    })
                current_para_lines = []
                current_para_start = None

        for i, line in enumerate(self._lines, start=1):
            # 更新当前标题上下文
            if i in headings:
                current_heading = headings[i]
                flush_paragraph()
                continue

            # 处理代码块
            if code_block_pattern.match(line):
                in_code_block = not in_code_block
                flush_paragraph()
                continue

            if in_code_block:
                continue

            # 跳过标题
            if heading_pattern.match(line):
                flush_paragraph()
                continue

            # 跳过列表
            if list_pattern.match(line):
                flush_paragraph()
                continue

            # 空行结束段落
            stripped = line.strip()
            if not stripped:
                flush_paragraph()
                continue

            # 添加到当前段落
            if current_para_start is None:
                current_para_start = i
            current_para_lines.append(stripped)

        flush_paragraph()
        return paragraphs

    def get_section(self, heading_text: str) -> str:
        """
        获取指定节的内容

        Args:
            heading_text: 标题文本（部分匹配即可）

        Returns:
            节内容文本，如果不存在返回空字符串
        """
        headings = self.get_headings()

        # 找到目标标题
        target_idx = None
        for i, h in enumerate(headings):
            if heading_text.lower() in h["text"].lower():
                target_idx = i
                break

        if target_idx is None:
            return ""

        start_line = headings[target_idx]["line"]
        end_line = len(self._lines)

        # 找到下一个同级或更高级标题
        target_level = headings[target_idx]["level"]
        for i in range(target_idx + 1, len(headings)):
            if headings[i]["level"] <= target_level:
                end_line = headings[i]["line"] - 1
                break

        # 提取内容
        section_lines = self._lines[start_line:end_line]
        return "\n".join(section_lines).strip()

    def get_line_range_for_section(self, heading_text: str) -> dict[str, int] | None:
        """
        获取指定节的行范围

        Args:
            heading_text: 标题文本

        Returns:
            行范围字典，包含 start 和 end，如果不存在返回 None
        """
        headings = self.get_headings()

        target_idx = None
        for i, h in enumerate(headings):
            if heading_text.lower() in h["text"].lower():
                target_idx = i
                break

        if target_idx is None:
            return None

        start_line = headings[target_idx]["line"]
        end_line = len(self._lines)

        target_level = headings[target_idx]["level"]
        for i in range(target_idx + 1, len(headings)):
            if headings[i]["level"] <= target_level:
                end_line = headings[i]["line"] - 1
                break

        return {"start": start_line, "end": end_line}

    def get_all_text(self) -> str:
        """
        获取所有纯文本（去除 markdown 格式标记）

        Returns:
            纯文本内容
        """
        result_lines: list[str] = []
        in_code_block = False

        for line in self._lines:
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue

            if in_code_block:
                continue

            # 去除标题标记
            clean_line = re.sub(r"^#{1,6}\s+", "", line)
            # 去除列表标记
            clean_line = re.sub(r"^\s*[-*+]\s+", "", clean_line)

            if clean_line.strip():
                result_lines.append(clean_line.strip())

        return "\n".join(result_lines)

    def find_text(self, text: str) -> dict[str, Any] | None:
        """
        查找文本并获取上下文

        Args:
            text: 要查找的文本

        Returns:
            找到的文本信息，包含：
                - text: 所在行文本
                - heading_context: 最近的上级标题
                - line: 行号
            如果未找到返回 None
        """
        headings = self.get_headings()
        heading_lines = {h["line"] for h in headings}

        current_heading: str | None = None

        for i, line in enumerate(self._lines, start=1):
            # 更新当前标题
            for h in headings:
                if h["line"] == i:
                    current_heading = h["text"]
                    break

            if text in line:
                return {
                    "text": line.strip(),
                    "heading_context": current_heading,
                    "line": i,
                }

        return None


__all__ = ["MarkdownParser"]