"""The three ``file.edit`` kinds and the ``description_only`` condition (README §6.2, §6.3).

Every function here is pure text-to-text: a UTF-8 string in, the edited
string out, or :class:`EditDoesNotApply` when the edit does not fit the file
exactly. Nothing is fuzzy: a heading or anchor must occur exactly once, a
diff's context and removed lines must match the file at the positions the
hunk header names. That is what makes a recorded patch reproducible: the
same patch on the same base gives the same bytes anywhere.

Lines are separated by ``"\\n"`` only; a ``"\\r"`` stays part of its line.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from agentclaw.community.core.bot_genome.patch_models import DiffEdit, InsertAfter, ReplaceSection, TextEdit

__all__ = [
    "EditDoesNotApply",
    "apply_edits",
    "apply_unified_diff",
    "description_only",
    "insert_after",
    "replace_section",
]

_HEADING = re.compile(r"^(#{1,6}) [^\n]+$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NO_NEWLINE = "\\ No newline at end of file"
_FRONT_MATTER_KEY = re.compile(r"^[A-Za-z0-9_-]+:")


class EditDoesNotApply(ValueError):
    """The edit does not fit the file exactly; the message says where."""


# ---------------------------------------------------------------------------------------------
# Line helpers


def _split(text: str) -> tuple[list[str], bool]:
    """``(lines without their newline, whether the text ends with a newline)``; ``""`` is ``([], True)``."""
    if text == "":
        return [], True
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
        return lines, True
    return lines, False


def _join(lines: Sequence[str], ends_with_newline: bool) -> str:
    if not lines:
        return ""
    text = "\n".join(lines)
    return text + "\n" if ends_with_newline else text


def _with_final_newline(content: str) -> str:
    """README §6.2: a non-empty ``content`` that does not end with a newline gets one."""
    return content + "\n" if content and not content.endswith("\n") else content


def _heading_level(line: str) -> int | None:
    match = _HEADING.match(line)
    return len(match.group(1)) if match else None


def _headings_outside_fences(lines: Sequence[str]) -> list[tuple[int, int]]:
    """``(index, level)`` of every ATX heading line that is not inside a fenced code block."""
    found: list[tuple[int, int]] = []
    fence: tuple[str, int] | None = None  # (fence character, opening length)
    for index, line in enumerate(lines):
        stripped = line.lstrip(" ")
        marker = stripped[:1]
        if marker in ("`", "~") and stripped.startswith(marker * 3):
            length = len(stripped) - len(stripped.lstrip(marker))
            if fence is None:
                fence = (marker, length)
                continue
            if fence[0] == marker and length >= fence[1] and stripped[length:].strip() == "":
                fence = None
                continue
        if fence is not None:
            continue
        level = _heading_level(line)
        if level is not None:
            found.append((index, level))
    return found


# ---------------------------------------------------------------------------------------------
# The kinds


def replace_section(text: str, heading: str, content: str) -> str:
    """Replace everything under the one ATX heading equal to ``heading``; the heading line stays."""
    lines, ends_nl = _split(text)
    headings = _headings_outside_fences(lines)
    matches = [(index, level) for index, level in headings if lines[index] == heading]
    if len(matches) != 1:
        raise EditDoesNotApply(f"heading {heading!r} occurs {len(matches)} times, not once")
    start, level = matches[0]
    end = len(lines)
    for index, other_level in headings:
        if index > start and other_level <= level:
            end = index
            break
    body, body_nl = _split(_with_final_newline(content))
    kept_tail = lines[end:]
    new_lines = lines[: start + 1] + body + kept_tail
    new_nl = ends_nl if kept_tail else (body_nl if body else ends_nl)
    return _join(new_lines, new_nl)


def insert_after(text: str, anchor: str, content: str) -> str:
    """Insert ``content`` directly below the one line equal to ``anchor``."""
    lines, ends_nl = _split(text)
    positions = [index for index, line in enumerate(lines) if line == anchor]
    if len(positions) != 1:
        raise EditDoesNotApply(f"anchor {anchor!r} occurs {len(positions)} times, not once")
    at = positions[0] + 1
    body, body_nl = _split(_with_final_newline(content))
    tail = lines[at:]
    new_nl = ends_nl if tail else (body_nl if body else ends_nl)
    return _join(lines[:at] + body + tail, new_nl)


def _parse_hunks(diff: str) -> list[tuple[int, int, list[tuple[str, str, bool]]]]:
    """``(old start, old count, [(tag, line, no newline after)])`` per hunk; strict about shape."""
    raw = diff.split("\n")
    if raw and raw[-1] == "":
        raw.pop()
    hunks: list[tuple[int, int, list[tuple[str, str, bool]]]] = []
    at = 0
    while at < len(raw):
        header = _HUNK.match(raw[at])
        if header is None:
            raise EditDoesNotApply(f"line {at + 1} of the diff is not a hunk header: {raw[at]!r}")
        old_start = int(header.group(1))
        old_count = int(header.group(2)) if header.group(2) is not None else 1
        new_count = int(header.group(4)) if header.group(4) is not None else 1
        at += 1
        body: list[tuple[str, str, bool]] = []
        seen_old = seen_new = 0
        while seen_old < old_count or seen_new < new_count:
            if at >= len(raw):
                raise EditDoesNotApply("the diff ends inside a hunk")
            line = raw[at]
            tag = line[:1]
            if tag not in (" ", "-", "+"):
                raise EditDoesNotApply(f"line {at + 1} of the diff is not a hunk line: {line!r}")
            if tag != "+":
                seen_old += 1
            if tag != "-":
                seen_new += 1
            if seen_old > old_count or seen_new > new_count:
                raise EditDoesNotApply(f"hunk at line {at + 1} has more lines than its header counts")
            at += 1
            no_newline = at < len(raw) and raw[at] == _NO_NEWLINE
            if no_newline:
                at += 1
            body.append((tag, line[1:], no_newline))
        hunks.append((old_start, old_count, body))
    if not hunks:
        raise EditDoesNotApply("the diff has no hunk")
    return hunks


def apply_unified_diff(text: str, diff: str) -> str:
    """Apply the hunks of a unified diff exactly: no fuzz, no offset search, hunks in order."""
    lines, ends_nl = _split(text)
    out: list[str] = []
    out_nl = True
    cursor = 0  # old lines consumed so far
    for old_start, old_count, body in _parse_hunks(diff):
        position = old_start - 1 if old_count else old_start
        if position < cursor or position > len(lines):
            raise EditDoesNotApply(f"hunk @@ -{old_start} overlaps the previous hunk or lies past the end")
        out.extend(lines[cursor:position])
        cursor = position
        for tag, line, no_newline in body:
            if tag != "+":
                if cursor >= len(lines) or lines[cursor] != line:
                    actual = lines[cursor] if cursor < len(lines) else None
                    raise EditDoesNotApply(f"line {cursor + 1} is {actual!r}, the diff expects {line!r}")
                if no_newline and (ends_nl or cursor != len(lines) - 1):
                    raise EditDoesNotApply(f"line {cursor + 1} is not the file's last line without a newline")
                if not no_newline and not ends_nl and cursor == len(lines) - 1:
                    raise EditDoesNotApply(
                        f"line {cursor + 1} ends the file without a newline; the diff says it has one"
                    )
                cursor += 1
            if tag != "-":
                out.append(line)
                out_nl = not no_newline
    out.extend(lines[cursor:])
    if cursor < len(lines):
        out_nl = ends_nl
    return _join(out, out_nl if out else True)


def apply_edits(text: str, edits: Iterable[TextEdit]) -> str:
    """Apply ``TextEdit`` records in order."""
    for edit in edits:
        if isinstance(edit, ReplaceSection):
            text = replace_section(text, edit.heading, edit.content)
        elif isinstance(edit, InsertAfter):
            text = insert_after(text, edit.anchor, edit.content)
        elif isinstance(edit, DiffEdit):
            text = apply_unified_diff(text, edit.diff)
        else:
            raise EditDoesNotApply(f"unknown edit kind {type(edit).__name__}")
    return text


# ---------------------------------------------------------------------------------------------
# README §6.3 ``description_only``


def _front_matter(text: str) -> tuple[list[str], str] | None:
    """``(front matter lines, the rest)`` when the text opens with a ``---`` block, else ``None``."""
    lines = text.split("\n")
    if lines[0] != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return lines[1:index], "\n".join(lines[index + 1 :])
    return None


def _description_span(front: Sequence[str]) -> tuple[int, int] | None:
    """The line range of the ``description`` key and its (possibly multi-line) value."""
    start = next((i for i, line in enumerate(front) if line.startswith("description:")), None)
    if start is None:
        return None
    end = start + 1
    while end < len(front) and not _FRONT_MATTER_KEY.match(front[end]):
        end += 1
    return start, end


def description_only(before: bytes, after: bytes) -> bool:
    """True when the two SKILL.md files differ only in the value of the front matter's ``description``."""
    try:
        old, new = before.decode("utf-8"), after.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if old == new:
        return False
    old_parts, new_parts = _front_matter(old), _front_matter(new)
    if old_parts is None or new_parts is None or old_parts[1] != new_parts[1]:
        return False
    old_span, new_span = _description_span(old_parts[0]), _description_span(new_parts[0])
    if old_span is None or new_span is None or old_span[0] != new_span[0]:
        return False
    old_front, new_front = old_parts[0], new_parts[0]
    return old_front[: old_span[0]] == new_front[: new_span[0]] and old_front[old_span[1] :] == new_front[new_span[1] :]
