"""Unified BotFriend repository (prod OceanBase + local SQLite).

One ORM implementation behind ``BotFriendRepositoryProtocol``. The
only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

Prod-twin parity (the raw-SQL ``ZdasBotFriendRepository`` is the
reference):

- ``insert`` is a **plain INSERT** (``db.add`` + ``db.flush``), never
  an upsert despite ``uk_entity_bot_id_env`` (friend records are
  append-only; prod has no ``ON DUPLICATE KEY``). ``env`` is forced
  to ``get_current_env()`` (prod ignores any ``data["env"]``); ``ext``
  stored as a JSON string.
- **Env-scoping (adopt prod):** every query and the two ``_update_*``
  filter ``env == get_current_env()``. The old SQLite twin did NO env
  filtering at all — that divergence is dropped (visible behavior
  change vs the old local twin; flagged in the PR).
- ``accept`` / ``reject`` / ``cancel`` / ``update_approval_status`` /
  ``create_approval`` keep prod's exact shape: ``get_by_id`` →
  mutate the approval list in Python → ``_update_status_and_ext`` /
  ``_update_ext``. Each helper is its own statement (prod opens a
  fresh connection per call — faithfully reproduced; not artificially
  fused into one transaction).
- ``soft_delete`` routes to ``cancel`` (status → CANCELLED) — it is
  **never** a hard DELETE (prod parity; the table has no delete path).
- ``_update_ext`` / ``_update_status_and_ext`` are **single
  conditional UPDATEs** (``WHERE id AND env``) with
  ``gmt_modified=func.now()``; ``rowcount == 0 → None`` (no
  SELECT-first). The old SQLite twin did read-modify-write with no
  env filter — dropped.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import and_, func, or_

from agentclaw.community.core.repository.protocols.bot import BotFriendRepositoryProtocol
from agentclaw.community.core.bot_public.repository.models import (
    ApprovalStatus,
    ApprovalType,
    BotFriendApproval,
    BotFriendModel,
    BotFriendQueryKey,
    BotFriendStatus,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class BotFriendRepository(
    BotFriendRepositoryProtocol,
):
    """Unified ORM ``BotFriendRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self.Model = BotFriendModel

    # ── insert (plain INSERT — never an upsert) ─────────────────

    def insert(self, data: Dict[str, Any]) -> Dict[str, Any]:
        ext = data.get("ext")
        ext_json = json.dumps(ext) if ext is not None else None
        with self._db.orm_session() as db:
            row = self.Model(
                requester_entity_id=data["requester_entity_id"],
                requester_name=data.get("requester_name"),
                target_entity_id=data["target_entity_id"],
                target_name=data.get("target_name"),
                target_bot_id=data["target_bot_id"],
                target_owner_name=data.get("target_owner_name"),
                status=data.get("status", BotFriendStatus.PENDING),
                ext=ext_json,
                env=get_current_env(),
            )
            db.add(row)
            db.flush()
            return row.to_dict()

    # ── queries (env-scoped — adopt prod) ───────────────────────

    def get_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.id == record_id,
                    self.Model.env == get_current_env(),
                )
                .first()
            )
            return row.to_dict() if row else None

    def get_by_entity_ids_batch(
        self, keys: List[BotFriendQueryKey]
    ) -> List[Dict[str, Any]]:
        if not keys:
            return []
        with self._db.orm_session() as db:
            conditions = [
                and_(
                    self.Model.requester_entity_id == k.requester_entity_id,
                    self.Model.target_entity_id == k.target_entity_id,
                    self.Model.target_bot_id == k.target_bot_id,
                )
                for k in keys
            ]
            rows = (
                db.query(self.Model)
                .filter(
                    or_(*conditions),
                    self.Model.env == get_current_env(),
                )
                .order_by(self.Model.gmt_create.desc())
                .all()
            )
            return [r.to_dict() for r in rows]

    def get_by_entity_ids(
        self,
        requester_entity_id: str,
        target_entity_id: str,
        target_bot_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.requester_entity_id == requester_entity_id,
                self.Model.target_entity_id == target_entity_id,
                self.Model.env == get_current_env(),
            )
            if target_bot_id:
                query = query.filter(
                    self.Model.target_bot_id == target_bot_id
                )
            row = query.order_by(self.Model.gmt_create.desc()).first()
            return row.to_dict() if row else None

    def list_by_requester(
        self,
        requester_entity_id: str,
        status: Optional[str] = None,
        target_name: Optional[str] = None,
        target_owner_name: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.requester_entity_id == requester_entity_id,
                self.Model.env == get_current_env(),
            )
            if status:
                query = query.filter(self.Model.status == status)
            if target_name or target_owner_name:
                ors = []
                if target_name:
                    ors.append(
                        self.Model.target_name.like(f"%{target_name}%")
                    )
                if target_owner_name:
                    ors.append(
                        self.Model.target_owner_name.like(
                            f"%{target_owner_name}%"
                        )
                    )
                query = query.filter(or_(*ors))
            total = query.count()
            rows = (
                query.order_by(self.Model.gmt_create.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [r.to_dict() for r in rows]

    def list_by_target(
        self,
        target_entity_id: str,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.target_entity_id == target_entity_id,
                self.Model.env == get_current_env(),
            )
            if status:
                query = query.filter(self.Model.status == status)
            total = query.count()
            rows = (
                query.order_by(self.Model.gmt_create.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [r.to_dict() for r in rows]

    def list_by_target_bot_id(
        self,
        target_bot_id: str,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.target_bot_id == target_bot_id,
                self.Model.env == get_current_env(),
            )
            if status:
                query = query.filter(self.Model.status == status)
            total = query.count()
            rows = (
                query.order_by(self.Model.gmt_create.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [r.to_dict() for r in rows]

    def list_approved_friends_for_bot(
        self,
        bot_id: str,
        owner_id: str,
        page_size: int = 1000,
    ) -> list[dict[str, Any]]:
        approved: list[dict[str, Any]] = []
        page = 1
        while True:
            with self._db.orm_session() as db:
                rows = (
                    db.query(self.Model)
                    .filter(
                        self.Model.target_bot_id == bot_id,
                        self.Model.target_entity_id == owner_id,
                        self.Model.status == BotFriendStatus.ACCEPTED,
                        self.Model.env == get_current_env(),
                    )
                    .order_by(self.Model.gmt_create.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                    .all()
                )
                for r in rows:
                    record = r.to_dict()
                    ext = record.get("ext") or {}
                    if isinstance(ext, str):
                        try:
                            ext = json.loads(ext)
                        except json.JSONDecodeError:
                            ext = {}
                    approvals = ext.get("approvals", [])
                    if not approvals:
                        continue
                    latest = approvals[-1]
                    if (
                        latest.get("approval_type") == ApprovalType.MANUAL
                        and latest.get("status")
                        == ApprovalStatus.APPROVED
                    ):
                        approved.append(record)
                if len(rows) < page_size:
                    break
                page += 1
        return approved

    # ── status transitions (get → mutate → _update_*) ───────────

    def accept(
        self,
        record_id: int,
        approval_uuid: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_by_id(record_id)
        if not existing:
            return None
        approvals = self._get_approvals_from_ext(existing.get("ext"))
        target_uuid = approval_uuid or (
            approvals[-1].get("uuid") if approvals else None
        )
        for approval in approvals:
            if approval.get("uuid") == target_uuid:
                approval["status"] = ApprovalStatus.APPROVED
                approval["approval_time"] = _now_iso()
                break
        new_ext = self._set_approvals_to_ext(
            existing.get("ext"), approvals
        )
        if ext:
            new_ext.update(ext)
        return self._update_status_and_ext(
            record_id, BotFriendStatus.ACCEPTED, new_ext
        )

    def reject(
        self,
        record_id: int,
        reject_reason: Optional[str] = None,
        approval_uuid: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_by_id(record_id)
        if not existing:
            return None
        approvals = self._get_approvals_from_ext(existing.get("ext"))
        target_uuid = approval_uuid or (
            approvals[-1].get("uuid") if approvals else None
        )
        for approval in approvals:
            if approval.get("uuid") == target_uuid:
                approval["status"] = ApprovalStatus.REJECTED
                approval["approval_time"] = _now_iso()
                if reject_reason:
                    approval["reject_reason"] = reject_reason
                break
        new_ext = self._set_approvals_to_ext(
            existing.get("ext"), approvals
        )
        if ext:
            new_ext.update(ext)
        return self._update_status_and_ext(
            record_id, BotFriendStatus.REJECTED, new_ext
        )

    def cancel(
        self,
        record_id: int,
        approval_uuid: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_by_id(record_id)
        if not existing:
            return None
        approvals = self._get_approvals_from_ext(existing.get("ext"))
        target_uuid = approval_uuid or (
            approvals[-1].get("uuid") if approvals else None
        )
        for approval in approvals:
            if approval.get("uuid") == target_uuid:
                approval["status"] = ApprovalStatus.CANCELLED
                approval["approval_time"] = _now_iso()
                break
        new_ext = self._set_approvals_to_ext(
            existing.get("ext"), approvals
        )
        if ext:
            new_ext.update(ext)
        return self._update_status_and_ext(
            record_id, BotFriendStatus.CANCELLED, new_ext
        )

    def soft_delete(self, record_id: int) -> bool:
        result = self.cancel(record_id)
        return result is not None

    # ── approvals (stored in ext) ───────────────────────────────

    def create_approval(
        self, record_id: int, approval_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_by_id(record_id)
        if not existing:
            return None
        approvals = self._get_approvals_from_ext(existing.get("ext"))
        approval = BotFriendApproval(
            uuid=approval_data["uuid"],
            approval_type=approval_data.get(
                "approval_type", ApprovalType.MANUAL
            ),
            require_approval=approval_data.get("require_approval", True),
            approval_url=approval_data.get("approval_url"),
            request_message=approval_data.get("request_message"),
            approval_time=None,
            reject_reason=None,
            status=ApprovalStatus.PENDING,
        )
        approvals.append(approval.to_dict())
        new_ext = self._set_approvals_to_ext(
            existing.get("ext"), approvals
        )
        return self._update_ext(record_id, new_ext)

    def get_approval_by_uuid(
        self, record_id: int, approval_uuid: str
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_by_id(record_id)
        if not existing:
            return None
        for approval in self._get_approvals_from_ext(
            existing.get("ext")
        ):
            if approval.get("uuid") == approval_uuid:
                return approval
        return None

    def update_approval_status(
        self,
        record_id: int,
        approval_uuid: str,
        new_status: str,
        reject_reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_by_id(record_id)
        if not existing:
            return None
        approvals = self._get_approvals_from_ext(existing.get("ext"))
        found = False
        for approval in approvals:
            if approval.get("uuid") == approval_uuid:
                approval["status"] = new_status
                approval["approval_time"] = _now_iso()
                if reject_reason:
                    approval["reject_reason"] = reject_reason
                found = True
                break
        if not found:
            return None
        new_ext = self._set_approvals_to_ext(
            existing.get("ext"), approvals
        )
        friend_status = (
            BotFriendStatus.ACCEPTED
            if new_status == ApprovalStatus.APPROVED
            else new_status
        )
        return self._update_status_and_ext(
            record_id, friend_status, new_ext
        )

    def get_latest_approval(
        self, record_id: int
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_by_id(record_id)
        if not existing:
            return None
        approvals = self._get_approvals_from_ext(existing.get("ext"))
        return approvals[-1] if approvals else None

    def list_approvals(
        self, record_id: int
    ) -> List[Dict[str, Any]]:
        existing = self.get_by_id(record_id)
        if not existing:
            return []
        return self._get_approvals_from_ext(existing.get("ext"))

    # ── helpers ─────────────────────────────────────────────────

    def _get_approvals_from_ext(
        self, ext: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not ext:
            return []
        approvals = ext.get("approvals", [])
        return approvals if isinstance(approvals, list) else []

    def _set_approvals_to_ext(
        self,
        ext: Optional[Dict[str, Any]],
        approvals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if ext is None:
            ext = {}
        ext["approvals"] = approvals
        return ext

    def _update_ext(
        self, record_id: int, ext: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        ext_json = json.dumps(ext) if ext else None
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.id == record_id,
                    self.Model.env == get_current_env(),
                )
                .update(
                    {
                        self.Model.ext: ext_json,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
        if rowcount == 0:
            return None
        return self.get_by_id(record_id)

    def _update_status_and_ext(
        self, record_id: int, status: str, ext: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        ext_json = json.dumps(ext) if ext else None
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.id == record_id,
                    self.Model.env == get_current_env(),
                )
                .update(
                    {
                        self.Model.status: status,
                        self.Model.ext: ext_json,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
        if rowcount == 0:
            return None
        return self.get_by_id(record_id)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()
