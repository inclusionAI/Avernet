"""Router-local catalog query dependency over the existing public ports."""

from typing import Any

from agentclaw.community.core.bot_inventory.protocols import BotInventoryTemplatePort
from agentclaw.community.core.repository.protocols.bot import BotRepository


def list_stage_bots(
    repository: BotRepository,
    templates: BotInventoryTemplatePort,
    **filters: Any,
) -> dict[str, Any]:
    """Keep filtering before pagination and preserve list template snapshots."""
    total, items = repository.list_by_conditions(**filters)
    ids = list(dict.fromkeys(row["bot_id"] for row in items if row.get("template_type")))
    configs = templates.list_template_configs_by_bot_ids(ids) if ids else {}
    for row in items:
        row["template_config"] = configs.get(row["bot_id"])
    return {"total": total, "items": items}
