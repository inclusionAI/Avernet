"""Read existing public rows only; no schema, ACL mirror, cache or directory RPC."""

import json

from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from sqlalchemy import and_, or_, tuple_


class KnowledgeSourceRepository:
    def __init__(self, db: DatabasePlugin, env: str, platform: str):
        self.db, self.env, self.platform = db, env, platform

    def scope(self, space_id: int, actor_id: str) -> dict:
        if not self.platform or get_current_avernet_tenant() != self.platform:
            raise KnowledgeManagementError("SPACE_MEMBER_REQUIRED")
        with self.db.orm_session() as session:
            row = (
                session.query(SpaceModel.id, SpaceModel.name)
                .join(SpaceMemberModel, SpaceMemberModel.space_id == SpaceModel.id)
                .filter(
                    SpaceModel.id == space_id,
                    SpaceModel.env == self.env,
                    SpaceModel.deleted_at.is_(None),
                    SpaceMemberModel.env == self.env,
                    SpaceMemberModel.user_id == actor_id,
                    SpaceMemberModel.status == "ACTIVE",
                )
                .one_or_none()
            )
            if row is None:
                raise KnowledgeManagementError("SPACE_MEMBER_REQUIRED")
            return {"space_id": row.id, "name": row.name}

    def bot_context(self, scope, actor_id):
        from agentclaw.community.core.bot_knowledge.repository.store import (
            BotKnowledgeRepository,
        )

        return BotKnowledgeRepository(self.db).get_bot(scope, actor_id=actor_id)

    def conversation_bot(self, scope):
        if (
            not self.platform
            or get_current_avernet_tenant() != self.platform
            or scope.env != self.env
        ):
            raise KnowledgeManagementError("KNOWLEDGE_CHAT_ACCESS_DENIED")
        from agentclaw.community.core.bot_knowledge.repository.store import (
            BotKnowledgeRepository,
        )

        return BotKnowledgeRepository(self.db).get_bot(scope)

    def selections(self, *, bot, kb_ids):
        from agentclaw.community.core.bot_knowledge.repository.models import (
            BotKnowledgeBindingModel as Binding,
        )

        if not kb_ids:
            return {}
        if len(kb_ids) > 100:
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        with self.db.orm_session() as session:
            rows = (
                session.query(Binding)
                .filter(
                    Binding.avernet_tenant == get_current_avernet_tenant(),
                    Binding.env == self.env,
                    Binding.bot_pk == bot["id"],
                    Binding.space_id == bot["space_id"],
                    Binding.kb_id.in_(kb_ids),
                )
                .limit(len(kb_ids) + 1)
                .all()
            )
            if len(rows) > len(kb_ids):
                raise KnowledgeManagementError("KNOWLEDGE_ENABLEMENT_UNAVAILABLE")
            return {
                row.kb_id: {"enabled": row.enabled, "revision": row.revision}
                for row in rows
            }

    def _queries(self, session, space_id):
        query = (
            session.query(BotModel, SpaceMemberModel.user_name)
            .outerjoin(
                SpaceMemberModel,
                and_(
                    SpaceMemberModel.space_id == space_id,
                    SpaceMemberModel.env == self.env,
                    SpaceMemberModel.user_id == BotModel.owner_id,
                    SpaceMemberModel.status == "ACTIVE",
                ),
            )
            .filter(
                BotModel.env == self.env,
                BotModel.is_delete == 0,
            )
        )
        explicit = query.filter(BotModel.space_id == space_id)
        personal_owner = (
            session.query(SpaceModel.personal_owner_id)
            .filter(
                SpaceModel.id == space_id,
                SpaceModel.env == self.env,
                SpaceModel.space_type == "PERSONAL",
                SpaceModel.deleted_at.is_(None),
            )
            .scalar()
        )
        if personal_owner is None:
            return (explicit,)
        # Inventory treats unassigned historical Bots as their Owner's personal
        # Bots. Keep disjoint, indexable branches; each caller bounds both before
        # merging, without relying on a UNION's LIMIT/seek pushdown by the DB.
        legacy = query.filter(
            BotModel.space_id.is_(None), BotModel.owner_id == personal_owner
        )
        return explicit, legacy

    @staticmethod
    def _display(bot, member_name):
        try:
            ext = json.loads(bot.ext) if bot.ext else {}
        except (TypeError, ValueError):
            ext = {}
        avatar = ext.get("avatar_url") if isinstance(ext, dict) else None
        return {
            "bot_pk": bot.id,
            "bot_id": bot.bot_id,
            "owner_id": bot.owner_id,
            "bot_name": bot.bot_name,
            "avatar_url": avatar if isinstance(avatar, str) else None,
            "owner_name": bot.owner_name or member_name,
            "bot_type": bot.bot_type,
        }

    def display(self, space_id: int, identities: list[tuple[str, str]]) -> dict:
        if len(identities) > 100:
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        if not identities:
            return {}
        with self.db.orm_session() as session:
            result = {}
            for query in self._queries(session, space_id):
                rows = (
                    query.filter(
                        tuple_(BotModel.bot_id, BotModel.owner_id).in_(identities)
                    )
                    .limit(len(identities) + 1)
                    .all()
                )
                for bot, member_name in rows:
                    identity = (bot.bot_id, bot.owner_id)
                    if identity in result:
                        raise KnowledgeManagementError(
                            "KNOWLEDGE_BOT_IDENTITY_AMBIGUOUS"
                        )
                    result[identity] = self._display(bot, member_name)
            return result

    def candidates(
        self, space_id: int, *, cursor: int, limit: int, keyword: str
    ) -> dict:
        with self.db.orm_session() as session:
            rows = []
            for query in self._queries(session, space_id):
                query = query.filter(BotModel.id > cursor)
                if keyword:
                    query = query.filter(
                        or_(
                            BotModel.bot_name.contains(keyword, autoescape=True),
                            BotModel.owner_id.contains(keyword, autoescape=True),
                            BotModel.owner_name.contains(keyword, autoescape=True),
                            SpaceMemberModel.user_name.contains(
                                keyword, autoescape=True
                            ),
                        )
                    )
                rows.extend(query.order_by(BotModel.id).limit(limit + 1).all())
            # At most 2 * (limit + 1) rows, with disjoint physical space scopes.
            rows.sort(key=lambda row: row[0].id)
            items = [self._display(bot, name) for bot, name in rows[:limit]]
            for item in items:
                item.pop("bot_pk")
            return {
                "items": items,
                "next_cursor": rows[limit - 1][0].id if len(rows) > limit else None,
            }


