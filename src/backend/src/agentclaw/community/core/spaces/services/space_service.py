"""Space lifecycle service."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.errors import (
    SpaceAlreadyExistsError,
    SpaceNameInvalidError,
    SpaceNotFoundError,
    SpaceScTeamBindingNotFoundError,
    SpaceScTeamRepairConflictError,
    SpaceScTeamRepairNotApplicableError,
)
from agentclaw.community.core.spaces.models import (
    PersonalSpaceLookupRecord,
    SpaceRecord,
    SpaceScTeamRepairResult,
    SpaceScTeamRepairStatus,
    SpaceSummaryRecord,
    SpaceType,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamQueryRequest,
)
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileLookupError,
)
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


# SC registers OCB Space ids under this stable external-source namespace.
# It is protocol identity, not caller input, so the repair endpoint must not allow
# clients to select another source and accidentally bind an unrelated SC Team.
_SC_SPACE_REF_SOURCE = "OCB"


class SpaceService:
    @inject
    def __init__(
        self,
        repository: SpaceRepositoryProtocol,
        skill_center_client: SkillCenterClient,
        staff_dept: StaffDeptPlugin,
    ) -> None:
        self._repository = repository
        self._skill_center_client = skill_center_client
        self._staff_dept = staff_dept

    def _get_creator_user_name(self, *, user_id: str) -> str | None:
        try:
            profile = self._staff_dept.get_profile_by_work_no(work_no=user_id)
        except StaffProfileLookupError:
            logger.warning(
                "staff profile lookup failed; creating space without creator name",
                extra={"user_id": user_id},
                exc_info=True,
            )
            return None
        if profile.nick_name is None:
            return None
        normalized = profile.nick_name.strip()
        return normalized[:128] or None

    def initialize_personal(self, *, user_id: str) -> tuple[SpaceRecord, bool]:
        env = get_current_env()
        existing = self._repository.get_personal_space(user_id=user_id, env=env)
        if existing is not None:
            return self._ensure_personal_sc_team_binding(existing, env=env), False

        creator_user_name = self._get_creator_user_name(user_id=user_id)
        try:
            with self._repository.create_personal_transaction(
                user_id=user_id, creator_user_name=creator_user_name, env=env
            ) as record:
                result = self._skill_center_client.create_team(
                    SkillCenterTeamCreateRequest(
                        team_code=record.space_code,
                        team_name=record.name,
                        ref_source_id=str(record.id),
                    )
                )
                record.sc_team_id = result.team_id
            return record, True
        except SpaceAlreadyExistsError:
            # A concurrent initializer may have committed the unique personal
            # Space first. Re-read it and ensure that its SC binding is complete.
            existing = self._repository.get_personal_space(user_id=user_id, env=env)
            if existing is None:
                raise
            return self._ensure_personal_sc_team_binding(existing, env=env), False

    def _ensure_personal_sc_team_binding(
        self, space: SpaceRecord, *, env: str
    ) -> SpaceRecord:
        if space.sc_team_id:
            return space

        # Lock the existing personal Space while resolving its external mapping,
        # so concurrent initialization requests cannot create duplicate SC Teams.
        with self._repository.personal_sc_team_binding_transaction(
            space_id=space.id, env=env
        ) as current:
            if current.sc_team_id:
                return current
            resolved = self._skill_center_client.get_team_by_ref_source(
                SkillCenterTeamQueryRequest(
                    source=_SC_SPACE_REF_SOURCE,
                    ref_source_id=str(current.id),
                )
            )
            if resolved is None:
                created = self._skill_center_client.create_team(
                    SkillCenterTeamCreateRequest(
                        team_code=current.space_code,
                        team_name=current.name,
                        ref_source_id=str(current.id),
                    )
                )
                current.sc_team_id = created.team_id
            else:
                current.sc_team_id = resolved.team_id
        return current

    def batch_query_personal(
        self, *, user_ids: list[str]
    ) -> list[PersonalSpaceLookupRecord]:
        normalized: list[str] = []
        seen: set[str] = set()
        for user_id in user_ids:
            value = user_id.strip()
            if not value:
                raise ValueError("user_id must not contain blank values")
            if value not in seen:
                seen.add(value)
                normalized.append(value)
        if not normalized:
            raise ValueError("user_id must not be empty")
        if len(normalized) > 500:
            raise ValueError("user_id must contain at most 500 unique values")
        return self._repository.batch_query_personal(
            user_ids=normalized, env=get_current_env()
        )

    def create_team(self, *, name: str, creator_id: str) -> SpaceRecord:
        normalized = name.strip()
        if not normalized or len(normalized) > 128:
            raise SpaceNameInvalidError("space name must contain 1-128 characters")
        creator_user_name = self._get_creator_user_name(user_id=creator_id)
        with self._repository.create_team_transaction(
            name=normalized,
            creator_id=creator_id,
            creator_user_name=creator_user_name,
            env=get_current_env(),
        ) as record:
            result = self._skill_center_client.create_team(
                SkillCenterTeamCreateRequest(
                    team_code=record.space_code,
                    team_name=record.name,
                    ref_source_id=str(record.id),
                )
            )
            record.sc_team_id = result.team_id
        return record

    def repair_sc_team_binding(self, *, space_id: int) -> SpaceScTeamRepairResult:
        """Repair one historical TEAM Space by lookup only; never create in SC.

        The operation is idempotent. Existing bindings are returned unchanged,
        and the repository performs a conditional update so concurrent requests
        cannot overwrite whichever binding was established first.
        """
        env = get_current_env()
        space = self._repository.get_space(space_id=space_id, env=env)
        if space is None:
            raise SpaceNotFoundError(f"space {space_id} not found")
        if space.space_type is not SpaceType.TEAM:
            raise SpaceScTeamRepairNotApplicableError(
                "SC Team binding repair applies only to TEAM spaces"
            )
        if space.sc_team_id:
            return SpaceScTeamRepairResult(
                space_id=space.id,
                status=SpaceScTeamRepairStatus.ALREADY_BOUND,
                sc_team_id=space.sc_team_id,
            )

        resolved = self._skill_center_client.get_team_by_ref_source(
            SkillCenterTeamQueryRequest(
                source=_SC_SPACE_REF_SOURCE,
                ref_source_id=str(space.id),
            )
        )
        if resolved is None:
            # Repair is intentionally lookup-only. Automatically creating here
            # could duplicate an SC Team whose external mapping is inconsistent.
            raise SpaceScTeamBindingNotFoundError(
                f"SC Team mapping for space {space.id} was not found"
            )

        if self._repository.backfill_sc_team_id(
            space_id=space.id, env=env, sc_team_id=resolved.team_id
        ):
            return SpaceScTeamRepairResult(
                space_id=space.id,
                status=SpaceScTeamRepairStatus.REPAIRED,
                sc_team_id=resolved.team_id,
            )

        # Another request may have won the conditional update. Re-read instead
        # of overwriting it or falsely claiming that this request repaired it.
        current = self._repository.get_space(space_id=space.id, env=env)
        if current is not None and current.sc_team_id:
            return SpaceScTeamRepairResult(
                space_id=current.id,
                status=SpaceScTeamRepairStatus.ALREADY_BOUND,
                sc_team_id=current.sc_team_id,
            )
        raise SpaceScTeamRepairConflictError(
            f"space {space.id} binding changed while repair was in progress"
        )

    def list_spaces(
        self,
        *,
        user_id: str,
        keyword: str | None,
        space_type: SpaceType | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[SpaceSummaryRecord]]:
        return self._repository.list_spaces(
            user_id=user_id,
            env=get_current_env(),
            keyword=keyword.strip() if keyword and keyword.strip() else None,
            space_type=space_type.value if space_type is not None else None,
            offset=(page_no - 1) * page_size,
            limit=page_size,
        )
