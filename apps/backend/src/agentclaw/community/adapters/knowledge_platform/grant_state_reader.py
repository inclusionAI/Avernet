"""One exact, ordered CAS-state batch; absence is explicit, never inferred."""

from time import monotonic
from urllib.parse import quote

from agentclaw.community.kernel.knowledge import require_knowledge_id
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError


def read_grant_states(catalog, *, kb_id, actor_id, identities, env):
    if not 1 <= len(identities) <= 20 or len(set(identities)) != len(identities):
        raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
    actor = catalog.identities.staff_no(actor_id)
    owners = {
        owner: catalog.identities.staff_no(owner)
        for owner in dict.fromkeys(owner for _, owner in identities)
    }
    targets = [(bot, owners[owner]) for bot, owner in identities]
    if len(set(targets)) != len(targets):
        raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
    payload = catalog._read(
        f"/api/v1/knowledge/kb/{quote(kb_id, safe='')}/bot-authorization-states",
        {},
        method="POST",
        body={
            "actor_staff_no": actor,
            "platform": catalog.platform,
            "targets": [{"bot_id": bot, "user_id": owner} for bot, owner in targets],
        },
        deadline=monotonic() + 8,
    )
    items = payload.get("items")
    if (
        payload.get("kb_id") != kb_id
        or payload.get("platform") != catalog.platform
        or payload.get("platform_env") != env
        or not isinstance(items, list)
        or len(items) != len(targets)
    ):
        raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
    result = []
    for (bot, owner), (_, mapped), row in zip(identities, targets, items, strict=True):
        if (
            not isinstance(row, dict)
            or row.get("bot_id") != bot
            or row.get("user_id") != mapped
            or type(row.get("granted")) is not bool
            or type(row.get("revision")) is not int
            or not 0 <= row["revision"] <= 2**63 - 1
            or (row["granted"] and row["revision"] == 0)
        ):
            raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
        try:
            require_knowledge_id(row["bot_id"], "bot_id")
            require_knowledge_id(row["user_id"], "user_id")
        except ValueError:
            raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID") from None
        result.append(
            {
                "bot_id": bot,
                "owner_id": owner,
                "granted": row["granted"],
                "revision": row["revision"],
            }
        )
    return {"items": result}
