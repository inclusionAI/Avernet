"""The protected workspace entries: one list, and the names other modules place at the root must be on it."""

from agentclaw.community.core.config_compose.teclaw_paths import LOCAL_SKILLS_DIRNAME
from agentclaw.community.core.services.identity import VALID_IDENTITY_FILES
from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES
from agentclaw.community.core.workspace.protected_entries import (
    IDENTITY_FILE_NAMES,
    PROTECTED_ROOT_DIRS,
    PROTECTED_ROOT_FILES,
    is_protected_root_entry,
    protected_entry_refusal,
)


def test_the_directories_other_owners_write_at_the_root_are_protected() -> None:
    """teclaw keeps a bot's local skills flat at ``/workspace/skills-local/``; a resources apply must not
    clear them with the rest of the root, so the skills construct's three trees and the engine's own
    directories are all on the list."""
    assert LOCAL_SKILLS_DIRNAME == "skills-local"
    assert {"skills", "skills-pool", LOCAL_SKILLS_DIRNAME, "state", "conf"} <= PROTECTED_ROOT_DIRS
    assert {f"{engine}_conf" for engine in SUPPORTED_ENGINE_TYPES} <= PROTECTED_ROOT_DIRS
    assert is_protected_root_entry(LOCAL_SKILLS_DIRNAME)
    assert protected_entry_refusal("skills-local/tidy/SKILL.md") == (
        "resource path 'skills-local/tidy/SKILL.md' is under 'skills-local', which is not a resources directory"
    )
    assert protected_entry_refusal("docs/skills-local/notes.md") is None


def test_the_identity_vocabulary_is_defined_once() -> None:
    assert PROTECTED_ROOT_FILES == IDENTITY_FILE_NAMES
    assert VALID_IDENTITY_FILES == set(IDENTITY_FILE_NAMES)
    assert is_protected_root_entry("CLAUDE.md")
    assert not is_protected_root_entry("docs")
