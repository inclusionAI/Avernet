"""Unit tests for teclaw engine-relative path mapping (``to_engine_relative``).

Every teclaw caller passes a namespace-relative logical path, so the mapper is a
strict normalizer (no host-path stripping).
"""
import pytest

from agentclaw.community.core.config_compose.teclaw_paths import (
    CONFIG_NS,
    IDENTITY_NS,
    TECLAW_ENGINE_CONFIG_FILE,
    WORKSPACE_NS,
    to_engine_relative,
    to_local_skill_engine_path,
)

pytestmark = pytest.mark.unit


class TestNamespaceRelativePaths:
    def test_workspace_file(self):
        assert to_engine_relative(f"{WORKSPACE_NS}/sub/x.pdf") == "/workspace/sub/x.pdf"

    def test_identity_file(self):
        assert to_engine_relative(f"{IDENTITY_NS}/AGENTS.md") == "/identity/AGENTS.md"

    def test_bare_namespace(self):
        assert to_engine_relative("workspace") == "/workspace"
        assert to_engine_relative("identity") == "/identity"

    def test_config_engine_config_file(self):
        # teclaw owns its engine config at /config/teclaw.json (read/written
        # per-file through the engine), addressed namespace-relative.
        assert (
            to_engine_relative(f"{CONFIG_NS}/{TECLAW_ENGINE_CONFIG_FILE}")
            == "/config/teclaw.json"
        )

    def test_collapses_redundant_slashes(self):
        assert to_engine_relative("identity//MEMORY.md/") == "/identity/MEMORY.md"

    def test_skills_subtree(self):
        assert (
            to_engine_relative("workspace/skills/skills-local/my-skill/SKILL.md")
            == "/workspace/skills/skills-local/my-skill/SKILL.md"
        )


class TestLocalSkillEnginePath:
    """teclaw local skills are flat under the workspace namespace."""

    def test_prepends_workspace_namespace(self):
        assert (
            to_local_skill_engine_path("skills-local/my-skill/SKILL.md")
            == "workspace/skills-local/my-skill/SKILL.md"
        )

    def test_bare_skill_dir(self):
        assert to_local_skill_engine_path("skills-local/my-skill") == "workspace/skills-local/my-skill"

    def test_nested_relative_path(self):
        assert (
            to_local_skill_engine_path("skills-local/my-skill/lib/util.py")
            == "workspace/skills-local/my-skill/lib/util.py"
        )

    def test_collapses_slashes_and_dot_segments(self):
        assert (
            to_local_skill_engine_path("./skills-local//my-skill/")
            == "workspace/skills-local/my-skill"
        )

    def test_idempotent_when_already_namespaced(self):
        assert (
            to_local_skill_engine_path("workspace/skills-local/my-skill")
            == "workspace/skills-local/my-skill"
        )

    def test_output_is_accepted_by_to_engine_relative(self):
        # The adapter output feeds the TeclawDeviceFileSystem mapper.
        ns_rel = to_local_skill_engine_path("skills-local/my-skill/SKILL.md")
        assert to_engine_relative(ns_rel) == "/workspace/skills-local/my-skill/SKILL.md"


class TestRejectsNonNamespacePaths:
    def test_absolute_host_path_raises(self):
        # A host path must never reach the engine seam — callers pass namespace-relative.
        with pytest.raises(ValueError):
            to_engine_relative("/aidesktop/aidesktop_pre/bolt_data/staff_1/b/teclaw/workspace/x")

    def test_unknown_namespace_raises(self):
        with pytest.raises(ValueError):
            to_engine_relative("data/foo.csv")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            to_engine_relative("")
