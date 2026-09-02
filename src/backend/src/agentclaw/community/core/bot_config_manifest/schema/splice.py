"""Rewrite a document's top-level ``script`` section, and nothing else (W8, D-10).

The legacy ``PUT …/startup-script`` on a bot that carries a manifest becomes a
write *through* the manifest: the submitted body replaces the document's
``script`` section, and the document is stored through the same validation as
any other write. W1 stores documents verbatim — the bytes are the meaning, and
a caller's comments and ordering are theirs — so this is a textual splice, not
a parse-and-dump: every byte outside the section is kept exactly as it was.

**Where the section is.** It starts at a line matching ``^script\\s*:`` at
column 0 and runs to the line before the next non-blank line that starts at
column 0 (a new top-level key, or a column-0 comment), or to the end of the
document. Blank lines trailing the section are left in place, so a document's
spacing between sections survives a rewrite.

**How the body is rendered.** As a YAML literal block: ``|`` for one trailing
newline, ``|-`` for none, with an indentation indicator when the first line
starts with a space. The helper then parses its own output and compares
``script.body`` with the body it was given; when the two differ (a CRLF body,
say — YAML folds line breaks) it renders a JSON-quoted scalar instead and
checks again. A body with more than one trailing newline goes straight to the
quoted form: ``|+`` would need trailing blank lines that are indistinguishable
from the document's own spacing between sections. A document that cannot be
parsed even before the splice is refused with the same error a ``PUT`` would
raise.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import yaml

from agentclaw.community.core.bot_config_manifest.schema.violations import (
    ManifestValidationError,
    Violation,
)

_HEADER_RE = re.compile(r"^script\s*:")
#: The document's own key for the section, and the key under it.
SECTION = "script"


def splice_script_section(document: str, body: Optional[str]) -> str:
    """Return ``document`` with its ``script`` section replaced, added or removed.

    ``body`` is the new script body; ``None`` removes the section. Every byte
    outside the section is unchanged. Raises ``ManifestValidationError`` when
    the document does not parse or the rendered section does not read back as
    the body given.
    """
    _require_parseable(document)
    lines = document.split("\n")
    span = _section_span(lines)

    if body is None:
        if span is None:
            return document
        start, end = span
        # The blank lines that separated the section from the next one go with
        # it: what is left is the spacing that preceded it, so two neighbours
        # end up as far apart as they were from the section.
        # (``split`` leaves a final empty element for the document's closing
        # newline; it is the newline, not a blank line, and stays.)
        while end < len(lines) - 1 and lines[end].strip() == "":
            end += 1
        return "\n".join(lines[:start] + lines[end:])

    for rendering in (_literal_block(body), _quoted_scalar(body)):
        candidate = _with_section(document, lines, span, rendering)
        if _reads_back(candidate, body):
            return candidate
    raise ManifestValidationError(
        [
            Violation(
                location="script.body",
                code="invalid_script",
                message="the script body could not be written into the document "
                "as YAML that reads back identically",
            )
        ]
    )


def _require_parseable(document: str) -> None:
    try:
        parsed = yaml.safe_load(document) if document.strip() else {}
    except yaml.YAMLError as exc:
        raise ManifestValidationError(
            [Violation(location="document", code="invalid_yaml", message=str(exc))]
        ) from None
    if parsed is not None and not isinstance(parsed, dict):
        raise ManifestValidationError(
            [
                Violation(
                    location="document",
                    code="invalid_document",
                    message="the document must be a YAML mapping",
                )
            ]
        )


def _section_span(lines: list[str]) -> Optional[tuple[int, int]]:
    """``(start, end)`` line indexes of the section, end exclusive; ``None`` if absent.

    ``end`` is just past the last non-blank line that belongs to the section,
    so the blank lines that follow stay where they were.
    """
    start = next((i for i, line in enumerate(lines) if _HEADER_RE.match(line)), None)
    if start is None:
        return None
    end = start + 1
    last_content = start
    while end < len(lines):
        line = lines[end]
        if line.strip() == "":
            end += 1
            continue
        if not line[0].isspace():
            break
        last_content = end
        end += 1
    return start, last_content + 1


def _with_section(
    document: str, lines: list[str], span: Optional[tuple[int, int]], rendering: list[str]
) -> str:
    if span is not None:
        start, end = span
        return "\n".join(lines[:start] + rendering + lines[end:])
    # Append: after the last byte, on its own line, ending in a newline like
    # every other section a hand-written document has.
    prefix = document if document.endswith("\n") or document == "" else document + "\n"
    return prefix + "\n".join(rendering) + "\n"


def _literal_block(body: str) -> list[str]:
    stripped = body.rstrip("\n")
    trailing = len(body) - len(stripped)
    if body == "" or trailing > 1:
        # Nothing a literal block renders faithfully: see the module docstring.
        return _quoted_scalar(body)
    chomping = "-" if trailing == 0 else ""
    content_lines = stripped.split("\n")
    indicator = "2" if content_lines[0][:1] == " " else ""
    rendered = [f"{SECTION}:", f"  body: |{indicator}{chomping}"]
    rendered.extend("    " + line if line else "" for line in content_lines)
    return rendered


def _quoted_scalar(body: str) -> list[str]:
    return [f"{SECTION}:", f"  body: {json.dumps(body)}"]


def _reads_back(candidate: str, body: str) -> bool:
    try:
        parsed: Any = yaml.safe_load(candidate)
    except yaml.YAMLError:
        return False
    if not isinstance(parsed, dict):
        return False
    section = parsed.get(SECTION)
    return isinstance(section, dict) and section.get("body") == body


__all__ = ["SECTION", "splice_script_section"]
