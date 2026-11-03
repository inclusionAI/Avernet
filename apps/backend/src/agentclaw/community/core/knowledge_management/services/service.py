"""Compose provider facts with bounded, same-space workspace display data. No write access."""

from agentclaw.community.core.knowledge_management.ports import (
    KnowledgeCatalog,
    KnowledgeDisplaySource,
    KnowledgeEnablementReader,
)
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from agentclaw.community.plugin_api.space_knowledge import (
    SpaceKnowledgeError,
    SpaceKnowledgeSync,
)


def require_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(ord(c) < 32 or c in "/\\" for c in value)
    ):
        raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")


def page_size(limit: int, cursor: int = 0) -> None:
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(cursor) is not int
        or not 0 <= cursor <= 2**63 - 1
    ):
        raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")


class KnowledgeManagementService:
    def __init__(
        self,
        source: KnowledgeDisplaySource,
        catalog: KnowledgeCatalog,
        enablement: KnowledgeEnablementReader,
        env: str,
        sync: SpaceKnowledgeSync,
    ):
        self.source, self.catalog, self.enablement, self.env = (
            source,
            catalog,
            enablement,
            env,
        )
        self.sync = sync

    def _scope(self, space_id, actor_id):
        if type(space_id) is not int or not 0 < space_id <= 2**63 - 1:
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        require_id(actor_id)
        return self.source.scope(space_id, actor_id)

    @staticmethod
    def _fact(item, space_id, kb_id=None):
        if not isinstance(item, dict) or item.get("space_id") != str(space_id):
            raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        try:
            require_id(item.get("kb_id"))
        except KnowledgeManagementError:
            raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID") from None
        if (
            (kb_id is not None and item["kb_id"] != kb_id)
            or not isinstance(item.get("name"), str)
            or type(item.get("can_manage")) is not bool
            or item.get("kgbase_status") not in {"ready", "creating", "failed"}
        ):
            raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        for key in ("document_count", "authorized_bot_count"):
            if key in item and (type(item[key]) is not int or item[key] < 0):
                raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        for key in ("description", "created_at", "updated_at", "updated_at_basis"):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        # These are provider-owned facts, including null clocks. Do not synthesize a
        # count from this page or turn created_at into an updated_at fallback.
        return dict(item)

    def list_in_space(self, *, space_id, actor_id, cursor="", limit=20):
        page_size(limit)
        if cursor:
            require_id(cursor)
        space = self._scope(space_id, actor_id)
        try:
            context = self.sync.get_context(space_id=space_id, environment=self.env)
        except SpaceKnowledgeError as exc:
            raise KnowledgeManagementError(exc.code) from None
        if context is None:
            return {"items": [], "knowledge_bases": [], "next_cursor": None}
        if (
            context.space_id != str(space_id)
            or context.environment != self.env
            or type(context.revision) is not int
            or context.revision < 1
        ):
            raise KnowledgeManagementError("KNOWLEDGE_SCOPE_MISMATCH")
        payload = self.catalog.list_in_space(
            space_id=space_id, actor_id=actor_id, cursor=cursor, limit=limit
        )
        items = payload.get("items", payload.get("knowledge_bases"))
        if not isinstance(items, list) or len(items) > limit:
            raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        facts = [
            self._fact(item, space_id) | {"space_name": space["name"]} for item in items
        ]
        if len({item["kb_id"] for item in facts}) != len(facts):
            raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        next_cursor = payload.get("next_cursor")
        if next_cursor is not None:
            try:
                require_id(next_cursor)
            except KnowledgeManagementError:
                raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID") from None
        # Both existing naming conventions survive; neither drops provider fields.
        return {
            **payload,
            "items": facts,
            "knowledge_bases": facts,
            "next_cursor": next_cursor,
        }

    def detail(self, *, space_id, kb_id, actor_id):
        require_id(kb_id)
        space = self._scope(space_id, actor_id)
        fact = self._fact(
            self.catalog.detail(space_id=space_id, kb_id=kb_id, actor_id=actor_id),
            space_id,
            kb_id,
        )
        return fact | {"space_name": space["name"]}

    def authorize_space(self, *, space_id, actor_id):
        self._scope(space_id, actor_id)

    def candidates(self, *, space_id, kb_id, actor_id, cursor=0, limit=20, keyword=""):
        page_size(limit, cursor)
        if not isinstance(keyword, str) or len(keyword) > 128:
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        # Native KB MANAGE is required before exposing any candidate directory.
        detail = self.detail(space_id=space_id, kb_id=kb_id, actor_id=actor_id)
        if not detail["can_manage"]:
            raise KnowledgeManagementError("KNOWLEDGE_MANAGE_REQUIRED")
        page = self.source.candidates(
            space_id, cursor=cursor, limit=limit, keyword=keyword
        )
        return {
            **page,
            "items": [
                {**item, "space_id": str(space_id), "space_name": detail["space_name"]}
                for item in page["items"]
            ],
        }

    def authorized_bots(self, *, space_id, kb_id, actor_id, cursor=0, limit=20):
        page_size(limit, cursor)
        require_id(kb_id)
        # Provider-side native READ still gates this read: the authorized-bots
        # endpoint rejects actors without KB_READ before any grant row is
        # returned, so no catalog-detail preflight is made here. There is
        # deliberately no per-Bot editor check either (nor a write-side call).
        space = self._scope(space_id, actor_id)
        payload = self.catalog.authorized_bots(
            space_id=space_id,
            kb_id=kb_id,
            actor_id=actor_id,
            cursor=cursor,
            limit=limit,
        )
        grants = payload.get("items")
        next_cursor = payload.get("next_cursor")
        if (
            not isinstance(grants, list)
            or len(grants) > limit
            or (
                next_cursor is not None
                and (
                    type(next_cursor) is not int
                    or not cursor < next_cursor <= 2**63 - 1
                )
            )
        ):
            raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
        identities = []
        for grant in grants:
            if not isinstance(grant, dict):
                raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
            identity = (grant.get("bot_id"), grant.get("bot_owner_id"))
            try:
                for value in identity:
                    require_id(value)
            except KnowledgeManagementError:
                raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID") from None
            if (
                grant.get("kb_id") != kb_id
                or grant.get("platform") != self.source.platform
                or grant.get("platform_env") != self.env
                or grant.get("granted") is not True
                or grant.get("user_id") != identity[1]
                or identity in identities
                or not isinstance(grant.get("id"), (int, str))
                or type(grant.get("id")) is bool
                or not grant["id"]
                or type(grant.get("revision")) is not int
                or grant["revision"] < 1
                or "updated_by" not in grant
                or (
                    grant["updated_by"] is not None
                    and not isinstance(grant["updated_by"], str)
                )
            ):
                raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
            identities.append(identity)
        # Read only the current grant page, not a whole-space/all-user directory.
        bots = self.source.display(space_id, identities) if identities else {}
        enabled = (
            self.enablement.read(space_id=space_id, kb_id=kb_id, bots=bots)
            if bots
            else {}
        )
        items = []
        for grant, identity in zip(grants, identities, strict=True):
            bot = bots.get(identity)
            if bot is not None and type(enabled.get(identity)) is not bool:
                raise KnowledgeManagementError("KNOWLEDGE_ENABLEMENT_UNAVAILABLE")
            display = (
                {
                    "bot_name": None,
                    "avatar_url": None,
                    "owner_id": identity[1],
                    "owner_name": None,
                    "bot_type": None,
                }
                if bot is None
                else {k: v for k, v in bot.items() if k != "bot_pk"}
            )
            items.append(
                {
                    **grant,
                    **display,
                    "space_id": str(space_id),
                    "space_name": space["name"],
                    "bot_available": bot is not None,
                    "metadata_missing": bot is None,
                    "enabled": enabled.get(identity),
                }
            )
        return {**payload, "items": items, "next_cursor": next_cursor}

    def grant_states(self, *, space_id, kb_id, actor_id, identities):
        if not 1 <= len(identities) <= 20 or len(set(identities)) != len(identities):
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        for identity in identities:
            for value in identity:
                require_id(value)
        detail = self.detail(space_id=space_id, kb_id=kb_id, actor_id=actor_id)
        if not detail["can_manage"]:
            raise KnowledgeManagementError("KNOWLEDGE_MANAGE_REQUIRED")
        bots = self.source.display(space_id, identities)
        if any(identity not in bots for identity in identities):
            raise KnowledgeManagementError("KNOWLEDGE_BOT_NOT_IN_SPACE")
        return self.catalog.grant_states(
            kb_id=kb_id, actor_id=actor_id, identities=identities, env=self.env
        )

    def set_grant(
        self, *, space_id, kb_id, actor_id, bot_id, owner_id, granted, expected_revision
    ):
        for value in (kb_id, bot_id, owner_id):
            require_id(value)
        detail = self.detail(space_id=space_id, kb_id=kb_id, actor_id=actor_id)
        if not detail["can_manage"]:
            raise KnowledgeManagementError("KNOWLEDGE_MANAGE_REQUIRED")
        identity = (bot_id, owner_id)
        display = self.source.display(space_id, [identity])
        if granted:
            if identity not in display:
                raise KnowledgeManagementError("KNOWLEDGE_BOT_NOT_IN_SPACE")
        elif identity not in display:
            # A revoked grant remains an provider fact even after the local Bot row is
            # soft-deleted. Verify that fact through provider, then let the same CAS
            # write path revoke it; do not require display metadata to survive.
            states = self.catalog.grant_states(
                kb_id=kb_id, actor_id=actor_id, identities=[identity], env=self.env
            )
            items = states.get("items") if isinstance(states, dict) else None
            if (
                not isinstance(items, list)
                or len(items) != 1
                or not isinstance(items[0], dict)
                or items[0].get("bot_id") != bot_id
                or items[0].get("owner_id") != owner_id
                or type(items[0].get("revision")) is not int
                or items[0]["revision"] < 1
            ):
                raise KnowledgeManagementError("KNOWLEDGE_BOT_NOT_IN_SPACE")
        return self.catalog.set_grant(
            kb_id=kb_id,
            actor_id=actor_id,
            bot_id=bot_id,
            owner_id=owner_id,
            granted=granted,
            expected_revision=expected_revision,
        )

    def available_for_bot(self, *, scope, actor_id, cursor=0, limit=20):
        page_size(limit, cursor)
        if limit > 20:
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        bot = self.source.bot_context(scope, actor_id)
        space_id = bot["space_id"]
        page = self.catalog.page_authorized(
            scope=scope, space_id=space_id, cursor=cursor, limit=limit
        )
        selected = page["kb_ids"]
        saved = self.source.selections(bot=bot, kb_ids=selected)
        # Only fetch this page's metadata, not every authorized KB on every request.
        space = self._scope(space_id, actor_id)
        facts = self.catalog.details(
            space_id=space_id, kb_ids=selected, actor_id=actor_id
        )
        if not isinstance(facts, list) or len(facts) != len(selected):
            raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        items = []
        for kb_id, value in zip(selected, facts, strict=True):
            fact = self._fact(value, space_id, kb_id)
            items.append(
                {
                    **fact,
                    "space_name": space["name"],
                    "bot_id": scope.bot_id,
                    "owner_id": scope.owner_id,
                    **saved.get(kb_id, {"enabled": False, "revision": 0}),
                }
            )
        return {
            "items": items,
            "next_cursor": page["next_cursor"],
        }
