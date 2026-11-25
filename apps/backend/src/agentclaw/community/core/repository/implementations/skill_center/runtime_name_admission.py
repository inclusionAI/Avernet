"""Compare selected Skill identities using the runtime mapping contract."""

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.skill_center.errors import SkillRuntimeNameConflictError
from agentclaw.community.kernel.skill_mapping_identity import mapping_source


def require_unique_runtime_names(
    session, scope, *, candidates: set[int], others: set[int]
) -> None:
    """Reject candidate collisions without blocking unrelated historical conflicts."""
    ids = candidates | others
    rows = scope(session.query(Skill), Skill).filter(Skill.id.in_(ids)).all()
    found_ids = {int(row.id) for row in rows}
    if not candidates <= found_ids:
        raise SkillRuntimeNameConflictError()
    candidate_names = {str(row.name or "") for row in rows if int(row.id) in candidates}
    duplicate_names = {
        name
        for name in candidate_names
        if sum(str(row.name or "") == name for row in rows) > 1
    }
    if not duplicate_names:
        return
    identities = {}
    for skill in rows:
        if str(skill.name or "") not in duplicate_names:
            continue
        locator = str(skill.git_path or "")
        version_number = None
        if locator.startswith("center://"):
            version = (
                scope(session.query(SkillVersion), SkillVersion)
                .filter(
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.status == "PUBLISHED",
                )
                .order_by(SkillVersion.version_ordinal.desc())
                .first()
            )
            if version is None or not skill.skill_uuid or not version.sc_version_number:
                raise SkillRuntimeNameConflictError()
            version_number = str(version.sc_version_number)
        try:
            _, _, identity = mapping_source(locator, skill.skill_uuid, version_number)
        except ValueError as exc:
            raise SkillRuntimeNameConflictError() from exc
        identities[int(skill.id)] = (str(skill.name or ""), identity)
    for skill_id in candidates:
        item = identities.get(skill_id)
        if item is None:
            continue
        for other_id, other in identities.items():
            if other_id != skill_id and item[0] == other[0] and item[1] != other[1]:
                raise SkillRuntimeNameConflictError()
