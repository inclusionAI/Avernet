"""Delivery-facing knowledge content capability, version 1.

Methods describe resource operations, never caller-selected URLs or upstream paths.
The browser adapter authenticates the actor and verifies workspace/KB access first.
The provider MUST enforce native read/write ACLs, exact KB/Source/Job ownership,
replacement hash CAS, upload conflicts, and schedule version CAS. No accepted task
is interpreted as business success. Providers sanitize reply headers and errors.
Raw streams must release resources on exhaustion, cancellation and read failures.

yuque_connection reports only the current actor's saved connector state and the
configured provider authorization entry, never credentials. It does not assert
remote token validity or MCP consent. The browser must recheck this state after
a popup completion notice rather than trusting the notice as a grant.

Knowledge-base deletion is provider-owned: omission of immediate schedules the
configured cancellable grace period; immediate=True skips it. A successful reply
acknowledges the provider's durable intent, not physical resource cleanup. Cancel
uses the provider's current-owner/deadline checks without an active-KB read
preflight, since a pending KB may be hidden. No local task or mutation retry is
introduced; native status, deadline, warning and cleanup projections pass through.

Session material is opaque to public orchestration, request-scoped and never logged.
prepare_access interprets it using deployment-specific authentication; caller-supplied
actor IDs cannot replace the authenticated identity. Unconfigured providers fail closed.
"""

from collections.abc import AsyncIterator, Mapping
from typing import Literal, Protocol, runtime_checkable

from agentclaw.community.kernel.knowledge_content import ContentAccess, ContentReply


@runtime_checkable
class KnowledgeContentServiceProtocol(Protocol):
    def prepare_access(
        self, *, actor_id: str, session: Mapping[str, str]
    ) -> ContentAccess: ...
    async def delete_knowledge_base(
        self, *, kb_id: str, access: ContentAccess, immediate: bool = False
    ) -> ContentReply: ...
    async def cancel_knowledge_base_deletion(
        self, *, kb_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def data_sources(
        self, *, kb_id: str, access: ContentAccess, offset: int, limit: int
    ) -> ContentReply: ...
    async def sources(
        self,
        *,
        kb_id: str,
        access: ContentAccess,
        offset: int,
        limit: int,
        exclude_book_documents: bool = False,
    ) -> ContentReply: ...
    async def source(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def upload(
        self,
        *,
        kb_id: str,
        access: ContentAccess,
        files: list[tuple[str, bytes, str]],
        on_name_conflict: str,
    ) -> ContentReply: ...
    async def replace(
        self,
        *,
        kb_id: str,
        source_id: str,
        access: ContentAccess,
        file: tuple[str, bytes, str],
        expected_content_hash: str,
    ) -> ContentReply: ...
    async def delete(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def raw(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> tuple[ContentReply, AsyncIterator[bytes] | None]: ...
    async def yuque_connection(
        self, *, kb_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def yuque_documents(
        self, *, kb_id: str, access: ContentAccess, query: str, offset: int, limit: int
    ) -> ContentReply: ...
    async def import_yuque(
        self, *, kb_id: str, access: ContentAccess, body: dict
    ) -> ContentReply: ...
    async def yuque_books(
        self,
        *,
        kb_id: str,
        access: ContentAccess,
        query: str,
        offset: int,
        limit: int,
        source: Literal["personal", "team"] | None = None,
        pagination: Literal["cursor"] | None = None,
        cursor: str | None = None,
    ) -> ContentReply: ...
    async def book_imports(
        self, *, kb_id: str, access: ContentAccess, offset: int, limit: int
    ) -> ContentReply: ...
    async def import_book(
        self, *, kb_id: str, access: ContentAccess, body: dict
    ) -> ContentReply: ...
    async def book_import(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def delete_book(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def book_deletion(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def cancel_book_import(
        self, *, kb_id: str, source_id: str, access: ContentAccess, body: dict
    ) -> ContentReply: ...
    async def sync_book(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def book_sync_run(
        self, *, kb_id: str, source_id: str, run_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def get_book_schedule(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def put_book_schedule(
        self, *, kb_id: str, source_id: str, access: ContentAccess, body: dict
    ) -> ContentReply: ...
    async def sync(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def sync_run(
        self, *, kb_id: str, source_id: str, run_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def get_schedule(
        self, *, kb_id: str, source_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def put_schedule(
        self, *, kb_id: str, source_id: str, access: ContentAccess, body: dict
    ) -> ContentReply: ...
    async def tasks(
        self, *, kb_id: str, access: ContentAccess, offset: int, limit: int
    ) -> ContentReply: ...
    async def revision(
        self, *, kb_id: str, revision_id: str, access: ContentAccess
    ) -> ContentReply: ...
    async def activities(
        self, *, kb_id: str, access: ContentAccess, offset: int, limit: int
    ) -> ContentReply: ...
    async def job(
        self, *, kb_id: str, job_id: str, access: ContentAccess
    ) -> ContentReply: ...
