"""Contract test: every get_bot_skills_{local,repo}_dir call in skill_center
router modules MUST pass is_desktop= kwarg, else non-desktop bots silently
fall through to the cloud branch and desktop bots silently fall through
WITHOUT is_desktop=True. Both cases were broken — see
docs/superpowers/plans/2026-05-19-fix-skills-path-device-provider-propagation.md.

Suppress a specific line with:
    # allow-missing-is-desktop: <one-line reason>
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROUTER_FILES = [
    Path(__file__).parents[3] / "src/agentclaw/community/adapters/http/skill_center/skills.py",
    Path(__file__).parents[3] / "src/agentclaw/community/adapters/http/skill_center/skillsets.py",
]

TARGET_METHODS = {"get_bot_skills_local_dir", "get_bot_skills_repo_dir"}

SUPPRESSION_MARKER = "# allow-missing-is-desktop"


def _violations_in(file_path: Path) -> list[tuple[int, str]]:
    source = file_path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(file_path))
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in TARGET_METHODS:
            continue
        has_kw = any(kw.arg == "is_desktop" for kw in node.keywords)
        if has_kw:
            continue
        line_idx = node.lineno - 1
        line_text = source_lines[line_idx] if 0 <= line_idx < len(source_lines) else ""
        prev_text = source_lines[line_idx - 1] if line_idx > 0 else ""
        if SUPPRESSION_MARKER in line_text or SUPPRESSION_MARKER in prev_text:
            continue
        violations.append((node.lineno, line_text.strip()))
    return violations


@pytest.mark.parametrize("file_path", ROUTER_FILES, ids=lambda p: p.name)
def test_router_path_factory_calls_pass_is_desktop(file_path: Path) -> None:
    violations = _violations_in(file_path)
    if violations:
        rendered = "\n".join(f"  {file_path.name}:{ln}  {src}" for ln, src in violations)
        pytest.fail(
            f"{len(violations)} call(s) to get_bot_skills_{{local,repo}}_dir omit "
            f"is_desktop= kwarg in {file_path.name}:\n{rendered}\n\n"
            "Fix: pass is_desktop= explicitly (use the 5th tuple element from "
            "_get_path_params). To intentionally allow, add "
            f"`{SUPPRESSION_MARKER}: <reason>` on the same or preceding line."
        )
