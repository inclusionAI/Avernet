"""Atomic Bot-scoped persistence; no remote I/O occurs in these transactions."""

from __future__ import annotations


from injector import inject
from sqlalchemy import literal, select, update
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_knowledge.repository.online_selection import read_selection
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.knowledge import (
    BotKnowledgeScope,
    KnowledgeError,
    KnowledgeSelectionStage,
    require_selection_stage,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel


def binding_view(
    row: Binding, bot: BotModel, selection_stage: KnowledgeSelectionStage = "draft"
) -> dict:
    online = selection_stage == "online" and bot.bot_type == "service"
    return {
        "bot_id": bot.bot_id,
        "owner_id": bot.owner_id,
        "kb_id": row.kb_id,
        "enabled": row.online_enabled if online else row.enabled,
        "revision": row.revision,
        "runtime_selection": "online" if online else "current",
    }


def resolve_bot(session, scope: BotKnowledgeScope, *, lock: bool = False) -> BotModel:
    query = session.query(BotModel).filter(
        BotModel.bot_id == scope.bot_id,
        BotModel.owner_id == scope.owner_id,
        BotModel.env == scope.env,
        BotModel.is_delete == 0,
    )
    bot = (query.with_for_update() if lock else query).one_or_none()
    if bot is None:
        raise KnowledgeError("BOT_NOT_FOUND", 404)
    if lock:
        # SQLite ignores FOR UPDATE. A no-op write acquires its writer lock BEFORE
        # loading mutable configuration, while also serializing MySQL writers.
        session.execute(update(BotModel).where(BotModel.id == bot.id).values(id=bot.id))
        session.refresh(bot, with_for_update=True)
    return bot


def require_space(
    session,
    bot: BotModel,
    actor_id: str | None = None,
    *,
    include_owner: bool = True,
    lock: bool = False,
) -> None:
    space_query = session.query(SpaceModel).filter(
        SpaceModel.id == bot.space_id,
        SpaceModel.env == bot.env,
        SpaceModel.deleted_at.is_(None),
    )
    space = (space_query.with_for_update() if lock else space_query).one_or_none()
    if space is None:
        raise KnowledgeError("SPACE_NOT_FOUND", 404)
    # Native EditorPolicy revokes editors on Team membership only. Personal
    # explicit editors need not have a Space-member row (nor does ownership
    # derive from one). Keep the Space existence/deletion check for both.
    if space.space_type == "PERSONAL":
        return
    required = {bot.owner_id} if include_owner else set()
    if actor_id is not None:
        required.add(actor_id)
    members_query = session.query(SpaceMemberModel.user_id).filter(
        SpaceMemberModel.space_id == bot.space_id,
        SpaceMemberModel.env == bot.env,
        SpaceMemberModel.status == "ACTIVE",
        SpaceMemberModel.user_id.in_(required),
    )
    members = (members_query.with_for_update() if lock else members_query).all()
    if {m[0] for m in members} != required:
        raise KnowledgeError("SPACE_MEMBER_REQUIRED", 403)


def require_editor(
    session, bot: BotModel, actor_id: str, *, lock: bool = False
) -> None:
    require_space(session, bot, actor_id, lock=lock)
    if actor_id == bot.owner_id:
        return
    collaborator_query = session.query(BotCollaboratorModel).filter(
        BotCollaboratorModel.bot_pk == bot.id,
        BotCollaboratorModel.env == bot.env,
        BotCollaboratorModel.user_id == actor_id,
        BotCollaboratorModel.role.in_(("admin", "member")),
    )
    row = (
        collaborator_query.with_for_update() if lock else collaborator_query
    ).one_or_none()
    if row is None:
        raise KnowledgeError("BOT_EDITOR_REQUIRED", 403)


def require_editable(session, bot: BotModel, *, lock: bool = False) -> None:
    """Same current-draft rule as service-Bot editing, not a versioned selection."""
    if bot.bot_type != "service":
        return
    from agentclaw.community.core.service_bot.repository.models import (
        BotPublishModel,
        PublishStatus,
    )

    query = session.query(BotPublishModel).filter(BotPublishModel.env == bot.env)
    if bot.bot_id != "default":
        query = query.filter(BotPublishModel.source_bot_pk == bot.id)
    else:
        query = query.filter(
            BotPublishModel.publish_bot_id == bot.bot_id,
            BotPublishModel.owner_id == bot.entity_id,
        )
        # Publish rows have no tenant column. A retained source Bot (including
        # a retired source PK in a legacy recovery) is the tenant authority.
        # Do not rebind legacy records to this Bot's current PK. A hard-deleted
        # source has no trustworthy tenant attribution: fail closed rather than
        # accidentally exposing an older draft by dropping an orphan successor.
        source_table = BotModel.__table__
        source_exists = (
            select(source_table.c.id)
            .where(source_table.c.id == BotPublishModel.source_bot_pk)
            .exists()
        )
        unresolved = query.filter(~source_exists)
        if (unresolved.with_for_update() if lock else unresolved).first() is not None:
            raise KnowledgeError("BOT_DRAFT_REQUIRED", 403)
        trusted_source = (
            select(BotModel.id)
            .where(
                BotModel.id == BotPublishModel.source_bot_pk,
                BotModel.avernet_tenant == bot.avernet_tenant,
                BotModel.env == bot.env,
            )
            .exists()
        )
        query = query.filter(trusted_source)
    query = query.order_by(BotPublishModel.version.desc(), BotPublishModel.id.desc())
    current = (query.with_for_update() if lock else query).first()
    if current is None or current.status != PublishStatus.DRAFT:
        raise KnowledgeError("BOT_DRAFT_REQUIRED", 403)


class BotKnowledgeRepository:
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self.db = db

    def get_bot(
        self,
        scope: BotKnowledgeScope,
        *,
        actor_id: str | None = None,
        include_owner: bool = True,
        for_edit: bool = False,
    ) -> dict:
        with self.db.orm_session() as session:
            bot = resolve_bot(session, scope)
            if actor_id is None:
                require_space(session, bot, include_owner=include_owner)
            else:
                require_editor(session, bot, actor_id)
            if for_edit:
                require_editable(session, bot)
            return bot.to_dict()

    def mutate(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        kb_id: str,
        value: bool,
        space_id: int,
        expected_revision: int,
    ) -> dict:
        try:
            with self.db.transactional_orm_session() as session:
                bot = resolve_bot(session, scope, lock=True)
                require_editor(session, bot, actor_id, lock=True)
                require_editable(session, bot, lock=True)
                if bot.space_id != space_id:
                    raise KnowledgeError("SPACE_MISMATCH", 403)
                row = (
                    session.query(Binding)
                    .filter(
                        Binding.bot_pk == bot.id,
                        Binding.env == scope.env,
                        Binding.kb_id == kb_id,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                previous = row.revision if row else 0
                current_space = row is not None and row.space_id == bot.space_id
                # Other-space rows are absent from this space's public view.
                # Reclaim them with revision=0, but never reset the stored CAS
                # counter: an old-space request must not match the new state.
                if expected_revision != (previous if current_space else 0):
                    raise KnowledgeError("REVISION_CONFLICT")
                if row is None and not value:
                    # Disabling an absent choice is a no-op, not a tombstone.
                    return binding_view(
                        Binding(kb_id=kb_id, enabled=False, revision=0), bot
                    )
                if value and (not current_space or not row.enabled):
                    count = (
                        session.query(Binding)
                        .filter(
                            Binding.bot_pk == bot.id,
                            Binding.env == scope.env,
                            Binding.space_id == bot.space_id,
                            Binding.enabled.is_(True),
                        )
                        .count()
                    )
                    if count >= 100:
                        raise KnowledgeError("KNOWLEDGE_LIMIT_EXCEEDED")
                if row is None:
                    row = Binding(
                        bot_pk=bot.id,
                        space_id=bot.space_id,
                        env=scope.env,
                        kb_id=kb_id,
                        enabled=False,
                        revision=0,
                        updated_by=actor_id,
                    )
                    session.add(row)
                changed = not current_space or row.enabled != value
                if not current_space:
                    # Reclaiming a moved-space row must not carry old production access.
                    row.online_enabled = False
                row.space_id = bot.space_id
                row.enabled = value
                if changed or not previous:
                    row.revision += 1
                    row.updated_by = actor_id
                session.flush()
                result = binding_view(row, bot)
                return result
        except IntegrityError:
            raise KnowledgeError("REVISION_CONFLICT") from None

    def list_enabled_current(self, scope: BotKnowledgeScope) -> tuple[str, ...]:
        """Compatibility view: personal current or service draft/verification."""
        return self.list_enabled(scope, selection_stage="draft")

    def list_enabled(
        self, scope: BotKnowledgeScope, *, selection_stage: KnowledgeSelectionStage
    ) -> tuple[str, ...]:
        require_selection_stage(selection_stage)
        with self.db.orm_session() as session:
            try:
                bot = resolve_bot(session, scope)
                require_space(session, bot)
            except KnowledgeError as exc:
                if exc.status in (403, 404):
                    return ()
                raise
            enabled_column = (
                Binding.online_enabled
                if selection_stage == "online" and bot.bot_type == "service"
                else Binding.enabled
            )
            selection = (
                read_selection(session, bot)
                if selection_stage == "online" and bot.bot_type == "service"
                else None
            )
            if selection is not None and not selection.ids:
                return ()
            known = selection.unchanged(session) if selection is not None else literal(True)
            ids_filter = Binding.kb_id.in_(selection.ids) if selection is not None else literal(True)
            rows = (
                session.query(Binding.kb_id)
                .filter(
                    Binding.env == scope.env,
                    Binding.bot_pk == bot.id,
                    Binding.space_id == bot.space_id,
                    enabled_column.is_(True),
                    ids_filter,
                    known,
                )
                .order_by(Binding.kb_id)
                .limit(101)
                .all()
            )
            if len(rows) > 100:
                raise KnowledgeError("KNOWLEDGE_LIMIT_EXCEEDED", 503)
            return tuple(row[0] for row in rows)

    def list_bindings(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        cursor: int = 0,
        limit: int = 20,
        selection_stage: KnowledgeSelectionStage = "draft",
    ) -> dict:
        require_selection_stage(selection_stage)
        with self.db.orm_session() as session:
            bot = resolve_bot(session, scope)
            require_editor(session, bot, actor_id)
            selection = (
                read_selection(session, bot)
                if selection_stage == "online" and bot.bot_type == "service"
                else None
            )
            known = selection.unchanged(session) if selection is not None else literal(True)
            rows = (
                session.query(Binding, known.label("selection_known"))
                .filter(
                    Binding.env == scope.env,
                    Binding.bot_pk == bot.id,
                    Binding.space_id == bot.space_id,
                    Binding.id > cursor,
                )
                .order_by(Binding.id)
                .limit(limit + 1)
                .all()
            )
            page = rows[:limit]
            items = []
            for row, selection_known in page:
                item = binding_view(row, bot, selection_stage)
                if not selection_known or (selection is not None and row.kb_id not in selection.ids):
                    item["enabled"] = False
                items.append(item)
            result = {
                "items": items,
                "next_cursor": page[-1][0].id if len(rows) > limit else None,
                "runtime_selection": (
                    "online"
                    if selection_stage == "online" and bot.bot_type == "service"
                    else "current"
                ),
                "selection_stage": selection_stage,
            }
            return result
