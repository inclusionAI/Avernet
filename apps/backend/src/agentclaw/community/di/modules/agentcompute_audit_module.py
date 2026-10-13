"""DI wiring for the agentcompute append-only audit writer (task 12.7).

Lives in its own module — separate from ``AgentComputeModule`` — because
that module is edited by other work waves in parallel; the audit writer
takes the shared ``DatabasePlugin`` seam exactly like the governance audit
repository, so this module needs no infrastructure of its own.
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.core.agentcompute.repository.audit.impl import AgentComputeAuditWriter
from agentclaw.community.plugin_api.database import DatabasePlugin


class AgentComputeAuditModule(Module):

    @provider
    @singleton
    @inject
    def provide_audit_writer(self, db: DatabasePlugin) -> AgentComputeAuditWriter:
        return AgentComputeAuditWriter(db)