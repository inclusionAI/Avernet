"""Legacy Space writers depend on the transaction contract, never knowledge SQL."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


CORE = Path(__file__).resolve().parents[3] / "src/agentclaw/community/core"
WRITERS = (
    "repository/implementations/spaces/space.py",
    "repository/implementations/spaces/management.py",
    "repository/implementations/work_orders/work_order.py",
    "repository/implementations/work_orders/auto_approval.py",
    "repository/implementations/skill_center/space_skill.py",
)
# This is a finite, deliberate seam, not a generic repository dependency rule.
FORBIDDEN = (
    "agentclaw.community.core.space_knowledge",
    "agentclaw.community.core.repository.implementations.spaces.knowledge_sync",
    "agentclaw.community.core.repository.implementations.spaces.identity",
    "agentclaw.community.core.repository.implementations.spaces.knowledge_effects",
    "agentclaw.community.core.repository.implementations.spaces.persistence_effects",
)


def violations(source, package):
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            imports.append(module)
            imports.extend(f"{module}.{alias.name}" for alias in node.names)
    return [
        name
        for name in imports
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN)
    ]


@pytest.mark.parametrize("relative", WRITERS)
def test_legacy_writer_has_no_direct_knowledge_dependency(relative):
    path = CORE / relative
    assert path.is_file(), f"writer disappeared; update the declared boundary: {path}"
    package = "agentclaw.community.core." + ".".join(Path(relative).parts[:-1])
    assert violations(path.read_text(), package) == []


@pytest.mark.parametrize(
    "source",
    [
        "from agentclaw.community.core.repository.implementations.spaces import knowledge_sync as ks",
        "import agentclaw.community.core.space_knowledge.repository.models as m",
        "from .identity import reserve_new_space_identity as reserve",
        "from . import knowledge_effects",
    ],
)
def test_guard_detects_alias_and_relative_bypasses(source):
    assert violations(
        source, "agentclaw.community.core.repository.implementations.spaces"
    )


@pytest.mark.parametrize("relative", ["persistence_effects.py", "source_lock.py"])
def test_default_effect_and_source_lock_have_no_knowledge_imports(relative):
    path = CORE / "repository/implementations/spaces" / relative
    banned = violations(
        path.read_text(), "agentclaw.community.core.repository.implementations.spaces"
    )
    assert banned == []
