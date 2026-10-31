"""Mechanical plumbing register for the workspace density roster.

The register discloses the control-flow, fixture, guard, narration and
cleanup lines of the two frozen upstream shells that the density roster
deliberately does NOT bill: they carry no assertion of their own, or their
assertion face already surfaces through a charged content leaf.  The register
lives outside every denominator (63 atoms and 59 parsed obligations), so no
upstream line is ever billed twice.  The census is frozen in
``frozen.PLUMBING_COUNTS`` and any drift fails closed.
"""
from __future__ import annotations

import re
from typing import Any

from ..run_state import RunStateError
from .frozen import (
    GROUP_SCOPED_FIRST,
    GROUP_SCOPED_LAST,
    GROUP_SOURCE_FILE,
    STORIES_SOURCE_FILE,
    STORY_LINE_END,
    STORY_LINE_START,
    TAIL_LINE_END,
    TAIL_LINE_START,
)


def scan_plumbing(group_lines: list[str], stories_lines: list[str],
                  group_spans: dict[str, tuple[int, int]],
                  stories_spans: dict[str, tuple[int, int]],
                  corrections: list[str]) -> list[dict[str, Any]]:
    """Return the 44-entry plumbing register, mechanically scanned."""
    entries: list[dict[str, Any]] = []

    def add(entry_class: str, source_file: str, line_start: int, line_end: int,
            quote: str, reason: str) -> None:
        entries.append({
            "class": entry_class,
            "source_file": source_file,
            "line_start": line_start,
            "line_end": line_end,
            "quote": quote,
            "reason": reason,
            "charged_to_roster": False,
        })

    scopes = (
        (group_lines, GROUP_SOURCE_FILE, GROUP_SCOPED_FIRST, GROUP_SCOPED_LAST),
        (stories_lines, STORIES_SOURCE_FILE, STORY_LINE_START, STORY_LINE_END),
        (stories_lines, STORIES_SOURCE_FILE, TAIL_LINE_START, TAIL_LINE_END),
    )
    for lines, source_file, low, high in scopes:
        for number in range(low, high + 1):
            if re.match(r'^\s{4}info "', lines[number - 1]):
                add("narration_info", source_file, number, number, lines[number - 1],
                    "story narration line; carries no assertion of its own")
    for number in range(GROUP_SCOPED_FIRST, GROUP_SCOPED_LAST + 1):
        line = group_lines[number - 1]
        if re.search(r"ensure_cli_token PM \|\| \{ skip_case", line):
            add("cli_token_skip_guard", GROUP_SOURCE_FILE, number, number, line,
                "CLI token bootstrap skip guard; the case outcome is carried by the charged leaves")
        if re.search(r'\[\[ -z "\$gid" \]\]', line):
            add("empty_group_id_guard", GROUP_SOURCE_FILE, number, number, line,
                "fixture guard for an empty setup group id; not a semantic assertion")
        if re.search(r'bcs_cli PM get-group --id "\$gid" >/dev/null 2>&1 \|\| true', line):
            add("implicit_cli_get_read", GROUP_SOURCE_FILE, number, number, line,
                "implicit CLI read-back whose captured output feeds the charged round-trip "
                "content leaves; it is the read face of those leaves, not an extra obligation")
        if re.search(r'api_delete "/groups/\$gid" >/dev/null 2>&1', line):
            add("best_effort_cleanup_api_delete", GROUP_SOURCE_FILE, number, number, line,
                "best-effort fixture cleanup deletion (also the error-path cleanup form); "
                "never a business assertion")
    for helper in ("_cli_create_group", "_cli_delete_group"):
        span = group_spans.get(helper)
        if span is None or not (GROUP_SCOPED_FIRST <= span[0] <= span[1] <= GROUP_SCOPED_LAST):
            raise RunStateError(f"workspace density CLI helper span moved: {helper}")
        add("cli_helper_definition", GROUP_SOURCE_FILE, span[0], span[1],
            "\n".join(group_lines[span[0] - 1:span[1]]),
            "CLI helper definition envelope (identifier extraction / best-effort deletion); "
            "its behavior surfaces only through charged content leaves")
    guard_line = None
    for number in range(GROUP_SCOPED_FIRST, GROUP_SCOPED_LAST + 1):
        if re.search(r'^\s{4}if \[ "\$HTTP_STATUS" = "401" \]; then$', group_lines[number - 1]):
            guard_line = number
            break
    if guard_line is None:
        raise RunStateError("workspace density label 401 guard moved out of group.sh")
    fi_line = None
    for number in range(guard_line + 1, guard_line + 12):
        if group_lines[number - 1] == "    fi":
            fi_line = number
            break
    if fi_line is None:
        raise RunStateError("workspace density label 401 guard never closes")
    add("deprecated_label_401_guard", GROUP_SOURCE_FILE, guard_line, fi_line,
        "\n".join(group_lines[guard_line - 1:fi_line]),
        "dual-form guard for the deprecated label mutation face; S07 correction applies "
        f"verbatim: \"{corrections[0]}\" The skipped branch is disclosed here and not billed; "
        "the charged content leaf requires the authorized 200 form")
    for number in range(STORY_LINE_START, TAIL_LINE_END + 1):
        line = stories_lines[number - 1]
        if re.match(r'^\s{4}(?:_story_group_metadata_without_workspace \|\| return|'
                    r'\[\[ -n "\$group_id" \]\] \|\| return)$', line):
            if number <= STORY_LINE_END:
                reason = ("story delegation line to the metadata tail function; the six tail "
                          "parents anchor here and their leaves carry the in-function anchors")
            else:
                reason = ("early-return guard after the incident group id fixture; "
                          "not an additional assertion")
            add("story_tail_bind", STORIES_SOURCE_FILE, number, number, line, reason)
    return entries
