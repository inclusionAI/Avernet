"""Atomic cleanup of locked SkillSet rows and their owned associations."""


def delete_set_rows(session, repository, set_ids: list[int]) -> None:
    if not set_ids:
        return
    session.query(repository.SkillSetSkill).filter(
        repository.SkillSetSkill.skill_set_id.in_(set_ids)
    ).delete(synchronize_session=False)
    # Some legacy production databases predate the MCP membership table.
    # A savepoint keeps this compatibility case from invalidating the outer
    # transaction on databases where a failed statement aborts the transaction.
    try:
        with session.begin_nested():
            session.query(repository.SkillSetMCPServer).filter(
                repository.SkillSetMCPServer.skill_set_id.in_(set_ids)
            ).delete(synchronize_session=False)
    except Exception as exc:
        message = str(exc).lower()
        if "ac_skill_set_mcp" not in message or (
            "doesn't exist" not in message and "no such table" not in message
        ):
            raise
    session.query(repository.SkillSet).filter(
        repository.SkillSet.id.in_(set_ids)
    ).delete(synchronize_session=False)
