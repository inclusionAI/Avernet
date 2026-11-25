"""Legacy skillsets default routes."""

from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    InitAndSyncResponse,
    SetDefaultSkillsFastRequest,
    SetDefaultSkillsFastResponse,
    SetDefaultSkillsRequest,
    SetDefaultSkillsResponse,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from fastapi import Depends, Query

from .skillsets_common import (
    _get_path_params,
    logger,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skillsets", tags=["skillsets"])

@router.get("/admin/init-and-sync", response_model=InitAndSyncResponse)
async def init_and_sync_default_skills(
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> InitAndSyncResponse:
    """Admin interface: Initialize default skill set and sync all Git skills into it.

    This endpoint will:
    1. Create default skill set if not exists
    2. Scan all skills in skills-repo
    3. Add all git skills to default skill set
    """
    # Get effective path parameters
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )
    path_factory.get_bot_engine_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )

    set_service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=runtime_engine,
    )

    # Step 1: Ensure default skill set exists
    skill_set = set_service.ensure_default_skill_set(user_id=None)
    skill_set_id = skill_set.get("id")

    # Add type field for response
    skill_set["type"] = "default"

    # Step 2: Scan all git skills
    sync_result = {
        "scanned": 0,
        "created": 0,
        "added": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    try:
        # Get all skills from git repo
        git_skills = skill_service.get_skills_in_path("")
        sync_result["scanned"] = len(git_skills)

        # Get existing skills in default set
        existing_skills = set_service.get_set_skills(skill_set_id, user_id=None)
        existing_skill_ids = {str(s.get("id")) for s in existing_skills if s.get("id")}

        # Step 3: Process each git skill
        skill_ids_to_add = []
        for gs in git_skills:
            try:
                skill_id = gs.get("id")
                full_path = gs.get("full_path", "")

                if not skill_id or not full_path:
                    continue

                # Check if skill exists in database
                skill = skill_service.get_skill_by_path(f"git://{skill_id}")

                if not skill:
                    # Create skill from market data
                    skill = set_service._create_skill_from_market(gs, user_id=None)
                    if skill:
                        sync_result["created"] += 1

                if skill:
                    skill_db_id = str(skill.get("id"))
                    if skill_db_id in existing_skill_ids:
                        sync_result["skipped"] += 1
                    else:
                        skill_ids_to_add.append(skill_db_id)

            except Exception as e:
                sync_result["failed"] += 1
                sync_result["errors"].append(
                    f"Failed to process {gs.get('id', 'unknown')}: {str(e)}"
                )

        # Step 4: Add skills to default set (bypass user restriction)
        if skill_ids_to_add:
            try:
                for skill_id in skill_ids_to_add:
                    try:
                        # Check association exists
                        existing = set_service.skill_set_repo.get_skills_in_set(
                            skill_set_id
                        )
                        if not any(str(s.get("id")) == skill_id for s in existing):
                            set_service.skill_set_repo.add_skill_to_set(
                                skill_set_id, skill_id
                            )
                            sync_result["added"] += 1
                    except Exception as e:
                        sync_result["failed"] += 1
                        sync_result["errors"].append(
                            f"Failed to add skill {skill_id}: {str(e)}"
                        )

            except Exception as e:
                sync_result["errors"].append(f"Batch add failed: {str(e)}")

    except Exception as e:
        sync_result["errors"].append(f"Sync failed: {str(e)}")

    return InitAndSyncResponse(
        success=True,
        skill_set={
            "id": str(skill_set_id),
            "name": skill_set.get("name"),
            "type": "default",
        },
        sync=sync_result,
    )


@router.post("/admin/set-default-skills", response_model=SetDefaultSkillsResponse)
async def set_default_skills(
    request: SetDefaultSkillsRequest,
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> SetDefaultSkillsResponse:
    """Admin interface: Set specific skills as the default skill set content.

    This endpoint will:
    1. Create default skill set if not exists
    2. Clear existing skills in default set
    3. Add specified skills to default set
    4. Sync skills to skills-default directory
    """
    import shutil

    from agentclaw.community.core.workspace.path_factory import (
        get_global_skills_default_dir,
        get_global_skills_repo_dir,
    )

    # Get effective path parameters
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )
    path_factory.get_bot_engine_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )

    set_service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=runtime_engine,
    )

    # Step 1: Ensure default skill set exists
    skill_set = set_service.ensure_default_skill_set(user_id=None)
    skill_set_id = skill_set.get("id")

    # Add type field for response
    skill_set["type"] = "default"

    # Step 2: Get existing skills in default set
    existing_skills = set_service.get_set_skills(skill_set_id, user_id=None)
    existing_skill_ids = {str(s.get("id")) for s in existing_skills if s.get("id")}

    sync_result = {
        "requested": len(request.skill_ids),
        "added": 0,
        "removed": 0,
        "skipped": 0,
        "not_found": 0,
        "copied": 0,
        "deleted": 0,
        "errors": [],
    }

    # Step 3: Remove skills not in the new list
    skill_ids_to_keep = set(request.skill_ids)
    for existing in existing_skills:
        existing_id = str(existing.get("id"))
        if existing_id not in skill_ids_to_keep:
            try:
                set_service.skill_set_repo.remove_skill_from_set(
                    skill_set_id, existing_id
                )
                sync_result["removed"] += 1
            except Exception as e:
                sync_result["errors"].append(
                    f"Failed to remove skill {existing_id}: {str(e)}"
                )

    # Step 4: Add new skills and collect skill info for directory sync
    skills_to_sync = []  # [(skill_name, git_path), ...]

    # Pre-load market skills for lookup
    market_skills = skill_service.get_skills_in_path("")
    market_skills_map = {}
    for ms in market_skills:
        # Build lookup map by link_name and git_path
        link_name = ms.get("link_name", "")
        ms.get("full_path", "")
        ms_id = ms.get("id", "")
        if link_name:
            market_skills_map[link_name] = ms
        if ms_id:
            market_skills_map[ms_id] = ms

    for skill_id in request.skill_ids:
        try:
            # Check if skill exists (try by ID -> link_name -> git_path -> market)
            skill = None

            # 1. Try by numeric ID
            if skill_id.isdigit():
                try:
                    skill = skill_service.get_skill(skill_id, user_id=None)
                except (ValueError, AttributeError):
                    pass

            # 2. Try by link_name (e.g., infra_demo_odps-sql-generator)
            if not skill:
                skill = skill_service.get_skill_by_link_name(skill_id)

            # 3. Try by git_path (fallback)
            if not skill:
                skill = skill_service.get_skill_by_path(f"git://{skill_id}")
                if not skill:
                    all_skills = skill_service.list_skills()
                    for s in all_skills:
                        git_path = s.get("git_path", "")
                        if (
                            git_path.endswith(f"/{skill_id}")
                            or git_path == f"git://{skill_id}"
                        ):
                            skill = s
                            break

            # 4. Try from market (skills-repo) and create if not found in DB
            if not skill:
                market_skill = market_skills_map.get(skill_id)
                if market_skill:
                    # Create skill record from market data
                    skill = set_service._create_skill_from_market(
                        market_skill, user_id=None
                    )
                    if skill:
                        logger.info(
                            f"[set_default_skills] Created skill from market: {skill_id} -> {skill.get('id')}"
                        )

            if not skill:
                sync_result["not_found"] += 1
                sync_result["errors"].append(f"Skill not found: {skill_id}")
                continue

            skill_db_id = str(skill.get("id"))
            git_path = skill.get("git_path", "")
            skill_name = skill.get("name", "")

            # Check if already in set
            if skill_db_id in existing_skill_ids:
                sync_result["skipped"] += 1
                # Still add to sync list
                if git_path:
                    skills_to_sync.append((skill_name, git_path))
                continue

            # Add to set
            set_service.skill_set_repo.add_skill_to_set(skill_set_id, skill_db_id)
            sync_result["added"] += 1

            # Add to sync list
            if git_path:
                skills_to_sync.append((skill_name, git_path))

        except Exception as e:
            sync_result["errors"].append(f"Failed to add skill {skill_id}: {str(e)}")

    # Step 5: Sync skills to skills-default directory
    try:
        # Get paths
        skills_repo_path = get_global_skills_repo_dir()
        skills_default_path = get_global_skills_default_dir()

        logger.info(
            f"[set_default_skills] Syncing to skills-default: {skills_default_path}"
        )
        logger.info(f"[set_default_skills] Skills repo: {skills_repo_path}")

        # Ensure skills-default directory exists
        skills_default_path.mkdir(parents=True, exist_ok=True)

        # Get list of skill names to keep
        skill_names_to_keep = set()
        for skill_name, git_path in skills_to_sync:
            skill_names_to_keep.add(skill_name)

        # Remove skills not in the list
        if skills_default_path.exists():
            for item in skills_default_path.iterdir():
                if item.is_dir() and item.name not in skill_names_to_keep:
                    try:
                        shutil.rmtree(item)
                        sync_result["deleted"] += 1
                        logger.info(f"[set_default_skills] Deleted: {item.name}")
                    except Exception as e:
                        sync_result["errors"].append(
                            f"Failed to delete {item.name}: {str(e)}"
                        )

        # Copy skills to skills-default
        for skill_name, git_path in skills_to_sync:
            if git_path.startswith("git://"):
                rel_path = git_path[6:]  # Remove 'git://' prefix
                src_dir = skills_repo_path / rel_path
                dst_dir = skills_default_path / skill_name

                if src_dir.exists():
                    try:
                        # Remove existing if exists
                        if dst_dir.exists():
                            shutil.rmtree(dst_dir)
                        # Copy the skill directory
                        shutil.copytree(src_dir, dst_dir)
                        sync_result["copied"] += 1
                        logger.info(f"[set_default_skills] Copied: {skill_name}")
                    except Exception as e:
                        sync_result["errors"].append(
                            f"Failed to copy {skill_name}: {str(e)}"
                        )
                else:
                    sync_result["errors"].append(f"Source skill not found: {rel_path}")

    except Exception as e:
        logger.error(f"[set_default_skills] Failed to sync to skills-default: {e}")
        sync_result["errors"].append(f"Directory sync failed: {str(e)}")

    return SetDefaultSkillsResponse(
        success=True,
        skill_set={
            "id": str(skill_set_id),
            "name": skill_set.get("name"),
            "type": "default",
        },
        sync=sync_result,
    )


