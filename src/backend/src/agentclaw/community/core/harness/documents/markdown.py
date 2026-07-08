"""MarkdownDocument — structured markdown parsing and manipulation.

Zero external dependencies; uses only stdlib + re.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(slots=True)
class Section:
    """A markdown section (header + body)."""

    title: str
    level: int
    start_line: int
    end_line: int
    content: str  # includes the header line
    body: str  # body only, excludes header


@dataclass(slots=True)
class MarkdownDocument:
    """Structured markdown document with section tree support.

    Behaves like a mutable in-memory DOM for markdown.
    """

    meta: dict[str, str] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    raw_text: str = ""

    @classmethod
    def parse(cls, raw_text: str, bot_id: str = "", file_type: str = "") -> MarkdownDocument:
        """Parse raw markdown text into structured sections."""
        lines = raw_text.splitlines(keepends=True)
        sections: list[Section] = []
        current_start = 0
        current_title = "(root)"
        current_level = 0
        current_lines: list[str] = []

        def _flush(end_idx: int) -> None:
            if current_lines:
                body_lines = current_lines[1:] if len(current_lines) > 1 else []
                sections.append(
                    Section(
                        title=current_title,
                        level=current_level,
                        start_line=current_start,
                        end_line=end_idx,
                        content="".join(current_lines),
                        body="".join(body_lines).rstrip("\n"),
                    )
                )

        for i, line in enumerate(lines):
            m = re.match(r"^(#{1,6})\s+(.*)$", line.rstrip("\n"))
            if m:
                _flush(i)
                current_level = len(m.group(1))
                current_title = m.group(2).strip()
                current_start = i
                current_lines = [line]
            else:
                current_lines.append(line)

        _flush(len(lines))

        checksum = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        return cls(
            meta={
                "file_type": file_type,
                "bot_id": bot_id,
                "version": "1.0",
                "checksum": checksum,
            },
            sections=sections,
            raw_text=raw_text,
        )

    def serialize(self) -> str:
        """Rebuild raw markdown from sections."""
        parts: list[str] = []
        for sec in self.sections:
            parts.append(sec.content)
        return "".join(parts).rstrip("\n") + "\n"

    def get_section(self, title: str) -> Section | None:
        for sec in self.sections:
            if sec.title == title:
                return sec
        return None

    def replace_section(self, title: str, new_body: str) -> bool:
        for i, sec in enumerate(self.sections):
            if sec.title == title:
                new_content = f"{'#' * sec.level} {sec.title}\n\n{new_body}\n"
                self.sections[i] = Section(
                    title=sec.title,
                    level=sec.level,
                    start_line=sec.start_line,
                    end_line=sec.start_line + new_content.count("\n"),
                    content=new_content,
                    body=new_body,
                )
                self.raw_text = self.serialize()
                return True
        return False

    def insert_after(self, anchor_title: str, title: str, body: str, level: int = 2) -> bool:
        sec = self.get_section(anchor_title)
        if sec is None:
            return False
        for i, s in enumerate(self.sections):
            if s.title == anchor_title:
                new_content = f"\n{'#' * level} {title}\n\n{body}\n"
                new_sec = Section(
                    title=title,
                    level=level,
                    start_line=s.end_line,
                    end_line=s.end_line + new_content.count("\n"),
                    content=new_content,
                    body=body,
                )
                self.sections.insert(i + 1, new_sec)
                self.raw_text = self.serialize()
                return True
        return False

    def delete_section(self, title: str) -> bool:
        for i, sec in enumerate(self.sections):
            if sec.title == title:
                self.sections.pop(i)
                self.raw_text = self.serialize()
                return True
        return False

    def compute_diff(self, other: MarkdownDocument) -> str:
        a = self.raw_text.splitlines(keepends=True)
        b = other.raw_text.splitlines(keepends=True)
        return "".join(difflib.unified_diff(a, b, fromfile="before", tofile="after", lineterm=""))

    def __iter__(self) -> Iterator[Section]:
        return iter(self.sections)

    def __len__(self) -> int:
        return len(self.sections)
