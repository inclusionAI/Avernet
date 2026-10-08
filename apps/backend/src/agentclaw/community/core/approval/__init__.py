"""Approval-workflow core helpers.

The public approval capability is a Plugin Protocol at
``agentclaw.community.plugin_api.approval_workflow`` (``ApprovalWorkflowPlugin``); the corp
and community implementations live under ``plugins/``.

This package now holds only the neutral callback handler. The lower-level vendor
facade types moved to the prod plugin overlay (excluded from the open-source
build) so core carries no vendor import or vendor DTO fields.
"""
