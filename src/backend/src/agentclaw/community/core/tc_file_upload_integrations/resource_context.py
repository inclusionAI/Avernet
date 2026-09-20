"""Authoritative TC resource context resolved from OCB-owned state."""

from __future__ import annotations

from dataclasses import dataclass
import re

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.protocols.platform import (
    SessionResourceRepositoryProtocol,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
)

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class TcResourceContextSnapshot:
    resource_id: str
    status: str
    deleted: bool
    transfer_id: str
    tenant: str
    bot_uuid: str
    user_id: str
    bot_id: str
    filename: str
    size_bytes: int
    content_sha256: str | None
    session_namespace: str
    session_id: str
    conversation_id: str
    scope_type: str
    group_id: str | None
    members: tuple[dict[str, str], ...]
    session_revision: int
    session_active: bool

    def as_payload(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "status": self.status,
            "deleted": self.deleted,
            "transfer_id": self.transfer_id,
            "tenant": self.tenant,
            "bot_uuid": self.bot_uuid,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "content_sha256": self.content_sha256,
            "session_namespace": self.session_namespace,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "scope_type": self.scope_type,
            "group_id": self.group_id,
            "members": list(self.members),
            "session_revision": self.session_revision,
            "session_active": self.session_active,
        }


class TcResourceContextService:
    """Build the complete ECB snapshot from the authoritative resource row."""

    def __init__(
        self,
        repository: SessionResourceRepositoryProtocol,
        token_vault: TokenVault,
    ) -> None:
        self._repository = repository
        self._token_vault = token_vault

    def resolve(self, resource_id: str) -> TcResourceContextSnapshot:
        normalized = resource_id.strip() if isinstance(resource_id, str) else ""
        if not normalized:
            raise ValueError("resource_id_required")
        record = self._repository.get_by_resource_id(normalized)
        if record is None:
            raise ValueError("resource_not_found")
        return self._snapshot(record)

    def _snapshot(self, record: SessionResourceRecord) -> TcResourceContextSnapshot:
        session_ciphertext = record.session_key_ciphertext or ""
        session_id = self._token_vault.decrypt_or_passthrough(
            session_ciphertext
        ).strip()
        required = {
            "transfer_id": record.transfer_id,
            "tenant": record.tenant,
            "bot_uuid": record.bot_uuid,
            "user_id": record.owner_id,
            "bot_id": record.bot_id,
            "filename": record.filename,
            "session_id": session_id,
        }
        if any(
            not isinstance(value, str) or not value.strip()
            for value in required.values()
        ):
            raise ValueError("resource_context_incomplete")
        if record.size_bytes is None or record.size_bytes < 0:
            raise ValueError("resource_context_incomplete")
        if record.scope_type not in {
            "session",
            "openapi_session",
            "friend_bot_chat",
            "personal_bot_chat",
        }:
            raise ValueError("resource_context_unsupported_scope")

        digest = record.client_content_hash
        content_sha256 = (
            digest.lower() if digest and _SHA256.fullmatch(digest) else None
        )
        deleted = (
            record.deleted_at is not None
            or record.status is SessionResourceStatus.DELETED
        )
        active = record.status is SessionResourceStatus.READY and not deleted
        return TcResourceContextSnapshot(
            resource_id=record.resource_id,
            status=record.status.value,
            deleted=deleted,
            transfer_id=record.transfer_id,
            tenant=record.tenant,
            bot_uuid=record.bot_uuid,
            user_id=record.owner_id,
            bot_id=record.bot_id,
            filename=record.filename,
            size_bytes=record.size_bytes,
            content_sha256=content_sha256,
            session_namespace="tc",
            session_id=session_id,
            conversation_id=session_id,
            scope_type="direct",
            group_id=None,
            members=(),
            session_revision=max(record.task_version, 1),
            session_active=active,
        )
