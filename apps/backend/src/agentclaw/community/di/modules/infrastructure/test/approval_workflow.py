"""Approval-workflow concern — test / singlebox binding (local noop)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin


class TestApprovalWorkflowModule(Module):
    """test / singlebox: local noop approval workflow."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.antprocess import LocalAntProcessService

        binder.bind(ApprovalWorkflowPlugin, to=LocalAntProcessService, scope=singleton)
