"""Governance services — domain logic layer.

Each service orchestrates business rules and delegates data access
to the repositories in :mod:`agentclaw.community.core.economy.governance.repositories`.

- :class:`GovernanceBotService` — scan-and-decision orchestrator (scan_service)
- :class:`GovernanceFeedbackService` — user feedback on governance notifications
- :class:`GovernanceAdminService` — backend admin (pause/resume/bulk-whitelist)
- :class:`GovernanceWhitelistService` — whitelist batch add + delete
- :mod:`notify_builder_service` — Markdown + TC card rendering helpers
"""
