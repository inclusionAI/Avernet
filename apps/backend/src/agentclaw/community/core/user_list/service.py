"""Application service for user-list checks and authorized corrections."""

from __future__ import annotations

from injector import inject

from agentclaw.community.plugin_api.staff_dept import DeptLookupError, StaffDeptPlugin
from agentclaw.community.core.user_list.rules import (
    ACTUAL_BOT_TYPES, department_path_candidates, validate_membership,
)

from agentclaw.community.core.repository.protocols.identity import UserListRepositoryProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.core.user_list.user_list_service_protocol import UserListServiceProtocol


logger = get_logger()


class UserListService(UserListServiceProtocol):
    """Expose only boolean membership, never the underlying list data."""

    @inject
    def __init__(self, repository: UserListRepositoryProtocol, staff_dept: StaffDeptPlugin) -> None:
        self._repository = repository
        self._staff_dept = staff_dept

    def is_in_user_list(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        env: str | None = None,
    ) -> bool:
        return self._repository.exists(
            entity_id=entity_id,
            user_list_type=user_list_type,
            env=env,
        )

    def correct_membership(
        self,
        *,
        actor_id: str,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
        subject_type: str = "user",
        bot_type: str = "all",
        env: str | None = None,
    ) -> bool:
        entity_id = validate_membership(entity_id, subject_type, bot_type)
        self._repository.set_membership(
            entity_id=entity_id,
            user_list_type=user_list_type,
            in_whitelist=in_whitelist,
            subject_type=subject_type,
            bot_type=bot_type,
            env=env,
        )
        logger.info(
            "user_list_membership_corrected actor_id=%s entity_id=%s "
            "user_list_type=%s env=%s in_whitelist=%s subject_type=%s bot_type=%s",
            actor_id,
            entity_id,
            user_list_type,
            env or "runtime",
            in_whitelist,
            subject_type,
            bot_type,
        )
        return in_whitelist


    def matches_user(
        self, *, entity_id: str, user_list_type: str, env: str, bot_type: str,
    ) -> bool:
        # COSEC: all is a rule scope, never a trusted actual Bot type.
        if bot_type not in ACTUAL_BOT_TYPES:
            raise ValueError("Actual Bot type must be service or personal")
        scope = dict(user_list_type=user_list_type, env=env, bot_types=(bot_type, "all"))
        source = "none"
        reason = "not_configured"
        paths: tuple[str, ...] = ()
        department = None
        stage = "user_list_query"
        try:
            if self._repository.exists_any(entity_ids=(entity_id,), subject_type="user", **scope):
                source, reason = "user", "matched"
                return True
            if not self._repository.has_rules(subject_type="department", **scope):
                return False
            stage = "directory_lookup"
            department = self._staff_dept.get_dept_by_work_no(work_no=entity_id)
            if not department.dept_path:
                reason = "no_dept"
                return False
            try:
                paths = department_path_candidates(department.dept_path)
            except ValueError:
                reason = "invalid_path"
                return False
            stage = "department_list_query"
            matched = self._repository.exists_any(entity_ids=paths, subject_type="department", **scope)
            source, reason = ("department", "matched") if matched else ("none", "not_matched")
            return matched
        except DeptLookupError:
            reason = "directory_error"
            return False
        except Exception:
            reason = "directory_error" if stage == "directory_lookup" else "db_error"
            raise
        finally:
            logger.info(
                "user_list.match_decision entity_id=%s user_list_type=%s env=%s bot_type=%s "
                "matched_source=%s reason=%s department_paths=%s dept_no=%s dept_name=%s dept_path=%s",
                entity_id, user_list_type, env, bot_type, source, reason, paths,
                department.dept_no if department else None,
                department.dept_name if department else None,
                department.dept_path if department else None,
            )


__all__ = ["UserListService"]
