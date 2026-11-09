"""Recycling journal and transactional completion over existing tables only."""

from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

from agentclaw.community.core.bot_management.engines.aicoding.recycle_state import (
    BLOCKING,
    RecycleSuperseded,
    RecycleTargetChanged,
    journal,
)
from agentclaw.community.core.bot_dormant.sqlite_models import DormantCheckAudit
from agentclaw.community.core.bot_management.engines.registry import assert_lifecycle_mutable
from agentclaw.community.core.bot_management.owner_handoff.guard import (
    assert_bot_mutable,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
)
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.repository.protocols.bot.recycle import (
    BotRecycleRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def _ext(row):
    return json.loads(row.ext) if row.ext else {}


class RecycleRepository(BotRecycleRepositoryProtocol):
    """All writes lock the same Bot row, scope by tenant/env/owner and merge ext.

    A database error propagates; it must never be downgraded to backup success.
    The provider is called outside transactions. Completion/audit commit together.
    """

    def __init__(self, db: DatabasePlugin, app: str, *, is_teclaw):
        self.db, self.app, self.is_teclaw = db, app, is_teclaw

    def _row(self, session, bot_id, owner_id, *, lock=False):
        q = session.query(BotModel).filter(
            BotModel.bot_id == bot_id,
            BotModel.owner_id == owner_id,
            BotModel.env == get_current_env(),
            BotModel.is_delete == 0,
            BotModel.avernet_tenant == get_current_avernet_tenant(),
        )
        row = (q.with_for_update() if lock else q).one_or_none()
        if row is None:
            raise BotNotFoundError("Bot not found")
        if lock:
            assert_bot_mutable({"ext": row.ext})
        return row

    def read(self, bot_id, owner_id):
        with self.db.orm_session() as session:
            return self._row(session, bot_id, owner_id).to_dict()

    def _check(self, row, payload):
        record = journal(row.to_dict())
        if (
            row.id != payload["bot_pk"]
            or record.get("operation_id") != payload["operation_id"]
            or record.get("app") != self.app
        ):
            raise RecycleSuperseded("Recycle operation changed")
        return record

    def _matches(self, session, row, target, engine):
        if row.binding_id != target["binding_id"] or row.active_engine != engine:
            return False
        if row.binding_id is None:
            return True
        binding = (
            session.query(EntityDeviceBinding)
            .filter(
                EntityDeviceBinding.id == row.binding_id,
                EntityDeviceBinding.env == row.env,
            )
            .with_for_update()
            .one_or_none()
        )
        if binding is None:
            return False
        props = json.loads(binding.device_props or "{}")
        return (
            binding.device_id == target["device_id"]
            and binding.device_provider == target["provider"]
            and binding.entity_id == row.entity_id
            and props.get("sandbox_id") == target.get("sandbox_id")
            and (props.get("bot_uuid") or binding.device_id) == target.get("bot_uuid")
        )

    def ensure(self, payload, task_id):
        with self.db.transactional_orm_session() as session:
            row = self._row(session, payload["bot_id"], payload["owner_id"], lock=True)
            ext = _ext(row)
            old = ext.get("recycle") or {}
            if old.get("operation_id") == payload["operation_id"]:
                return deepcopy(old)
            if old.get("phase") in BLOCKING or int(old.get("task_id") or 0) >= task_id:
                raise RecycleSuperseded("Another recycle owns this Bot")
            restart = ext.get("coding_restart") or {}
            valid = (
                row.id == payload["bot_pk"]
                and row.status == "ACTIVE"
                and restart.get("phase")
                not in {"QUEUED", "BACKING_UP", "RESTARTING", "WAITING_READY"}
                and self._matches(session, row, payload["target"], payload["engine"])
            )
            record = {
                **deepcopy(payload),
                "task_id": task_id,
                "phase": "QUEUED" if valid else "FAILED",
                "error_code": None if valid else "TARGET_CHANGED",
                "message": None if valid else "Bot 状态或目标已变化，未执行回收",
            }
            ext["recycle"] = record
            row.ext = json.dumps(ext, ensure_ascii=False)
            return deepcopy(record)

    def update(self, payload, phases, *, expected=None, **changes):
        with self.db.transactional_orm_session() as session:
            row = self._row(session, payload["bot_id"], payload["owner_id"], lock=True)
            record = self._check(row, payload)
            if record.get("phase") not in phases or any(
                record.get(k) != v for k, v in (expected or {}).items()
            ):
                raise RecycleSuperseded("Recycle phase changed")
            # Once release intent is persisted, target checks protect every later
            # write. A replacement must never inherit this operation's outcome.
            if changes.get("phase") == "RELEASING" and (
                row.status != "ACTIVE"
                or not self._matches(session, row, record["target"], record["engine"])
            ):
                raise RecycleTargetChanged("Recycle target changed before release")
            # Concurrent/redelivered backup workers cannot reset the budget or
            # policy, or forget physical targets already confirmed by a peer.
            if "backup_started_at" in record:
                changes.pop("backup_started_at", None)
            if "fail_block" in record:
                changes.pop("fail_block", None)
            if "fastdisk_targets" in changes:
                changes["fastdisk_targets"] = sorted(
                    set(record.get("fastdisk_targets", []))
                    | set(changes["fastdisk_targets"])
                )
            if (
                "physical_targets" in changes
                and "physical_targets" in record
                and changes["physical_targets"] != record["physical_targets"]
            ):
                raise RecycleTargetChanged("Physical recycle targets changed")
            record.update(deepcopy(changes))
            ext = _ext(row)
            ext["recycle"] = record
            row.ext = json.dumps(ext, ensure_ascii=False)
            return deepcopy(record)

    def finish(self, payload, finalizer):
        with self.db.transactional_orm_session() as session:
            row = self._row(session, payload["bot_id"], payload["owner_id"], lock=True)
            record = self._check(row, payload)
            if record["phase"] == "SUCCEEDED":
                return
            if (
                record["phase"] != "RELEASING"
                or record.get("finalizer") != finalizer
                or not record.get("freeze_done")
                or not record.get("release_confirmed")
                or row.status != "ACTIVE"
                or not self._matches(session, row, record["target"], record["engine"])
            ):
                raise RecycleTargetChanged("Recycle completion no longer owns target")
            if row.binding_id is not None:
                binding = (
                    session.query(EntityDeviceBinding)
                    .filter(
                        EntityDeviceBinding.id == row.binding_id,
                        EntityDeviceBinding.env == row.env,
                    )
                    .one()
                )
                binding.status = "RELEASED"
                binding.release_reason = "openapi recycle"
                binding.released_by = row.owner_id
                binding.released_at = datetime.now()
            # This transaction is the sole OpenAPI recycle audit writer. A retry
            # sees SUCCEEDED under the row lock and never writes a second audit.
            session.add(
                DormantCheckAudit(
                    run_id=payload["operation_id"],
                    bot_id=row.bot_id,
                    owner_id=row.owner_id,
                    check_result="manual",
                    action_taken="recycled",
                    source="openapi",
                    dry_run=0,
                )
            )
            row.status, row.binding_id, row.device_id = "RECYCLED", None, None
            record.update(phase="SUCCEEDED", finished_at=datetime.now().isoformat())
            ext = _ext(row)
            ext["recycle"] = record
            row.ext = json.dumps(ext, ensure_ascii=False)

    def claim_activation(self, bot_id, owner_id):
        """Atomically claim RECYCLED; all activation entrypoints share this claim."""
        with self.db.transactional_orm_session() as session:
            row = self._row(session, bot_id, owner_id, lock=True)
            bot = row.to_dict()
            if row.bot_type != "personal" or self.is_teclaw(row.active_engine):
                raise BotOperationNotAllowedError(
                    "Only personal managed-cloud Bots can be activated"
                )
            assert_lifecycle_mutable(bot)
            if row.status in {"ACTIVE", "REACTIVATING"}:
                return row.status, False
            if row.status != "RECYCLED":
                raise BotInvalidLifecycleStateError(
                    bot_id=bot_id, current_status=row.status
                )
            row.status = "REACTIVATING"
            return "REACTIVATING", True

    @contextmanager
    def activation_rollback(self, bot_id, owner_id):
        """Keep a new activation from interleaving with a Passport rollback.

        Only a pre-allocation REACTIVATING record may be rolled back. Late
        allocation failures cannot freeze a new/serving binding.
        """
        with self.db.transactional_orm_session() as session:
            row = self._row(session, bot_id, owner_id, lock=True)
            allowed = row.status == "REACTIVATING" and row.binding_id is None
            if allowed:
                assert_lifecycle_mutable(row.to_dict())
            yield allowed
            if allowed:
                row.status = "RECYCLED"