class ExistingKnowledgeEnablementReader:
    """Read existing Bot enablement; never mirror platform grants or ACLs."""

    def __init__(self, db: DatabasePlugin, env: str):
        self.db, self.env = db, env

    def read(self, *, space_id: int, kb_id: str, bots: dict) -> dict:
        if not bots:
            return {}
        try:
            from agentclaw.community.core.bot_knowledge.repository.models import (
                BotKnowledgeBindingModel as Binding,
            )

            by_pk = {bot["bot_pk"]: identity for identity, bot in bots.items()}
            with self.db.orm_session() as session:
                rows = (
                    session.query(Binding)
                    .filter(
                        Binding.avernet_tenant == get_current_avernet_tenant(),
                        Binding.env == self.env,
                        Binding.space_id == space_id,
                        Binding.kb_id == kb_id,
                        Binding.bot_pk.in_(by_pk),
                    )
                    .limit(len(by_pk) + 1)
                    .all()
                )
                result = dict.fromkeys(bots, False)
                seen = set()
                for row in rows:
                    if row.bot_pk in seen or type(row.enabled) is not bool:
                        raise ValueError("invalid enablement row")
                    seen.add(row.bot_pk)
                    result[by_pk[row.bot_pk]] = row.enabled
                return result
        except Exception:
            # Missing module/table and read failures are not an absent binding.
            raise KnowledgeManagementError("KNOWLEDGE_ENABLEMENT_UNAVAILABLE") from None
