"""Application service for listing Skills owned by a Space."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.skill_center.space_skill_query_service_protocol import (
    SpaceSkillQueryServiceProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillReadRepository,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceSkillReadRecord,
)
from agentclaw.community.core.skill_center.space_skill_query_service_protocol import (
    SpaceSkillDetailRecord,
    SpaceSkillSummaryRecord,
)
from agentclaw.community.core.skill_center.draft_content import DraftRevisionRef
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.core.skill_center.services.space_skill_grant_service import (
    space_skill_actor_permissions,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileLookupError,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.work_no import normalize_work_no_for_lookup


logger = get_logger()


class SpaceSkillQueryService(SpaceSkillQueryServiceProtocol):
    """Authorize a Space member and delegate its Skill query to persistence."""

    @inject
    def __init__(
        self,
        access_service: SpaceAccessService,
        repository: SpaceSkillReadRepository,
        staff_dept: StaffDeptPlugin,
    ) -> None:
        self._access_service = access_service
        self._repository = repository
        self._staff_dept = staff_dept

    def list_space_skills(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[SpaceSkillSummaryRecord]]:
        space, member = self._access_service.require_space_member(
            space_id=space_id,
            user_id=actor_id,
        )
        normalized_keyword = keyword.strip() if keyword else None
        if not normalized_keyword:
            normalized_keyword = None
        total, records = self._repository.list_skills(
            space_id=space_id,
            actor_id=actor_id,
            env=get_current_env(),
            keyword=normalized_keyword,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        owner_display_names = self._resolve_owner_display_names(records)
        return total, [
            self._to_summary(
                record,
                actor_id=actor_id,
                space_type=space.space_type,
                space_role=member.role,
                owner_display_name=owner_display_names[record["owner_user_id"]],
            )
            for record in records
        ]

    def get_space_skill(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> SpaceSkillDetailRecord:
        space, member = self._access_service.require_space_member(
            space_id=space_id, user_id=actor_id
        )
        record = self._repository.get_skill(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=get_current_env(),
        )
        owner_display_name = self._resolve_owner_display_names([record])[
            record["owner_user_id"]
        ]
        summary = self._to_summary(
            record,
            actor_id=actor_id,
            space_type=space.space_type,
            space_role=member.role,
            owner_display_name=owner_display_name,
        )
        summary["source"] = record["source_type"]
        summary["offline_at"] = record["offline_at"]
        summary["offline_by"] = record["offline_by"]
        return summary

    def _to_summary(
        self,
        record: SpaceSkillReadRecord,
        *,
        actor_id: str,
        space_type,
        space_role,
        owner_display_name: str | None,
    ) -> SpaceSkillSummaryRecord:
        role = record["current_user_skill_role"]
        is_team = record["space_type"] == "TEAM"
        holder = record["lease_holder_user_id"]
        draft = None
        if record["draft_status"] is None:
            lease_summary = None
        else:
            ref = DraftRevisionRef.from_locator(
                tenant=get_current_avernet_tenant(),
                env=get_current_env(),
                locator=record["draft_locator"],
            )
            draft = {
                "target_version": record["draft_target_version"],
                "status": record["draft_status"],
                "revision_id": ref.revision_id,
                "name": record["name"],
                "description": record["draft_description"],
                "source_kind": record["draft_source_kind"],
                "source_repo_url": (
                    record["source_repo_url"]
                    if record["draft_source_kind"] == "GIT"
                    else None
                ),
                "source_branch": (
                    record["source_branch"]
                    if record["draft_source_kind"] == "GIT"
                    else None
                ),
                "source_commit_sha": (
                    record["source_commit_sha"]
                    if record["draft_source_kind"] == "GIT"
                    else None
                ),
                "source_subdir": (
                    record["source_subdir"]
                    if record["draft_source_kind"] == "GIT"
                    else None
                ),
            }
        if record["draft_status"] is not None and not is_team:
            lease_summary = {
                "required": False,
                "state": "NOT_REQUIRED",
                "holder_user_id": None,
                "holder_display_name": None,
            }
        elif record["draft_status"] is not None and holder is None:
            lease_summary = {
                "required": True,
                "state": "FREE",
                "holder_user_id": None,
                "holder_display_name": None,
            }
        elif record["draft_status"] is not None:
            lease_summary = {
                "required": True,
                "state": "HELD_BY_ME" if holder == actor_id else "HELD_BY_OTHER",
                "holder_user_id": holder,
                "holder_display_name": record["lease_holder_display_name"],
            }
        latest = None
        if record["latest_version_id"] is not None:
            latest = {
                "version": record["latest_version_ordinal"],
                "sc_version_number": record["latest_sc_version_number"],
                "published_at": record["latest_published_at"],
            }
        active_publication = None
        if record["active_attempt_id"] is not None:
            active_publication = {
                "attempt_id": str(record["active_attempt_id"]),
                "target_version": record["active_attempt_target_version"],
                "status": record["active_attempt_status"],
            }
        pending = None
        if record["pending_request_id"] is not None:
            pending = {
                "work_order_id": record["pending_request_id"],
                "work_order_no": record["pending_request_no"],
                "status": "PENDING",
            }
        lifecycle = (
            "OFFLINE"
            if record["offline_at"] is not None
            else "PUBLISHED"
            if latest is not None
            else "DRAFT_ONLY"
        )
        return {
            "id": record["id"],
            "skill_uuid": record["skill_uuid"],
            "name": record["name"],
            "description": (
                record["description"]
                if latest is not None
                else record["draft_description"]
            ),
            "lifecycle_status": lifecycle,
            "space_type": record["space_type"],
            "owner": {
                "user_id": record["owner_user_id"],
                "display_name": owner_display_name,
            },
            "latest_published_version": latest,
            "draft": draft,
            "active_publication": active_publication,
            "actor": {
                "skill_role": role,
                "permissions": space_skill_actor_permissions(
                    space_type=space_type,
                    space_role=space_role,
                    skill_role=role,
                ),
                "pending_editor_request": pending,
            },
            "lease_summary": lease_summary,
            "gmt_created": record["gmt_created"],
            "gmt_modified": record["gmt_modified"],
        }

    def _resolve_owner_display_names(
        self, records: list[SpaceSkillReadRecord]
    ) -> dict[str, str | None]:
        """Resolve the current display name once per Owner in one read response.

        ``ac_space_member.user_name`` is a historical snapshot and may be null.
        The workshop contract needs the current staff-directory display name. A
        profile outage must not make a read-only Skill listing unavailable, so
        the persisted value remains a best-effort fallback.
        """
        persisted_names = {
            record["owner_user_id"]: record["owner_display_name"] for record in records
        }
        resolved: dict[str, str | None] = {}
        for owner_id, persisted_name in persisted_names.items():
            try:
                profile = self._staff_dept.get_profile_by_work_no(
                    work_no=normalize_work_no_for_lookup(owner_id)
                )
            except StaffProfileLookupError:
                logger.warning(
                    "staff profile lookup failed; retaining stored Space Skill owner name",
                    extra={"owner_id": owner_id},
                    exc_info=True,
                )
                resolved[owner_id] = persisted_name
                continue
            display_name = (profile.nick_name or "").strip()
            resolved[owner_id] = display_name[:128] or persisted_name
        return resolved
