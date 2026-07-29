"""Session resource state machine, authorization, and Engine content proxy."""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.session_resources.baas_client import (
    SessionResourceBaasClient,
)
from agentclaw.community.core.session_resources.repository.protocol import (
    SessionResourceRepositoryProtocol,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
    SessionUploadIntent,
    TransferApiVersion,
    hash_identifier,
)
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterStreamResponse,
    DeviceAdapterTransport,
)

log = logging.getLogger("session_resource.service")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._ -]+$")


class SessionResourceService:
    def __init__(
        self,
        *,
        repository: SessionResourceRepositoryProtocol,
        baas_client: SessionResourceBaasClient,
        task_queue: TaskQueueService,
        device_context_resolver: DeviceContextResolver,
        token_vault: TokenVault,
        adapter_transport: DeviceAdapterTransport,
    ) -> None:
        self._repository = repository
        self._baas = baas_client
        self._task_queue = task_queue
        self._resolver = device_context_resolver
        self._vault = token_vault
        self._adapter_transport = adapter_transport

    def create_upload_intent(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        scope_type: str,
        engine_type: str,
        filename: str,
        size_bytes: int | None = None,
        content_hash: str | None = None,
    ) -> SessionUploadIntent:
        safe_filename = self._safe_filename(filename)
        context = self._resolver.resolve_for_bot(bot_id, owner_id)
        tenant = context.conn_info.get("tenant")
        bot_uuid = context.conn_info.get("bot_uuid")
        if not isinstance(tenant, str) or not isinstance(bot_uuid, str):
            raise ValueError("BaaS device identity is unavailable")
        grant = self._baas.create_session_upload_grant(
            tenant=tenant,
            session_id=session_key,
            filename=safe_filename,
            file_size=size_bytes,
            operator=owner_id,
        )
        resource_id = f"sr_{uuid.uuid4().hex}"
        session_hash = hash_identifier(session_key)
        scope_hash = hash_identifier(f"{scope_type}:{owner_id}:{bot_id}")
        relative = (
            f".teamclaw/session-files/{scope_hash}/{session_hash}/"
            f"{resource_id}/{safe_filename}"
        )
        record = self._repository.create(
            SessionResourceRecord(
                resource_id=resource_id,
                owner_id=owner_id,
                bot_id=bot_id,
                scope_type=scope_type,
                scope_key_hash=scope_hash,
                session_key_hash=session_hash,
                engine_type=engine_type,
                tenant=tenant,
                bot_uuid=bot_uuid,
                display_name=filename,
                filename=safe_filename,
                device_path=f"workspace/{relative}",
                workspace_relative_path=relative,
                transfer_id=grant.transfer_id,
                status=SessionResourceStatus.UPLOAD_URL_ISSUED,
                transfer_api_version=TransferApiVersion.SESSION_V2,
                session_key_ciphertext=self._vault.encrypt(session_key),
                size_bytes=size_bytes,
                client_content_hash=content_hash,
            )
        )
        log.info(
            "session_resource.upload_intent.create resource_id=%s session_key_hash=%s file_ext=%s size_bytes=%s upload_type=%s",
            resource_id,
            session_hash[:16],
            Path(safe_filename).suffix.lower(),
            size_bytes,
            grant.upload_type,
        )
        return SessionUploadIntent(resource=record, grant=grant)

    def complete_upload(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        resource_id: str,
        transfer_id: str,
    ) -> SessionResourceRecord:
        session_hash = hash_identifier(session_key)
        current = self._owned(resource_id, owner_id, bot_id, session_hash)
        if current.transfer_id != transfer_id:
            raise ValueError("transfer_id_mismatch")
        if current.status in {
            SessionResourceStatus.DEVICE_SYNCING,
            SessionResourceStatus.READY,
        }:
            return current
        status = self._complete_baas_upload(current)
        if status != "DONE":
            log.warning(
                "session_resource.upload_complete.not_done resource_id=%s status=%s",
                current.resource_id,
                status,
            )
            raise ValueError("transfer_not_done")
        return self._schedule_materialization(current, allow_ready=False)

    def get_status(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        resource_id: str,
    ) -> SessionResourceRecord:
        session_hash = hash_identifier(session_key)
        record = self._owned(resource_id, owner_id, bot_id, session_hash)
        log.info(
            "session_resource.materialize.poll resource_id=%s session_key_hash=%s status=%s task_version=%s",
            record.resource_id,
            session_hash[:16],
            record.status.value,
            record.task_version,
        )
        return record

    def list_resources(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        ready_only: bool = False,
    ) -> list[SessionResourceRecord]:
        records = self._repository.list_owned(
            owner_id,
            bot_id,
            hash_identifier(session_key),
        )
        return [
            record
            for record in records
            if record.status is not SessionResourceStatus.DELETED
            and (not ready_only or record.status is SessionResourceStatus.READY)
        ]

    async def open_content(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        resource_id: str,
        disposition: str,
    ) -> tuple[SessionResourceRecord, DeviceAdapterStreamResponse]:
        if disposition not in {"inline", "attachment"}:
            raise ValueError("invalid_disposition")
        record = self._owned(
            resource_id,
            owner_id,
            bot_id,
            hash_identifier(session_key),
        )
        if record.status is not SessionResourceStatus.READY:
            raise ValueError("resource_not_ready")
        context = self._resolver.resolve_for_bot(record.bot_id, record.owner_id)
        response = await self._adapter_transport.stream(
            context.conn_info,
            "GET",
            f"/api/resource-materializations/{record.resource_id}/content",
            params={"disposition": disposition},
            timeout=60.0,
        )
        if 200 <= response.status_code < 300:
            log.info(
                "session_resource.content.stream.open resource_id=%s status=%s provider=%s",
                record.resource_id,
                response.status_code,
                context.provider,
            )
            return record, response
        await response.close()
        if response.status_code == 409:
            self._queue_rematerialization(record)
            raise ValueError("resource_materializing")
        log.warning(
            "session_resource.content.stream.fail resource_id=%s status=%s provider=%s",
            record.resource_id,
            response.status_code,
            context.provider,
        )
        raise ValueError("engine_content_unavailable")

    def reference(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        resource_id: str,
    ) -> dict:
        record = self.get_status(
            owner_id=owner_id,
            bot_id=bot_id,
            session_key=session_key,
            resource_id=resource_id,
        )
        if record.status is not SessionResourceStatus.READY:
            raise ValueError("resource_not_ready")
        return {
            "resource_id": record.resource_id,
            "display_name": record.display_name,
            "size_bytes": record.size_bytes,
            "content_hash": record.client_content_hash,
        }

    def delete(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        resource_id: str,
    ) -> SessionResourceRecord:
        session_hash = hash_identifier(session_key)
        result = self._repository.soft_delete(
            resource_id,
            owner_id,
            bot_id,
            session_hash,
        )
        if result is None:
            raise ValueError("resource_not_found")
        log.info(
            "session_resource.delete.success resource_id=%s session_key_hash=%s",
            result.resource_id,
            session_hash[:16],
        )
        return result

    def materialized_callback(
        self,
        *,
        resource_id: str,
        transfer_id: str,
        task_id: str,
        task_version: int,
        ready: bool,
        materialized_ref: dict | None,
        error_code: str | None,
    ) -> SessionResourceRecord | None:
        if ready:
            current = self._repository.get_by_resource_id(resource_id)
            if current is None:
                return None
            if (
                not isinstance(materialized_ref, dict)
                or materialized_ref.get("relative_path")
                != current.workspace_relative_path
            ):
                log.warning(
                    "session_resource.materialize.callback.reject resource_id=%s task_version=%s reason=relative_path_mismatch",
                    resource_id,
                    task_version,
                )
                raise ValueError("materialized_ref_mismatch")
        result = self._repository.cas_finish_materialization(
            resource_id=resource_id,
            transfer_id=transfer_id,
            task_id=task_id,
            task_version=task_version,
            ready=ready,
            materialized_ref=materialized_ref,
            error_code=error_code,
        )
        log.info(
            "session_resource.materialize.callback.%s resource_id=%s task_version=%s applied=%s",
            "ready" if ready else "fail",
            resource_id,
            task_version,
            result is not None,
        )
        return result

    def _complete_baas_upload(self, record: SessionResourceRecord) -> str:
        if record.transfer_api_version is TransferApiVersion.SESSION_V2:
            session_id = self._decrypt_session_key(record)
            return self._baas.complete_session_upload(
                tenant=record.tenant,
                session_id=session_id,
                transfer_id=record.transfer_id,
            )
        return self._baas.complete_legacy_upload(
            tenant=record.tenant,
            bot_uuid=record.bot_uuid,
            transfer_id=record.transfer_id,
        )

    def _queue_rematerialization(self, record: SessionResourceRecord) -> None:
        try:
            self._schedule_materialization(record, allow_ready=True)
        except ValueError as exc:
            if str(exc) != "materialize_state_conflict":
                raise
            log.info(
                "session_resource.content.rematerialize.inflight resource_id=%s",
                record.resource_id,
            )

    def _schedule_materialization(
        self,
        record: SessionResourceRecord,
        *,
        allow_ready: bool,
    ) -> SessionResourceRecord:
        task_id = f"mt_{uuid.uuid4().hex}"
        started = self._repository.cas_start_materialization(
            resource_id=record.resource_id,
            owner_id=record.owner_id,
            bot_id=record.bot_id,
            session_key_hash=record.session_key_hash,
            transfer_id=record.transfer_id,
            task_id=task_id,
            allow_ready=allow_ready,
        )
        if started is None:
            raise ValueError("materialize_state_conflict")
        try:
            self._task_queue.enqueue(
                "session_resource.materialize",
                self._task_payload(started),
                deadline_seconds=3600,
            )
        except Exception:
            self._repository.cas_finish_materialization(
                resource_id=started.resource_id,
                transfer_id=started.transfer_id,
                task_id=started.task_id or "",
                task_version=started.task_version,
                ready=False,
                materialized_ref=None,
                error_code="dispatch_failed",
            )
            log.exception(
                "session_resource.materialize.dispatch.fail resource_id=%s task_version=%s",
                record.resource_id,
                started.task_version,
            )
            raise
        log.info(
            "session_resource.materialize.dispatch resource_id=%s task_version=%s transfer_hash=%s source=%s",
            record.resource_id,
            started.task_version,
            hash_identifier(record.transfer_id)[:16],
            record.transfer_api_version.value,
        )
        return started

    def _decrypt_session_key(self, record: SessionResourceRecord) -> str:
        ciphertext = record.session_key_ciphertext
        if not ciphertext:
            raise ValueError("session_key_missing")
        session_id = self._vault.decrypt_or_passthrough(ciphertext)
        if not session_id:
            raise ValueError("session_key_missing")
        return session_id

    def _owned(
        self,
        resource_id: str,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> SessionResourceRecord:
        record = self._repository.get_owned(
            resource_id,
            owner_id,
            bot_id,
            session_key_hash,
        )
        if record is None:
            raise ValueError("resource_not_found")
        return record

    @staticmethod
    def _safe_filename(value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("invalid_filename")
        if not _SAFE_FILENAME.fullmatch(value):
            raise ValueError("invalid_filename")
        return value

    @staticmethod
    def _task_payload(record: SessionResourceRecord) -> dict:
        return {
            "resource_id": record.resource_id,
            "task_id": record.task_id,
            "task_version": record.task_version,
        }