@router.post(
    "/admin/set-default-skills-fast", response_model=SetDefaultSkillsFastResponse
)
async def set_default_skills_fast(
    request: SetDefaultSkillsFastRequest,
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(None, description="引擎类型 (默认: moltis)"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> SetDefaultSkillsFastResponse:
    """Admin interface: Set specific skills as the default skill set content (FAST VERSION).

    This endpoint will:
    1. Create default skill set if not exists
    2. Clear existing skills in default set
    3. Add specified skills to default set (DB only, no file operations)

    Note: File sync to skills-default directory is no longer needed.
    Skill symlinks are generated dynamically from DB via get_symlink_mappings().
    """
    # Get effective path parameters
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
    )

    set_service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=runtime_engine,
    )

    # Step 1: Ensure default skill set exists
    skill_set = set_service.ensure_default_skill_set(user_id=None)
    skill_set_id = skill_set.get("id")

    # Add type field for response
    skill_set["type"] = "default"

    # Step 2: Get existing skills in default set
    existing_skills = set_service.get_set_skills(skill_set_id, user_id=None)
    existing_skill_ids = {str(s.get("id")) for s in existing_skills if s.get("id")}

    sync_result = {
        "requested": len(request.skill_ids),
        "added": 0,
        "removed": 0,
        "skipped": 0,
        "not_found": 0,
        "copied": 0,  # 保留字段，但不再使用
        "deleted": 0,  # 保留字段，但不再使用
        "errors": [],
    }

    # Step 3: Remove skills not in the new list
    skill_ids_to_keep = set(request.skill_ids)
    for existing in existing_skills:
        existing_id = str(existing.get("id"))
        if existing_id not in skill_ids_to_keep:
            try:
                set_service.skill_set_repo.remove_skill_from_set(
                    skill_set_id, existing_id
                )
                sync_result["removed"] += 1
            except Exception as e:
                sync_result["errors"].append(
                    f"Failed to remove skill {existing_id}: {str(e)}"
                )

    # Step 4: Add new skills (DB only)
    # Pre-load market skills for lookup
    market_skills = skill_service.get_skills_in_path("")
    market_skills_map = {}
    for ms in market_skills:
        link_name = ms.get("link_name", "")
        ms_id = ms.get("id", "")
        if link_name:
            market_skills_map[link_name] = ms
        if ms_id:
            market_skills_map[ms_id] = ms

    for skill_id in request.skill_ids:
        try:
            # Check if skill exists (try by ID -> link_name -> market)
            skill = None

            # 1. Try by numeric ID
            if skill_id.isdigit():
                try:
                    skill = skill_service.get_skill(skill_id, user_id=None)
                except (ValueError, AttributeError):
                    pass

            # 2. Try by link_name
            if not skill:
                skill = skill_service.get_skill_by_link_name(skill_id)

            # 3. Try from market and create if not found in DB
            if not skill:
                market_skill = market_skills_map.get(skill_id)
                if market_skill:
                    skill = set_service._create_skill_from_market(
                        market_skill, user_id=None
                    )
                    if skill:
                        logger.info(
                            f"[set_default_skills_fast] Created skill from market: {skill_id} -> {skill.get('id')}"
                        )

            if not skill:
                sync_result["not_found"] += 1
                sync_result["errors"].append(f"Skill not found: {skill_id}")
                continue

            skill_db_id = str(skill.get("id"))

            # Check if already in set
            if skill_db_id in existing_skill_ids:
                sync_result["skipped"] += 1
                continue

            # Add to set
            set_service.skill_set_repo.add_skill_to_set(skill_set_id, skill_db_id)
            sync_result["added"] += 1

        except Exception as e:
            sync_result["errors"].append(f"Failed to add skill {skill_id}: {str(e)}")

    # Step 5: 文件同步已移除
    # 软链通过 get_symlink_mappings() 从 DB 动态生成，不再需要复制文件到 skills-default 目录
    logger.info(
        "[set_default_skills_fast] DB update completed, skipping file sync "
        "(symlinks generated dynamically)"
    )

    return SetDefaultSkillsFastResponse(
        success=True,
        skill_set={
            "id": str(skill_set_id),
            "name": skill_set.get("name"),
            "type": "default",
        },
        sync=sync_result,
    )
