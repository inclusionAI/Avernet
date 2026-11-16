"""Compatibility only: primitive SQL lives in L4, product composition in L6."""

from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    AppendEventRecordRequest as AppendEventRecordRequest,
    ContextCollabRepository as ContextCollabRepository,
    ContextCollabRepositoryProtocol as ContextCollabRepositoryProtocol,
    ContextCreatedRecord as ContextCreatedRecord,
    ContextStatusRecord as ContextStatusRecord,
    CreateColletRecordRequest as CreateColletRecordRequest,
    EventAppendedRecord as EventAppendedRecord,
    InitialEventDraft as InitialEventDraft,
)
