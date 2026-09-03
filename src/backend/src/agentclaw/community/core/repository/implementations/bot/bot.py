"""Unified Bot repository (prod OceanBase + local SQLite).

One ORM implementation behind ``BotRepository``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes,
so this single body runs unchanged on OceanBase (prod) and SQLite
(local), collapsing the previous raw-SQL/ORM twins so CI exercises
the prod path too.

Prod-twin parity (the raw-SQL ``ZdasBotRepository`` is the
reference). Three deliberate adopt-prod behavior changes vs the old
SQLite twin (flagged in the PR):

1. **Env-scoping** — every query / count / exists / update /
   soft-delete filters ``env == get_current_env()`` (and
   ``is_delete == 0`` where prod does). The old SQLite twin did NO
   env filtering.
2. **``update_by_owner`` field allowlist** — only prod's fixed
   allowlist of columns is written; any other field (notably
   ``engine_types``, ``creator_id``) is silently ignored, exactly as
   prod does. The old SQLite twin ``setattr``'d any field.
3. **``get_device_provider_*``** — implements prod's real
   ``ac_bots`` ⟕ ``ac_entity_device_binding`` join (the old SQLite
   twin was a stub returning ``None``).

``insert`` is a **plain INSERT** (``db.add`` + ``db.flush``) — the
table has no unique key; never an upsert. ``update_by_owner`` /
``soft_delete_by_owner`` are **single conditional UPDATEs** guarded
by ``is_delete=0 AND env`` with ``rowcount==0 → None/False`` (no
SELECT-first); soft-delete is an ``is_delete=1`` UPDATE, never a
DELETE. ``gmt_modified=func.now()`` DB-side on every UPDATE (prod's
literal ``NOW()`` / model ``onupdate``). Return shaping reuses
``BotModel.to_dict()``; ``search_bots`` returns the full ``to_dict()``
(a superset of prod's projected columns — behavior-compatible since
prod consumers only read the projected subset) plus a nested
``publish``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import and_, func, or_

from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.repository.implementations.bot.engine_filter import (
    engine_criterion,
)
from agentclaw.community.core.repository.implementations.bot.reachability import (
    BotReachabilityQueries,
)
from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository as BotRepositoryProtocol,
)

logger = get_logger()


# Prod's exact ``update_by_owner`` allowlist. Any field not here
# (e.g. engine_types, creator_id, entity_id) is silently ignored —
# faithful prod parity.
_UPDATE_ALLOWED_FIELDS = (
    "bot_name",
    "bot_desc",
    "owner_name",
    "modifier_id",
    "share_policy",
    "status",
    "binding_id",
    "device_id",
    "active_engine",
    "public",
    "ext",
    "bot_type",
)
# Only allowlisted JSON columns are reachable; engine_types is NOT
# in _UPDATE_ALLOWED_FIELDS (prod parity — it is silently dropped).
_JSON_FIELDS = ("share_policy", "ext")


class BotRepository(
    BotReachabilityQueries,
    BotRepositoryProtocol,
):
    """Unified ORM ``BotRepository`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self.Model = BotModel

    # ── insert (plain INSERT — no unique key, never an upsert) ──

    def insert(self, bot_data: Dict[str, Any]) -> Dict[str, Any]:
        share_policy = bot_data.get("share_policy")
        if share_policy is not None:
            share_policy = json.dumps(share_policy)

        ext = bot_data.get("ext")
        if ext is not None:
            ext = json.dumps(ext)

        engine_types = bot_data.get("engine_types")
        engine_types = (
            json.dumps(engine_types)
            if engine_types is not None
            else json.dumps(list(SUPPORTED_ENGINE_TYPES))
        )

        with self._db.orm_session() as db:
            bot = self.Model(
                bot_id=bot_data["bot_id"],
                bot_name=bot_data.get("bot_name"),
                bot_desc=bot_data.get("bot_desc"),
                entity_id=bot_data["entity_id"],
                entity_type=bot_data["entity_type"],
                creator_id=bot_data["creator_id"],
                owner_id=bot_data["owner_id"],
                engine_types=engine_types,
                status=bot_data.get("status", "PENDING"),
                binding_id=bot_data.get("binding_id"),
                modifier_id=bot_data.get("modifier_id"),
                share_policy=share_policy,
                is_delete=bot_data.get("is_delete", 0),
                active_engine=bot_data.get("active_engine", "moltis"),
                device_id=bot_data.get("device_id"),
                env=get_current_env(),
                owner_name=bot_data.get("owner_name"),
                public=bot_data.get("public", "0"),
                ext=ext,
                bot_type=bot_data.get("bot_type", "personal"),
                template_type=bot_data.get("template_type"),
                space_id=bot_data.get("space_id"),
            )
            db.add(bot)
            db.flush()
            return bot.to_dict()

    # ── queries (env-scoped — adopt prod) ───────────────────────

    def _env(self):
        return self.Model.env == get_current_env()

    @staticmethod
    def _to_caller_identity_dict(bot: BotModel) -> Dict[str, Any]:
        """Return the private Bot projection required by Caller services."""
        result = bot.to_dict()
        result["call_type"] = bot.call_type
        result["caller_config_revision"] = bot.caller_config_revision
        return result

    def get_by_id_and_owner(
        self,
        bot_id: str,
        owner_id: str,
        *,
        execution_options: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            q = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self._env(),
            )
            if execution_options:
                q = q.execution_options(**execution_options)
            bot = q.first()
            return bot.to_dict() if bot else None

    def get_live_by_id_owner_and_env(
        self, *, bot_id: str, owner_id: str, env: str
    ) -> list[dict[str, Any]]:
        """Return every live exact match without consulting runtime env."""
        with self._db.orm_session() as db:
            bots = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self.Model.env == env,
                )
                .order_by(self.Model.id.asc())
                .all()
            )
            return [bot.to_dict() for bot in bots]

    def update_ext_by_id_owner_and_env(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        ext: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically update one live exact-env row or roll back."""
        with self._db.orm_session() as db:
            exact = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self.Model.env == env,
            )
            rowcount = exact.update(
                {
                    self.Model.ext: json.dumps(ext),
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            if rowcount != 1:
                raise RuntimeError(
                    "explicit-env bot ext update must affect exactly one live row; "
                    f"affected={rowcount}"
                )
            bot = exact.one()
            return bot.to_dict()

    def get_by_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Query bot by bot_id only (no owner check).

        Used when we need to read bot metadata (like active_engine) but
        don't need to verify ownership. For permission checks, use
        get_by_id_and_owner instead.
        """
        with self._db.orm_session() as db:
            bot = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .first()
            )
            return bot.to_dict() if bot else None

    def get_by_id_and_entity(
        self, bot_id: str, entity_id: str
    ) -> Optional[Dict[str, Any]]:
        """Query one live Bot by exact bot and entity identifiers in this env."""
        with self._db.orm_session() as db:
            # COSEC: SQLAlchemy binds both identifiers; never interpolate request
            # values into a raw SQL expression.
            bot = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.entity_id == entity_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .first()
            )
            return self._to_caller_identity_dict(bot) if bot else None

    def get_unique_by_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Return one live Bot by id or fail closed when callers are ambiguous."""
        with self._db.orm_session() as db:
            # COSEC: SQLAlchemy binds the request-derived identifier; never
            # interpolate it into a raw SQL expression.
            bots = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .limit(2)
                .all()
            )
            if len(bots) > 1:
                raise BotLookupAmbiguousError
            return self._to_caller_identity_dict(bots[0]) if bots else None

    def list_by_owner(
        self, owner_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.is_delete == 0,
                self.Model.owner_id == owner_id,
                self._env(),
            )
            total = query.count()
            bots = (
                query.order_by(self.Model.gmt_create.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [b.to_dict() for b in bots]

    def list_live_bot_ids_by_owner(self, owner_id: str) -> List[str]:
        """Every live ``bot_id`` for this owner, in a single id-only query."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model.bot_id)
                .filter(
                    self.Model.is_delete == 0,
                    self.Model.owner_id == owner_id,
                    self._env(),
                )
                .all()
            )
            return [row[0] for row in rows]

    def list_by_owner_or_collaborator(
        self, owner_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        with self._db.orm_session() as db:
            query = self._reachable_by(db, owner_id)
            total = query.count()
            bots = (
                query.order_by(self.Model.gmt_create.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [b.to_dict() for b in bots]

    def list_by_entity(
        self,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(self.Model.is_delete == 0, self._env())
            if entity_id:
                query = query.filter(self.Model.entity_id == entity_id)
            if entity_type:
                query = query.filter(self.Model.entity_type == entity_type)
            total = query.count()
            bots = (
                query.order_by(self.Model.gmt_create.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [b.to_dict() for b in bots]

    def list_by_conditions(
        self,
        public: Optional[str] = None,
        bot_name: Optional[str] = None,
        owner_name: Optional[str] = None,
        bot_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        engine: Optional[str] = None,
        status: Optional[str] = None,
        space_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        bot_ids: Optional[List[str]] = None,
    ) -> tuple[int, List[Dict[str, Any]]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(self.Model.is_delete == 0, self._env())
            if public:
                query = query.filter(self.Model.public == public)
            if bot_name:
                # autoescape=True escapes the caller's % and _ and emits the
                # matching ESCAPE clause. Interpolating into LIKE directly makes
                # those characters wildcards, so searching for a name containing
                # a literal "%" matched everything and inflated the total.
                query = query.filter(
                    self.Model.bot_name.contains(bot_name, autoescape=True)
                )
            if owner_name:
                query = query.filter(self.Model.owner_name == owner_name)
            if bot_id:
                query = query.filter(self.Model.bot_id == bot_id)
            if bot_ids is not None:
                # An empty list is a real restriction — "none of them" — and must
                # not be read as "no filter". Callers that mean "unrestricted"
                # pass None; the difference is the whole point of the parameter.
                query = query.filter(self.Model.bot_id.in_(bot_ids))
            if owner_id:
                query = query.filter(self.Model.owner_id == owner_id)
            if space_id is not None:
                query = query.filter(self.Model.space_id == space_id)
            if engine:
                query = query.filter(engine_criterion(self.Model, engine))
            if status:
                query = query.filter(self.Model.status == status)
            total = query.count()
            bots = (
                query.order_by(self.Model.gmt_create.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [b.to_dict() for b in bots]

    def list_public_bots_by_owner_bot_pairs(
        self, pairs: List[tuple[str, str]]
    ) -> List[Dict[str, Any]]:
        """Live public bots matching any ``(bot_id, owner_id)`` pair, this env.

        ORM-based replacement for a former raw ``cursor.execute("… FROM
        ac_bots …")`` in ``bot_discover_service`` — raw SQL bypasses the
        ``BotModel`` tenant guard (``do_orm_execute`` only fires for ORM
        statements), so this read now goes through the ORM and is tenant-scoped
        automatically. Returns ``to_dict()`` dicts (the search-interface shape
        the caller wants).
        """
        if not pairs:
            return []
        with self._db.orm_session() as db:
            bots = (
                db.query(self.Model)
                .filter(
                    or_(
                        *[
                            and_(
                                self.Model.bot_id == bot_id,
                                self.Model.owner_id == owner_id,
                            )
                            for bot_id, owner_id in pairs
                        ]
                    ),
                    self.Model.is_delete == 0,
                    self._env(),
                    self.Model.public == "1",
                )
                .all()
            )
            return [b.to_dict() for b in bots]

    def list_bots_by_owner_bot_pairs(
        self,
        pairs: List[tuple[str, str]],
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """Page exact Bot/owner pairs within the current tenant and environment."""
        if not pairs:
            return 0, []
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                or_(
                    *[
                        and_(
                            self.Model.bot_id == bot_id,
                            self.Model.owner_id == owner_id,
                        )
                        for bot_id, owner_id in pairs
                    ]
                ),
                self.Model.is_delete == 0,
                self._env(),
            )
            total = query.count()
            bots = (
                query.order_by(self.Model.gmt_create.desc(), self.Model.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [bot.to_dict() for bot in bots]

    def list_by_search(
        self,
        public: Optional[str] = None,
        search: Optional[str] = None,
        page: int | None = 1,
        page_size: int | None = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(self.Model.is_delete == 0, self._env())
            if public:
                query = query.filter(self.Model.public == public)
            if search:
                query = query.filter(
                    or_(
                        self.Model.owner_name.like(f"%{search}%"),
                        self.Model.bot_name.like(f"%{search}%"),
                    )
                )
            total = query.count()
            if page is None or page_size is None:
                bots = query.order_by(
                    self.Model.gmt_create.desc(), self.Model.id.desc()
                ).all()
            else:
                bots = (
                    query.order_by(self.Model.gmt_create.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                    .all()
                )
            return total, [b.to_dict() for b in bots]

    # ── updates (single conditional statements) ─────────────────

    def update_by_owner(
        self, bot_id: str, owner_id: str, update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        values: dict = {}
        for field in _UPDATE_ALLOWED_FIELDS:
            if field in update_data:
                v = update_data[field]
                if field in _JSON_FIELDS and v is not None:
                    v = json.dumps(v)
                values[getattr(self.Model, field)] = v

        if not values:
            return self.get_by_id_and_owner(bot_id, owner_id)

        values[self.Model.gmt_modified] = func.now()
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .update(values, synchronize_session=False)
            )
        if rowcount == 0:
            return None
        return self.get_by_id_and_owner(bot_id, owner_id)

    def update_space_by_owner(
        self, *, bot_id: str, owner_id: str, space_id: int
    ) -> Optional[Dict[str, Any]]:
        """Update only ``space_id`` without widening the generic allowlist.

        The generic ``update_by_owner`` intentionally mirrors the historical
        production allowlist. Space assignment is a newer structured mutation,
        so it gets an explicit repository operation instead of silently changing
        the semantics of every existing caller.
        """
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .update(
                    {
                        self.Model.space_id: space_id,
                        self.Model.modifier_id: owner_id,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
        if rowcount == 0:
            return None
        return self.get_by_id_and_owner(bot_id, owner_id)

    def compare_and_set_ext(
        self,
        *,
        bot_id: str,
        owner_id: str,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Whole-column ``ext`` CAS scoped by owner, env, and live row."""
        expected_json = json.dumps(expected_ext) if expected_ext is not None else None
        ext_json = json.dumps(ext)
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self._env(),
            )
            if expected_json is None:
                query = query.filter(self.Model.ext.is_(None))
            else:
                query = query.filter(self.Model.ext == expected_json)
            affected = query.update(
                {
                    self.Model.ext: ext_json,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
        if affected == 0:
            return None
        return self.get_by_id_and_owner(bot_id, owner_id)

    def soft_delete_by_owner(self, bot_id: str, owner_id: str) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .update(
                    {
                        self.Model.is_delete: 1,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            return rowcount > 0

    # ── counts / existence (env-scoped) ─────────────────────────

    def count_by_owner(self, owner_id: str, exclude_bot_type: str | None = None) -> int:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.is_delete == 0,
                self.Model.owner_id == owner_id,
                self._env(),
            )
            if exclude_bot_type:
                query = query.filter(self.Model.bot_type != exclude_bot_type)
            return query.count()

    def exists_by_owner_and_bot_id(self, owner_id: str, bot_id: str) -> bool:
        with self._db.orm_session() as db:
            return (
                db.query(self.Model)
                .filter(
                    self.Model.is_delete == 0,
                    self.Model.owner_id == owner_id,
                    self.Model.bot_id == bot_id,
                    self._env(),
                )
                .count()
                > 0
            )

    def exists_by_owner_and_bot_type(self, owner_id: str, bot_type: str) -> bool:
        with self._db.orm_session() as db:
            return (
                db.query(self.Model)
                .filter(
                    self.Model.is_delete == 0,
                    self.Model.owner_id == owner_id,
                    self.Model.bot_type == bot_type,
                    self._env(),
                )
                .count()
                > 0
            )

    def exists_by_bot_name(self, bot_name: str) -> bool:
        with self._db.orm_session() as db:
            return (
                db.query(self.Model)
                .filter(
                    self.Model.is_delete == 0,
                    self.Model.bot_name == bot_name,
                    self._env(),
                )
                .count()
                > 0
            )

    def get_by_bot_name(self, bot_name: str) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            bot = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_name == bot_name,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .first()
            )
            return bot.to_dict() if bot else None

    def get_by_binding_id(self, binding_id: int) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            bot = (
                db.query(self.Model)
                .filter(
                    self.Model.binding_id == binding_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .first()
            )
            return bot.to_dict() if bot else None

    # ── device-provider join (adopt prod — drop the local stub) ─

    def _device_provider_result(
        self, device_provider, device_props_json, bot_type=None
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "device_provider": device_provider,
            "bot_type": bot_type or "",
        }
        if device_provider == "arca":
            try:
                props = (
                    json.loads(device_props_json)
                    if isinstance(device_props_json, str)
                    else device_props_json
                )
                sandbox_id = (props or {}).get("sandbox_id")
                if sandbox_id:
                    result["sandbox_id"] = sandbox_id
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
        return result

    def get_device_provider_by_bot_id_and_owner(
        self, bot_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        from agentclaw.community.core.devices.repository.models import (
            EntityDeviceBinding,
        )

        with self._db.orm_session() as db:
            row = (
                db.query(
                    EntityDeviceBinding.device_provider,
                    EntityDeviceBinding.device_props,
                    self.Model.bot_type,
                )
                .select_from(self.Model)
                .outerjoin(
                    EntityDeviceBinding,
                    self.Model.device_id == EntityDeviceBinding.device_id,
                )
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .first()
            )
            if row is None:
                return None
            return self._device_provider_result(row[0], row[1], row[2])

    def get_device_provider_by_bot_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        from agentclaw.community.core.devices.repository.models import (
            EntityDeviceBinding,
        )

        with self._db.orm_session() as db:
            row = (
                db.query(
                    EntityDeviceBinding.device_provider,
                    EntityDeviceBinding.device_props,
                    self.Model.bot_type,
                )
                .select_from(self.Model)
                .outerjoin(
                    EntityDeviceBinding,
                    self.Model.device_id == EntityDeviceBinding.device_id,
                )
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .first()
            )
            if row is None:
                return None
            return self._device_provider_result(row[0], row[1], row[2])

    # ── search / active (env-scoped JOIN to bot_publish) ────────

    def search_bots(
        self,
        key: Optional[str] = None,
        bot_status: Optional[str] = None,
        public: Optional[str] = None,
        owner_id: Optional[str] = None,
        service_status_list: Optional[List[str]] = None,
        bot_type: Optional[str] = None,
        active_engine: Optional[str] = None,
        collaborator_user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        provider: Optional[str] = None,
        template_type: Optional[str] = None,
        bot_status_list: Optional[List[str]] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        from agentclaw.community.core.bot_collaborator.models import (
            BotCollaboratorModel,
        )
        from agentclaw.community.core.service_bot.repository.models import (
            BotPublishModel,
        )
        from agentclaw.community.core.devices.repository.models import (
            EntityDeviceBinding,
        )

        env = get_current_env()
        with self._db.orm_session() as db:
            # 构造查询：根据是否传入 collaborator_user_id 决定是否 JOIN 协作者表
            if collaborator_user_id:
                query = (
                    db.query(
                        self.Model,
                        BotPublishModel,
                        BotCollaboratorModel.role.label("user_role"),
                    )
                    .outerjoin(
                        BotPublishModel,
                        (BotPublishModel.source_bot_pk == self.Model.id)
                        & (BotPublishModel.env == env),
                    )
                    .outerjoin(
                        BotCollaboratorModel,
                        (BotCollaboratorModel.bot_pk == self.Model.id)
                        & (BotCollaboratorModel.env == env)
                        & (BotCollaboratorModel.user_id == collaborator_user_id),
                    )
                )
            else:
                query = db.query(self.Model, BotPublishModel).outerjoin(
                    BotPublishModel,
                    (BotPublishModel.source_bot_pk == self.Model.id)
                    & (BotPublishModel.env == env),
                )

            # 基础过滤
            query = query.filter(
                self.Model.is_delete == 0,
                self.Model.env == env,
            )

            # key 搜索（仅搜索 bot_name）
            if key:
                query = query.filter(self.Model.bot_name.like(f"%{key}%"))

            # bot_status 过滤
            if bot_status:
                query = query.filter(self.Model.status == bot_status)
            if bot_status_list:
                query = query.filter(self.Model.status.in_(bot_status_list))

            # public 过滤
            if public:
                query = query.filter(self.Model.public == public)

            # owner_id + collaborator_user_id 组合过滤
            if owner_id and collaborator_user_id:
                # 场景1: owner_id 匹配 或 协作者匹配
                query = query.filter(
                    or_(
                        self.Model.owner_id == owner_id,
                        BotCollaboratorModel.id.isnot(None),
                    )
                )
            elif owner_id:
                # 场景2: 仅 owner_id
                query = query.filter(self.Model.owner_id == owner_id)
            elif collaborator_user_id:
                # 场景3: 仅 collaborator_user_id
                query = query.filter(BotCollaboratorModel.id.isnot(None))

            # bot_type 过滤
            if bot_type:
                query = query.filter(self.Model.bot_type == bot_type)

            # active_engine 过滤
            if active_engine:
                query = query.filter(engine_criterion(self.Model, active_engine))

            # bot_id 过滤
            if bot_id:
                query = query.filter(self.Model.bot_id == bot_id)

            # template_type 过滤
            if template_type:
                query = query.filter(self.Model.template_type == template_type)

            # provider 过滤（按需 JOIN 设备绑定表，避免影响其它路径行数）
            if provider:
                query = (
                    query.outerjoin(
                        EntityDeviceBinding,
                        (self.Model.device_id == EntityDeviceBinding.device_id)
                        & (EntityDeviceBinding.env == env),
                    )
                    .filter(EntityDeviceBinding.device_provider == provider)
                    .distinct()
                )

            # service_status_list 过滤
            if service_status_list:
                query = query.filter(BotPublishModel.status.in_(service_status_list))

            # 去重（仅在 collaborator 查询时需要）
            if collaborator_user_id:
                query = query.distinct()

            total = query.count()
            if total == 0:
                return 0, []

            results = (
                query.order_by(
                    self.Model.gmt_create.desc(),
                    BotPublishModel.gmt_create.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )

            # 组装结果
            items: List[Dict[str, Any]] = []
            if collaborator_user_id:
                # 结果为 (bot, publish, user_role) 三元组
                for bot, publish, user_role in results:
                    item = bot.to_dict()
                    item["publish"] = publish.to_dict() if publish else None
                    item["user_role"] = user_role
                    items.append(item)
            else:
                # 现有逻辑：结果为 (bot, publish) 二元组
                for bot, publish in results:
                    item = bot.to_dict()
                    item["publish"] = publish.to_dict() if publish else None
                    items.append(item)
            return total, items

    def list_active_bots_by_entity(
        self,
        entity_id: str,
        entity_type: Optional[str] = None,
        bot_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.is_delete == 0,
                self.Model.status == "ACTIVE",
                self.Model.binding_id.isnot(None),
                self._env(),
                or_(
                    self.Model.owner_id == entity_id,
                    self.Model.entity_id == entity_id,
                ),
            )
            if entity_type:
                query = query.filter(self.Model.entity_type == entity_type)
            if bot_type:
                query = query.filter(self.Model.bot_type == bot_type)
            return [b.to_dict() for b in query.all()]

    def list_domain_bots(
        self,
        page: int | None = None,
        page_size: int | None = None,
        keyword: str | None = None,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List domain bots (bots with ext.is_domain_bot=true).

        Domain bots are identified by checking the ext JSON field for
        is_domain_bot=true. Supports optional keyword search on bot_name.
        When page/page_size are omitted, returns all matching results.
        """
        env = get_current_env()
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.is_delete == 0,
                self.Model.env == env,
                self.Model.ext.isnot(None),
                func.json_extract(self.Model.ext, "$.is_domain_bot").is_(True),
            )

            if keyword:
                query = query.filter(self.Model.bot_name.ilike(f"%{keyword}%"))

            total = query.count()
            if total == 0:
                return 0, []

            query = query.order_by(self.Model.gmt_create.desc())

            if page is not None and page_size is not None:
                query = query.offset((page - 1) * page_size).limit(page_size)

            return total, [b.to_dict() for b in query.all()]
