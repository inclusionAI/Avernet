"""Governance notify sender — community binding (no-op).

The community distribution ships no DingTalk channel, so governance
notifications degrade to ``NoopGovernanceNotifySender`` (send methods return
``None``). Corp binds the DingTalk sender instead
(``infrastructure/corp/governance.py``). Corp-free by construction: imports only
``core``.
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.core.economy.governance.contracts.protocols import (
    GovernanceNotifySender,
)
from agentclaw.community.core.economy.governance.services.noop_notify_sender import (
    NoopGovernanceNotifySender,
)


class CommunityGovernanceModule(Module):
    """community: no-op governance notification sender."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            GovernanceNotifySender,
            to=NoopGovernanceNotifySender,
            scope=singleton,
        )
