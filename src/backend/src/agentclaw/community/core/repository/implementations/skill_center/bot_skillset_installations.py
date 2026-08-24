"""Bot-wide resolution from SkillSet membership to Installation rows.

Separate from ``capability_desired_state``, which mutates one named Set: these
range over every Set a Bot has and answer what its Installation table should say.
"""

from __future__ import annotations

from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.default_skillset_projection import (
    excluded_skill_ids,
    global_default_scope,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    BotSkillSetBridge,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def set_member_skill_ids(scope, session, *, skill_set_id: int) -> set[int]:
    """The Skill ids one SkillSet provides.

    Must agree with ``SkillSetRepository.get_skills_in_set_for_env``: a
    ``center://`` membership names its Skill by ``skill_uuid`` and resolves to
    the highest PUBLISHED version, everything else joins by ``skill_id``.
    """
    env = get_current_env()

    def ids(query) -> set[int]:
        return {int(row[0]) for row in query.all()}

    non_center = ids(
        scope(session.query(Skill.id), Skill)
        .join(SkillSetSkill, SkillSetSkill.skill_id == Skill.id)
        .filter(
            SkillSetSkill.skill_set_id == skill_set_id,
            SkillSetSkill.env == env,
            ~Skill.git_path.like("center://%"),
        )
    )
    latest = (
        scope(
            session.query(Skill.skill_uuid, func.max(Skill.version).label("mv")),
            Skill,
        )
        .filter(Skill.skill_uuid.isnot(None), Skill.status == "PUBLISHED")
        .group_by(Skill.skill_uuid)
        .subquery()
    )
    center = ids(
        scope(session.query(Skill.id), Skill)
        .join(SkillSetSkill, SkillSetSkill.skill_uuid == Skill.skill_uuid)
        .join(
            latest,
            and_(
                Skill.skill_uuid == latest.c.skill_uuid,
                Skill.version == latest.c.mv,
            ),
        )
        .filter(
            SkillSetSkill.skill_set_id == skill_set_id,
            SkillSetSkill.env == env,
            Skill.git_path.like("center://%"),
            Skill.status == "PUBLISHED",
        )
    )
    return non_center | center


class BotSkillSetInstallations:
    """Mixed into the control-plane repository; uses its ``_db``, ``_scope``,
    ``_owned_set_scope`` and ``_installation_repository``."""

    def flush_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> BotSkillSetBridge:
        """Make Installation say what SkillSet membership implies.

        Returns the bridge it applied, so the caller need not resolve twice.
        """
        # Resolve unlocked first: a Bot that already agrees — the common case —
        # answers here without a row lock or a write transaction.
        with self._db.orm_session() as session:
            bridge = self._resolve_bridge(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            installed = self._installed_skill_ids(
                session, bot_id=bot_id, owner_id=owner_id, env=env
            )
        if not (bridge.activate - installed) and not (bridge.deactivate & installed):
            return bridge

        # There is something to write, so resolve again holding the Set rows —
        # the same locks every SkillSet mutation takes.
        with self._db.transactional_orm_session() as session:
            bridge = self._resolve_bridge(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            rows = self._installation_rows(
                session, bot_id=bot_id, owner_id=owner_id, env=env
            ).with_for_update()
            installed = {int(row.skill_id) for row in rows.all()}
            for skill_id in sorted(bridge.activate - installed):
                self._install_one(
                    session, bot_id=bot_id, owner_id=owner_id, env=env,
                    skill_id=skill_id,
                )
            stale = sorted(bridge.deactivate & installed)
            if stale:
                rows.filter(BotSkillInstallation.skill_id.in_(stale)).delete(
                    synchronize_session=False
                )
            return bridge

    def _install_one(
        self, session, *, bot_id: str, owner_id: str, env: str, skill_id: int
    ) -> None:
        """Insert one Installation row, tolerating a concurrent winner.

        Each insert gets its own SAVEPOINT so a lost race rolls back that row
        alone. A losing insert is the only recoverable ``IntegrityError``, so
        anything the re-read cannot find is re-raised rather than swallowed —
        a deleted Skill, say. The re-read locks because InnoDB would otherwise
        answer it from this transaction's pre-race snapshot.
        """
        try:
            with session.begin_nested():
                session.add(
                    BotSkillInstallation(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        skill_id=skill_id,
                        env=env,
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
        except IntegrityError:
            winner = (
                self._installation_rows(
                    session, bot_id=bot_id, owner_id=owner_id, env=env
                )
                .filter(BotSkillInstallation.skill_id == skill_id)
                .with_for_update()
                .first()
            )
            if not winner:
                raise

    @staticmethod
    def _installation_rows(session, *, bot_id: str, owner_id: str, env: str):
        # ``env`` comes from the Bot, not the process: every other reader keys
        # Installation on the Bot's env.
        return session.query(BotSkillInstallation).filter(
            BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
            BotSkillInstallation.env == env,
            BotSkillInstallation.owner_id == owner_id,
            BotSkillInstallation.bot_id == bot_id,
        )

    @classmethod
    def _installed_skill_ids(
        cls, session, *, bot_id: str, owner_id: str, env: str
    ) -> set[int]:
        return {
            int(row.skill_id)
            for row in cls._installation_rows(
                session, bot_id=bot_id, owner_id=owner_id, env=env
            ).all()
        }

    def _resolve_bridge(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
        locked: bool = False,
    ) -> BotSkillSetBridge:
        """What Installation should say, given the Sets this Bot has.

        Runs in the caller's session so a repair can resolve and write in one
        transaction; ``locked`` holds the Set rows for its duration.
        """
        members: set[int] = set()
        activate: set[int] = set()
        inactive: set[int] = set()
        for row in self._bot_sets(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
            locked=locked,
        ):
            # A Default Set counts as active whatever its column says, and is
            # the only Set whose members are removed by an exclusion row rather
            # than by deleting the membership.
            is_default = bool(row.is_default)
            excluded = (
                excluded_skill_ids(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
                if is_default
                else frozenset()
            )
            for member_id in set_member_skill_ids(
                self._scope, session, skill_set_id=int(row.id)
            ):
                # An excluded member is under direct control from then on, so
                # the repair speaks for it in neither direction.
                if member_id in excluded:
                    continue
                members.add(member_id)
                claimed = activate if is_default or bool(row.is_active) else inactive
                claimed.add(member_id)
        return BotSkillSetBridge(
            members=frozenset(members),
            activate=frozenset(activate),
            # An active claim wins, so a stale inactive membership cannot
            # uninstall a live member.
            deactivate=frozenset(inactive - activate),
        )

    def ensure_active_skillset_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None = None,
    ) -> int:
        """Insert missing rows for active ordinary SkillSet members.

        A narrow cutover repair: never reads the Default Set, never removes
        rows, never changes existing Direct desired state.
        """
        with self._db.orm_session() as session:
            sets = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id,
                SkillSet.user_id == owner_id,
                SkillSet.is_default.is_(False),
                SkillSet.is_active.is_(True),
            )
            if engine_type is not None:
                sets = sets.filter(SkillSet.engine_type == engine_type)
            active_set_ids = {int(row.id) for row in sets.all()}
            if not active_set_ids:
                return 0

            member_ids = {
                int(row.skill_id)
                for row in self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(SkillSetSkill.skill_set_id.in_(active_set_ids))
                .all()
            }
            if not member_ids:
                return 0

        return sum(
            self._installation_repository.install(
                env=get_current_env(),
                owner_id=owner_id,
                bot_id=bot_id,
                skill_id=skill_id,
            )
            for skill_id in member_ids
        )

    def _bot_sets(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
        locked: bool = False,
    ) -> list[SkillSet]:
        """The Sets a Bot has: its own — including its legacy Default — plus
        the platform Default it inherits."""

        def rows(query):
            # Ordered by id. The queries below run owned then defaults, and
            # every caller reaches them through here, so callers agree with
            # each other — but not with `_set`/`_snapshot`. That is the open
            # lock-ordering gap, tracked separately.
            query = query.order_by(SkillSet.id)
            return (query.with_for_update() if locked else query).all()

        owned = rows(
            self._scope(session.query(SkillSet), SkillSet).filter(
                self._owned_set_scope(
                    bot_id=bot_id, owner_id=owner_id, engine_type=engine_type
                )
            )
        )
        candidates = default_engine_types or ((engine_type,) if engine_type else ())
        defaults: list[SkillSet] = []
        for candidate in candidates:
            defaults = rows(
                self._scope(session.query(SkillSet), SkillSet).filter(
                    global_default_scope((candidate,))
                )
            )
            if defaults:
                break
        return [*defaults, *owned]
