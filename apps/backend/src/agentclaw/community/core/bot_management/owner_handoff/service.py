"""Recoverable handoff coordinator. Every worker invocation performs one phase."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from injector import inject

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.notifications import (
    notify_bot_manager_sync,
)
from agentclaw.community.core.execution_identity.protocols import (
    RuntimePassportTokenUpdaterProtocol,
)
from agentclaw.community.core.bot_management.owner_handoff.contracts import (
    HandoffError,
    OwnerHandoffPolicy,
    OwnerHandoffRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.kernel.bot_manager_sync import SyncReason
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin

TASK_TYPE = "bot_owner_handoff"
PUBLIC_FIELDS = {
    "operation_id",
    "bot_pk",
    "bot_id",
    "entity_id",
    "old_owner_id",
    "new_owner_id",
    "actor_id",
    "phase",
    "execution_workno",
    "error_code",
    "gmt_create",
    "gmt_modified",
}


class OwnerHandoffService:
    @inject
    def __init__(
        self,
        repository: OwnerHandoffRepositoryProtocol,
        passport: PassportPlugin,
        runtime: RuntimePassportTokenUpdaterProtocol,
        staff: StaffDeptPlugin,
        bots: BotRepository,
        queue: TaskQueueService,
        policy: OwnerHandoffPolicy,
        manager_sync: BotManagerSyncProtocol,
    ):
        self._repo, self._passport, self._runtime = repository, passport, runtime
        self._staff, self._queue, self._policy = staff, queue, policy
        self._bots = bots
        self._manager_sync = manager_sync

    @staticmethod
    def _public(row):
        return {key: row[key] for key in PUBLIC_FIELDS}

    def _enqueue(self, row):
        self._queue.enqueue(
            TASK_TYPE,
            {
                "operation_id": row["operation_id"],
                "avernet_tenant": row["avernet_tenant"],
            },
            deadline_seconds=86400,
            idempotency_key=f"handoff:{row['avernet_tenant']}:{row['operation_id']}",
        )

    def create(self, *, bot_pk, actor_id, new_owner_id, request_key):
        if not self._policy.enabled:
            raise HandoffError("OWNER_HANDOFF_DISABLED")
        if (
            not new_owner_id.strip()
            or new_owner_id != new_owner_id.strip()
            or len(new_owner_id) > 1024
        ):
            raise HandoffError("INVALID_NEW_OWNER")
        if (
            not request_key
            or len(request_key) > 64
            or request_key != request_key.strip()
        ):
            raise HandoffError("INVALID_REQUEST_KEY")
        bot = self._bots.get_by_pk(bot_pk)
        if bot is None:
            raise HandoffError("BOT_NOT_FOUND")
        # A directory record is identity validation, NOT proof of employment status.
        profile = self._staff.get_user_by_work_no(work_no=new_owner_id)
        name = profile.display_name or profile.full_name or profile.username
        if profile.work_no != new_owner_id or not name:
            raise HandoffError("NEW_OWNER_NOT_FOUND")
        # Capability negotiation precedes freezing. Old deployments must not
        # silently ignore the correlation ID or selected-generation fence.
        if not self._passport.owner_handoff_supported(
            bot_id=bot["bot_id"],
            owner_workno=bot["owner_id"],
            entity_id=bot["entity_id"],
        ):
            raise HandoffError("PASSPORT_HANDOFF_UNSUPPORTED")
        row = self._repo.begin(
            bot_pk=bot_pk,
            actor_id=actor_id,
            new_owner_id=new_owner_id,
            new_owner_name=name,
            request_key=request_key,
            administrators=self._policy.administrators,
        )
        if row["phase"] not in {"SUCCEEDED", "CANCELLED"}:
            self._enqueue(row)
        return self._public(row)

    def get(self, *, operation_id, actor_id):
        if not self._policy.enabled and not self._policy.recovery_enabled:
            raise HandoffError("OWNER_HANDOFF_DISABLED")
        row = self._repo.get(operation_id)
        if (
            actor_id not in {row["actor_id"], row["new_owner_id"], row["old_owner_id"]}
            and actor_id not in self._policy.administrators
        ):
            raise HandoffError("HANDOFF_ACCESS_DENIED")
        return self._public(row)

    def retry(self, *, operation_id, actor_id):
        row = self.get(operation_id=operation_id, actor_id=actor_id)
        if not self._policy.recovery_enabled:
            raise HandoffError("HANDOFF_RECOVERY_DISABLED")
        if row["phase"] not in {"SUCCEEDED", "CANCELLED"}:
            self._enqueue(self._repo.get(operation_id))
        return row

    def cancel(self, *, operation_id, actor_id):
        self.get(operation_id=operation_id, actor_id=actor_id)
        self._repo.cancel(operation_id, actor_id, self._policy.administrators)
        return self.get(operation_id=operation_id, actor_id=actor_id)

    def authorization(self, *, operation_id, actor_id):
        if not self._policy.enabled and not self._policy.recovery_enabled:
            raise HandoffError("OWNER_HANDOFF_DISABLED")
        row = self._repo.get(operation_id)
        if actor_id != row["execution_workno"]:
            raise HandoffError("HANDOFF_ACCESS_DENIED")
        if row["phase"] != "WAITING_AUTHORIZATION":
            raise HandoffError("HANDOFF_AUTHORIZATION_NOT_PENDING")
        self._selected(row)
        auth = (
            self._passport.query_auth_status(
                row["bot_id"], row["old_owner_id"], entity_id=row["entity_id"]
            )
            or {}
        )
        self._selected(row)
        return {key: auth.get(key) for key in ("status", "redirect_url", "iframe_url")}

    def reconcile(self):
        if self._policy.recovery_enabled:
            for row in self._repo.recoverable():
                self._enqueue(row)

    def _metadata(self, row):
        return (
            self._passport.query_agent_passport(
                row["bot_id"], row["old_owner_id"], entity_id=row["entity_id"]
            )
            or {}
        )

    @staticmethod
    def _matches(row, passport):
        return (
            passport.get("handoff_operation_id") == row["operation_id"]
            and passport.get("execution_workno") == row["execution_workno"]
            and bool(passport.get("credential_id"))
            and passport.get("credential_id") != row["old_credential_id"]
        )

    def _selected(self, row):
        metadata = self._metadata(row)
        if (
            not self._matches(row, metadata)
            or metadata.get("credential_id") != row["credential_id"]
        ):
            raise HandoffError("PASSPORT_GENERATION_CONFLICT")
        return metadata

    def step(self, operation_id):
        if not self._policy.recovery_enabled:
            return False
        worker = uuid4().hex
        row = self._repo.claim(operation_id, worker)
        if row is None:
            return self._repo.get(operation_id)["phase"] in {"SUCCEEDED", "CANCELLED"}
        phase = row["phase"]
        try:
            if phase == "QUEUED":
                old = self._metadata(row)
                old_id = old.get("credential_id")
                if not old_id:
                    raise HandoffError("CURRENT_PASSPORT_REQUIRED")
                # The durable intent is committed BEFORE the remote call. A
                # subsequent invocation only reconciles; it never reissues.
                self._repo.advance(
                    operation_id,
                    worker,
                    "REISSUING",
                    old_credential_id=str(old_id),
                    release=False,
                )
                self._passport.reissue_agent_credentials(
                    bot_id=row["bot_id"],
                    owner_workno=row["new_owner_id"],
                    entity_id=row["entity_id"],
                    execution_workno=row["execution_workno"],
                    handoff_operation_id=operation_id,
                    expected_credential_id=str(old_id),
                )
                self._repo.advance(operation_id, worker, "REISSUING", error_code=None)
            elif phase == "REISSUING":
                current = self._metadata(row)
                if self._matches(row, current):
                    self._repo.advance(
                        operation_id,
                        worker,
                        "WAITING_AUTHORIZATION",
                        credential_id=str(current["credential_id"]),
                        authorization_id=current.get("authorization_id"),
                        agent_id=current.get("agent_id"),
                        error_code=None,
                    )
                else:
                    self._repo.advance(
                        operation_id,
                        worker,
                        phase,
                        error_code="PASSPORT_RESULT_UNKNOWN",
                    )
            elif phase == "WAITING_AUTHORIZATION":
                self._selected(row)
                auth = (
                    self._passport.query_auth_status(
                        row["bot_id"], row["old_owner_id"], entity_id=row["entity_id"]
                    )
                    or {}
                )
                self._selected(row)  # Fence the separate token/status read.
                ready = auth.get("status") == "ISSUED" and bool(auth.get("token"))
                self._repo.advance(
                    operation_id,
                    worker,
                    "UPDATING_RUNTIME" if ready else phase,
                    error_code=None if ready else "PASSPORT_AUTHORIZATION_PENDING",
                )
            elif phase == "UPDATING_RUNTIME":
                self._selected(row)
                auth = (
                    self._passport.query_auth_status(
                        row["bot_id"], row["old_owner_id"], entity_id=row["entity_id"]
                    )
                    or {}
                )
                self._selected(row)
                if auth.get("status") != "ISSUED" or not auth.get("token"):
                    raise HandoffError("PASSPORT_AUTHORIZATION_PENDING")
                result = self._runtime.hot_update_passport_token_to_device(
                    bot_id=row["bot_id"],
                    user_id=row["old_owner_id"],
                    token=str(auth["token"]),
                )
                bindings = result.get("bindings")
                if (
                    not isinstance(bindings, list)
                    or not bindings
                    or result.get("skipped")
                    or any(
                        not isinstance(binding, dict)
                        or type(binding.get("device_count")) is not int
                        or binding["device_count"] < 1
                        for binding in bindings
                    )
                ):
                    raise HandoffError("RUNTIME_ACK_REQUIRED")
                self._repo.advance(
                    operation_id, worker, "RETIRING_OLD_CREDENTIALS", error_code=None
                )
            elif phase == "RETIRING_OLD_CREDENTIALS":
                self._selected(row)
                self._passport.retire_agent_credentials(
                    bot_id=row["bot_id"],
                    owner_workno=row["new_owner_id"],
                    entity_id=row["entity_id"],
                    keep_credential_id=row["credential_id"],
                    handoff_operation_id=operation_id,
                )
                self._repo.advance(
                    operation_id, worker, "COMMITTING_OWNER", error_code=None
                )
            elif phase == "COMMITTING_OWNER":
                self._selected(row)
                self._repo.finish(operation_id, worker)
                # The owner change is durable — BCS's stale manager set must
                # be refreshed via the side-channel only. The shared helper
                # contains the notification failure, so it cannot reach the
                # outer error-state handling, re-enter a Passport admin path,
                # or call a BCS owner transfer.
                notify_bot_manager_sync(
                    self._manager_sync,
                    bot_pk=row["bot_pk"],
                    env=row["env"],
                    avernet_tenant=row["avernet_tenant"],
                    reason=SyncReason.OWNER_HANDOFF,
                )
                return True
            else:
                raise HandoffError("INVALID_HANDOFF_PHASE")
        except Exception as exc:
            # Provider errors may contain credentials or authorization links.
            # Persist only our enumerated local codes; never log raw exceptions.
            code = (
                str(exc) if isinstance(exc, HandoffError) else "HANDOFF_EXTERNAL_ERROR"
            )
            current = self._repo.get(operation_id)
            if current["worker"] == worker:
                self._repo.advance(
                    operation_id, worker, current["phase"], error_code=code
                )
            return False
        return False
