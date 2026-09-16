"""The default-skillset skill must name the directory the engine actually uses.

`cli_tools` ships without `PATH` injection (spec D-10), so the *only* thing
telling a model where its tools are is the `cli-tools-*` skill in that engine's
default skillset. If an engine moves its constant and the skill keeps the old
path, nothing fails — no import breaks, no request 500s — the agent simply
stops finding tools that are installed correctly. This test is what makes the
two move together.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.community.engines.claude_code.engine import CLAUDE_CODE_CLI_DIR
from engine.community.engines.openclaw.engine import OPENCLAW_CLI_DIR


def _skills_root() -> Path:
    """`src/engine/skills`, found by walking up to the distribution root."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "skills"
        if (parent / "pyproject.toml").is_file() and candidate.is_dir():
            return candidate
    raise AssertionError("src/engine/skills not found above this test")


#: (skill folder, the engine constant it has to agree with, the other engine's).
SKILL_DIRS = [
    ("cli-tools-openclaw", OPENCLAW_CLI_DIR, CLAUDE_CODE_CLI_DIR),
    ("cli-tools-aicoding", CLAUDE_CODE_CLI_DIR, OPENCLAW_CLI_DIR),
]


@pytest.mark.parametrize("folder,own_dir,other_dir", SKILL_DIRS)
def test_skill_names_its_own_engines_directory(
    folder: str, own_dir: Path, other_dir: Path
) -> None:
    text = (_skills_root() / folder / "SKILL.md").read_text(encoding="utf-8")

    assert str(own_dir) in text
    # Not the neighbour's tree: one skill per engine is the whole point, and a
    # copy-paste that leaves the other path in is the way that breaks.
    assert str(other_dir) not in text


@pytest.mark.parametrize("folder,own_dir,_other", SKILL_DIRS)
def test_the_path_is_in_the_description_too(
    folder: str, own_dir: Path, _other: Path
) -> None:
    """Skill selection reads the description; the body arrives later."""
    frontmatter = (
        (_skills_root() / folder / "SKILL.md")
        .read_text(encoding="utf-8")
        .split("---", 2)[1]
    )

    assert str(own_dir) in frontmatter
