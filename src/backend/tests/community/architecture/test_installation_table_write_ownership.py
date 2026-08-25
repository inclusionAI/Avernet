"""The Installation tables are written only from ``tables/``, via the UoW.

``ac_bot_skill_installation`` / ``ac_bot_mcp_installation`` are the single
source of truth for a Bot's active capabilities. A second writer is how the
two-authorities defect returns: a row written or deleted outside the UoW's
transaction is silently undone by the next flush or command. So:

  1. **Write ownership.** Only ``core/repository/implementations/
     skill_center/tables/`` may construct or delete rows of the two
     Installation models. Everything else may read them.
  2. **Reach.** The ``tables`` modules are imported only by the UoW
     composition modules — no service or adapter calls them directly, so
     every write runs under a UoW transaction.

Detection is AST-only. A *write* is a ``Call`` constructing the model
(``BotSkillInstallation(...)`` / ``BotMCPInstallation(...)``) or a
``.delete(...)`` call whose expression chain mentions either model — the
``session.query(Model)...delete()`` shape.

**Adding an allowlist entry is a review-level decision.**
"""

from __future__ import annotations

import ast
import pathlib

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"

_MODELS = {"BotSkillInstallation", "BotMCPInstallation"}

#: The table-command package — the writes' one home.
_TABLES_DIR = (
    "community/core/repository/implementations/skill_center/tables"
)

#: Files that may keep a write, each with a justification.
_WRITE_ALLOWLIST: dict[str, str] = {
    "community/core/models/skill.py": "Model definition module.",
    "community/core/models/mcp.py": "Model definition module.",
    "community/core/repository/implementations/skill_center/skill.py": (
        "exclude_default_set_skill still deletes the Installation row with "
        "its exclusion write. Its only caller is the G.16-dead legacy "
        "SkillSetService.remove_skill_from_set; the UoW exclusion commands "
        "replace the flow and the cleanup group deletes both, removing this "
        "entry (specs/2026-08-24-installation-single-source-of-truth)."
    ),
}

#: Modules allowed to import the ``tables`` command modules — the UoW and
#: the command mixins it composes.
_TABLES_IMPORTER_ALLOWLIST = {
    "community/core/repository/implementations/skill_center/capability_desired_state.py",
    "community/core/repository/implementations/skill_center/bot_skillset_installations.py",
    "community/core/repository/implementations/skill_center/default_exclusion_commands.py",
    "community/core/repository/implementations/skill_center/direct_installation_commands.py",
    "community/core/repository/implementations/skill_center/mcp_skill_set_control_plane.py",
}


def _python_files():
    for path in sorted(_AGENTCLAW_ROOT.rglob("*.py")):
        yield path, str(path.relative_to(_AGENTCLAW_ROOT)).replace("\\", "/")


def _mentions_model(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id in _MODELS
        for child in ast.walk(node)
    )


def _writes(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name) and callee.id in _MODELS:
            found.append(f"line {node.lineno}: constructs {callee.id}")
        elif isinstance(callee, ast.Attribute) and callee.attr in _MODELS:
            found.append(f"line {node.lineno}: constructs {callee.attr}")
        elif (
            isinstance(callee, ast.Attribute)
            and callee.attr == "delete"
            and _mentions_model(callee.value)
        ):
            found.append(f"line {node.lineno}: deletes Installation rows")
    return found


def test_installation_tables_are_written_only_from_the_tables_modules():
    violations: list[str] = []
    for path, relative in _python_files():
        if relative.startswith(_TABLES_DIR):
            continue
        if relative in _WRITE_ALLOWLIST:
            continue
        source = path.read_text(encoding="utf-8")
        if not any(model in source for model in _MODELS):
            continue
        for write in _writes(ast.parse(source)):
            violations.append(f"{relative}: {write}")
    assert not violations, (
        "Installation-table writes outside tables/ (route them through the "
        "UoW):\n" + "\n".join(violations)
    )


def test_the_tables_modules_are_reached_only_through_the_uow():
    violations: list[str] = []
    for path, relative in _python_files():
        if relative.startswith(_TABLES_DIR):
            continue
        if relative in _TABLES_IMPORTER_ALLOWLIST:
            continue
        source = path.read_text(encoding="utf-8")
        if "skill_center.tables" in source or "skill_center import tables" in source:
            violations.append(relative)
    assert not violations, (
        "tables/ command modules imported outside the UoW:\n"
        + "\n".join(violations)
    )


def test_the_write_allowlist_only_names_files_that_still_write():
    """A stale entry hides a future writer behind a dead justification."""
    for relative, justification in _WRITE_ALLOWLIST.items():
        path = _AGENTCLAW_ROOT / relative
        assert path.exists(), f"allowlisted file is gone: {relative}"
        if "Model definition" in justification:
            continue
        assert _writes(ast.parse(path.read_text(encoding="utf-8"))), (
            f"allowlist entry no longer writes — delete it: {relative}"
        )
