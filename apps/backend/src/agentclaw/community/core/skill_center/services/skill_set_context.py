"""Shared layout constants and paths for Legacy SkillSet services."""

from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    pass
from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,  # noqa: E402
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.log import get_logger

SKILLS_DIR = Path("/home/admin/.openclaw/workspace/skills")
SKILLS_REPO_DIR = SKILLS_DIR / "skills-repo"
SKILLS_LOCAL_DIR = SKILLS_DIR / "skills-local"
logger = get_logger()
_T = TypeVar("_T")


def _get_bot_paths(
    path_factory: WorkspacePathFactory,
    user_id: str | None = None,
    entity_id: str | None = None,
    bot_id: str | None = None,
    engine_type: str | None = None,
    entity_type: str | None = None,
    is_desktop: bool = False,
):
    """Get bot paths using new directory structure.

    Priority:
    1. If entity_id, bot_id, engine_type provided: use them directly
    2. If user_id provided: construct entity_id from user_id (staff_{user_id})
    3. Otherwise: use defaults (staff_default/default/moltis)

    Args:
        user_id: User ID (used to construct entity_id if entity_id not provided)
        entity_id: Entity ID (e.g., staff_xxx, proj_xxx, team_xxx or pure id)
        bot_id: Bot ID (default: "default")
        engine_type: Engine type (default: "moltis")
        entity_type: Entity type (e.g., staff, proj, team), used when entity_id is pure id


        is_desktop: True when ``ac_bots.bot_type == "desktop"`` — selects the
            agentbox engine-view path. Defaults to False so non-desktop bots
            (personal/service) keep the cloud OSS-view path.

    Returns:
        Tuple of (skills_dir, repo_dir, local_dir)
    """
    factory = path_factory

    # Priority 1: Use provided entity_id/bot_id/engine_type
    if entity_id and bot_id and engine_type:
        # Extract entity_type and pure entity_id from entity_id format
        if entity_id.startswith("staff_"):
            effective_entity_type = "staff"
            effective_entity_id = entity_id[6:]  # Remove "staff_" prefix
        elif entity_id.startswith("proj_"):
            effective_entity_type = "proj"
            effective_entity_id = entity_id[5:]  # Remove "proj_" prefix
        elif entity_id.startswith("team_"):
            effective_entity_type = "team"
            effective_entity_id = entity_id[5:]  # Remove "team_" prefix
        else:
            # Pure entity_id, use provided entity_type or default to "staff"
            effective_entity_type = entity_type if entity_type else "staff"
            effective_entity_id = entity_id
        effective_bot_id = bot_id
        effective_engine = engine_type
    # Priority 2: Construct from user_id
    elif user_id:
        effective_entity_type = "staff"
        effective_entity_id = user_id
        effective_bot_id = "default"
        effective_engine = DEFAULT_ENGINE_TYPE
    # Priority 3: Use defaults
    else:
        effective_entity_type = "staff"
        effective_entity_id = "default"
        effective_bot_id = "default"
        effective_engine = DEFAULT_ENGINE_TYPE

    # Use new bolt directory methods with separate entity_type and entity_id
    skills_dir = factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )

    local_dir = factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )
    repo_dir = factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )

    return skills_dir, repo_dir, local_dir


logger = get_logger()
