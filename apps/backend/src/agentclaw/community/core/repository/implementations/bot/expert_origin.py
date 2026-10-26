"""Historical expert creation facts owned by the Bot persistence boundary."""

from sqlalchemy import case, func

from agentclaw.community.core.repository.json_expression import JsonText
from agentclaw.community.plugin_api.models import BotModel


def expert_creation_counts(db, *, tenant: str, env: str):
    """One count per stable persisted Bot, including soft-deleted assets.

    Returns a relational subquery so the catalogue can order the complete
    matching set before pagination without fetching all Bots into memory.
    Invalid legacy ext values represent no expert provenance.
    """
    safe_ext = case((func.json_valid(BotModel.ext) == 1, BotModel.ext), else_="{}")
    expert_id = JsonText(safe_ext, "$.expert_origin.expert_id")
    repo_id = JsonText(safe_ext, "$.expert_origin.source_repo_id")
    return (
        db.query(
            expert_id.label("expert_id"),
            repo_id.label("repo_id"),
            func.count(func.distinct(BotModel.id)).label("creation_count"),
        )
        .filter(BotModel.avernet_tenant == tenant, BotModel.env == env)
        .group_by(expert_id, repo_id)
        .subquery()
    )
