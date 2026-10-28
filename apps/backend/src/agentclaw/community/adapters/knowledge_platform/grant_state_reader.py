"""Bounded compatibility read of canonical grants, including revoked rows."""

from time import monotonic
from urllib.parse import quote

from agentclaw.community.kernel.knowledge import require_knowledge_id
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError


def read_grant_states(catalog, *, kb_id, actor_id, identities, env):
    actor = catalog.identities.staff_no(actor_id)
    owners = dict.fromkeys(owner for _, owner in identities)
    owners = {owner: catalog.identities.staff_no(owner) for owner in owners}
    wanted = {(bot, owners[owner]) for bot, owner in identities}
    found, cursor, deadline = {}, 0, monotonic() + 8
    for _ in range(5):
        page = catalog._read(
            f"/api/v1/knowledge/kb/{quote(kb_id, safe='')}/bot-authorizations",
            {"actor_staff_no": actor, "cursor": cursor, "limit": 100},
            deadline=deadline,
        )
        items, next_cursor = page.get("items"), page.get("next_cursor")
        if (
            not isinstance(items, list)
            or len(items) > 100
            or "next_cursor" not in page
            or (
                next_cursor is not None
                and (
                    type(next_cursor) is not int
                    or not cursor < next_cursor <= 2**63 - 1
                )
            )
        ):
            raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
        for row in items:
            if not isinstance(row, dict) or row.get("kb_id") != kb_id:
                raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
            try:
                for key in ("bot_id", "user_id", "platform", "platform_env"):
                    require_knowledge_id(row.get(key), key)
            except (ValueError, TypeError):
                raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID") from None
            if (
                type(row.get("granted")) is not bool
                or type(row.get("revision")) is not int
                or row["revision"] < 1
            ):
                raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
            identity = (row["bot_id"], row["user_id"])
            if (
                row["platform"] == catalog.platform
                and row["platform_env"] == env
                and identity in wanted
            ):
                if identity in found:
                    raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
                found[identity] = row
        if next_cursor is None or len(found) == len(wanted):
            return {
                "items": [
                    {
                        "bot_id": bot,
                        "owner_id": owner,
                        "revision": found.get((bot, owners[owner]), {}).get(
                            "revision", 0
                        ),
                        "granted": found.get((bot, owners[owner]), {}).get(
                            "granted", False
                        ),
                    }
                    for bot, owner in identities
                ]
            }
        cursor = next_cursor
    # Unknown is not revision zero. Never probe state with a grant write.
    raise KnowledgeManagementError("KNOWLEDGE_GRANT_LOOKUP_LIMIT")
