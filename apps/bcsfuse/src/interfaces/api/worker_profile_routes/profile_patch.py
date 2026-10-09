"""Worker/Profile compatibility routes: profile_patch."""

import logging
from datetime import datetime
from fastapi import Request, HTTPException, status
from src.interfaces.api.schemas.worker_management_schemas import (
    ProfilePatchRequest as ContractProfilePatchRequest,
    ProfileResponse,
)

from . import common

logger = logging.getLogger(__name__)


@common.mgmt_router.patch(
    "/workers/{worker_id}/profiles/{profile_id}",
    summary="Patch profile",
    description="Partial update of profile fields with merge/delete semantics.",
    response_model=ProfileResponse,
    tags=["Profiles"],
)
async def patch_profile(worker_id: str, profile_id: str, request: Request, req: ContractProfilePatchRequest):
    """
    P2: Patch profile with contract-aligned semantics.

    Merge semantics:
    - display_name, soul_md, agents_md, tools_md, boot_md, heartbeat_md: Replace if provided
    - contents: Merge (add/update keys, don't delete unprovided keys)
    - contents_delete: Delete specified keys from contents
    - skill_sets: Replace all if provided
    - metadata: Merge (add/update keys, don't delete unprovided keys)
    - metadata_delete: Delete specified keys from metadata
    - activate: Activate profile if True
    """
    common._require_auth(request)

    profile_store = common._get_profile_content_store(request)

    if profile_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_NOT_AVAILABLE",
                "message": "worker_profile_content_store provider not available"
            }
        )

    try:
        profile = profile_store.get_profile(worker_id, profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PROFILE_NOT_FOUND", "message": f"Profile {profile_id} not found for worker {worker_id}"}
            )

        # Get current profile data
        if isinstance(profile, dict):
            current_data = profile
        elif hasattr(profile, 'model_dump'):
            current_data = profile.model_dump()
        elif hasattr(profile, 'dict'):
            current_data = profile.dict()
        else:
            current_data = dict(profile)

        # Track updated fields
        updated_fields = []

        # Apply scalar field updates (replace semantics)
        if req.display_name is not None:
            current_data['display_name'] = req.display_name
            updated_fields.append('display_name')

        if req.soul_md is not None:
            current_data['soul_md'] = req.soul_md
            updated_fields.append('soul_md')

        if req.agents_md is not None:
            current_data['agents_md'] = req.agents_md
            updated_fields.append('agents_md')

        if req.tools_md is not None:
            current_data['tools_md'] = req.tools_md
            updated_fields.append('tools_md')

        if req.boot_md is not None:
            current_data['boot_md'] = req.boot_md
            updated_fields.append('boot_md')

        if req.heartbeat_md is not None:
            current_data['heartbeat_md'] = req.heartbeat_md
            updated_fields.append('heartbeat_md')

        # Apply contents merge
        contents_added_or_updated = []
        contents_deleted = []

        if req.contents is not None:
            if 'contents' not in current_data:
                current_data['contents'] = {}
            current_data['contents'].update(req.contents)
            contents_added_or_updated = list(req.contents.keys())
            updated_fields.append('contents')

        # Apply contents_delete
        if req.contents_delete is not None and len(req.contents_delete) > 0:
            if 'contents' not in current_data:
                current_data['contents'] = {}
            for key in req.contents_delete:
                if key in current_data['contents']:
                    del current_data['contents'][key]
                    contents_deleted.append(key)
            if contents_deleted:
                updated_fields.append('contents_delete')

        # Apply skill_sets (replace semantics)
        if req.skill_sets is not None:
            current_data['skill_sets'] = [s.model_dump() if hasattr(s, 'model_dump') else dict(s) for s in req.skill_sets]
            updated_fields.append('skill_sets')

        # Apply metadata merge
        metadata_added_or_updated = []
        metadata_deleted = []

        if req.metadata is not None:
            if 'metadata' not in current_data:
                current_data['metadata'] = {}
            current_data['metadata'].update(req.metadata)
            metadata_added_or_updated = list(req.metadata.keys())
            updated_fields.append('metadata')

        # Apply metadata_delete
        if req.metadata_delete is not None and len(req.metadata_delete) > 0:
            if 'metadata' not in current_data:
                current_data['metadata'] = {}
            for key in req.metadata_delete:
                if key in current_data['metadata']:
                    del current_data['metadata'][key]
                    metadata_deleted.append(key)
            if metadata_deleted:
                updated_fields.append('metadata_delete')

        # Update timestamp
        current_data['updated_at'] = datetime.utcnow().isoformat()

        # Update version
        version = current_data.get('version', 1) + 1
        current_data['version'] = version

        # Handle activation
        is_active = current_data.get('is_active', False)
        if req.activate:
            profile_store.activate_profile(worker_id, profile_id)
            is_active = True
            current_data['is_active'] = True
            updated_fields.append('activate')

            # Sync profile binding
            binding_store = common._get_profile_binding_store(request)
            if binding_store:
                profile_key = f"{worker_id}:{profile_id}"
                try:
                    from src.domain.models.worker_source_info import WorkerSourceType
                    binding_store.bind_profile(
                        worker_id=worker_id,
                        profile_key=profile_key,
                        source_type=WorkerSourceType.API,
                    )
                    logger.info(
                        f"[PROFILE_ACTIVATE_SIDE_EFFECT] worker_id={worker_id}, "
                        f"profile_id={profile_id}, profile_key={profile_key}, "
                        f"binding_updated=True, result=success"
                    )
                except Exception as e:
                    logger.warning(f"[PROFILE_ACTIVATE_SIDE_EFFECT] Binding failed: {e}")

            # Sync worker active_profile_key (aligned with internal profile_routes.py)
            profile_key = f"{worker_id}:{profile_id}"
            try:
                from src.interfaces.api.dependencies.worker_dependencies import _get_registry_store as _get_reg_store
                reg_store = _get_reg_store()
                worker_obj = reg_store.get_by_id(worker_id) if reg_store else None
                if worker_obj:
                    if isinstance(worker_obj, dict):
                        worker_obj['active_profile_key'] = profile_key
                        from src.domain.models.worker import Worker as WorkerModel
                        reg_store.update(WorkerModel.model_validate(worker_obj))
                    else:
                        if hasattr(worker_obj, 'model_dump'):
                            w_dict = worker_obj.model_dump()
                        elif hasattr(worker_obj, 'dict'):
                            w_dict = worker_obj.dict()
                        else:
                            w_dict = dict(worker_obj)
                        w_dict['active_profile_key'] = profile_key
                        from src.domain.models.worker import Worker as WorkerModel
                        reg_store.update(WorkerModel.model_validate(w_dict))
                    logger.info(
                        f"[PROFILE_ACTIVATE_SIDE_EFFECT] worker_id={worker_id}, "
                        f"active_profile_key={profile_key}, updated=True"
                    )
            except Exception as e:
                logger.warning(f"[PROFILE_ACTIVATE_SIDE_EFFECT] Failed to update active_profile_key: {e}")

        logger.info(
            f"[PROFILE_PATCH_MERGE] worker_id={worker_id}, profile_id={profile_id}, "
            f"contents_added_or_updated={contents_added_or_updated}, "
            f"contents_deleted={contents_deleted}, "
            f"metadata_added_or_updated={metadata_added_or_updated}, "
            f"metadata_deleted={metadata_deleted}, "
            f"skill_sets_replaced={len(req.skill_sets) if req.skill_sets is not None else 'N/A'}"
        )

        # Save updated profile
        profile_store.upsert_profile(worker_id, profile_id, current_data)

        # Keep vectors consistent with profile writes. The previous internal
        # Profile service rebuilt whenever embedding-relevant content changed;
        # gating this on ENABLE_EAGER_INDEXING could leave ECB summary updates
        # searchable only after a later full rebuild.
        vector_fields = {
            'soul_md',
            'agents_md',
            'tools_md',
            'boot_md',
            'heartbeat_md',
            'contents',
            'contents_delete',
            'skill_sets',
        }
        if vector_fields.intersection(updated_fields):
            import time
            start = time.time()
            try:
                from src.interfaces.api.dependencies.fusion_dependencies import (
                    _build_vector_index_for_worker,
                )

                success = _build_vector_index_for_worker(worker_id)
                elapsed_ms = int((time.time() - start) * 1000)
                logger.info(
                    f"[PROFILE_INDEX_RESULT] worker_id={worker_id}, "
                    f"profile_id={profile_id}, result={'success' if success else 'skipped'}, "
                    f"elapsed_ms={elapsed_ms}"
                )
            except Exception as e:
                logger.warning(f"[PROFILE_INDEX_RESULT] Failed: {e}")
            try:
                from src.interfaces.api.profile_routes import _trigger_index_sync

                _trigger_index_sync(worker_id)
            except Exception as e:
                logger.warning(f"[PROFILE_INDEX_CACHE_RESET] Failed: {e}")

        # Return flat ProfileResponse
        return ProfileResponse(
            worker_id=worker_id,
            profile_id=profile_id,
            display_name=current_data.get('display_name'),
            soul_md=current_data.get('soul_md'),
            agents_md=current_data.get('agents_md'),
            tools_md=current_data.get('tools_md'),
            boot_md=current_data.get('boot_md'),
            heartbeat_md=current_data.get('heartbeat_md'),
            contents=current_data.get('contents', {}),
            skill_sets=current_data.get('skill_sets', []),
            metadata=current_data.get('metadata', {}),
            content_type=current_data.get('content_type', 'api'),
            is_active=is_active,
            version=version,
            created_at=current_data.get('created_at'),
            updated_at=current_data.get('updated_at'),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Profiles R3] Failed to patch profile {profile_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PATCH_PROFILE_ERROR", "message": str(e)}
        )
