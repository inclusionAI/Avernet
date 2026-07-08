"""Approval-workflow concern — community binding (no workflow)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin


class CommunityApprovalWorkflowModule(Module):
    """community: no approval workflow (publishing proceeds directly)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.approval_workflow import NoApprovalWorkflow

        binder.bind(ApprovalWorkflowPlugin, to=NoApprovalWorkflow, scope=singleton)
