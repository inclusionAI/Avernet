"""Rule 12/25 conformance for the Bot capability authorization hook slot."""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.skill_center.authorization_hook import (
    BotCapabilityAuthorizationHookProtocol,
    CollaboratorBotCapabilityAuthorizationHook,
)


class _Collaborators:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple] = []

    def check_collaborator_permission(self, *args, explicit: bool = False):
        # The edit-domain seam: capability mutations key off the explicit
        # ladder, so the contract records the flag the hook requests.
        self.calls.append((*args, explicit))
        return {"has_permission": self.allowed}


@pytest.fixture(params=[CollaboratorBotCapabilityAuthorizationHook])
def hook_and_policy(request):
    """Every registered hook implementation must pass the same contract."""
    policy = _Collaborators(allowed=True)
    hook: BotCapabilityAuthorizationHookProtocol = request.param(policy)
    return hook, policy


def test_owner_is_authorized_without_external_policy_lookup(hook_and_policy) -> None:
    hook, policy = hook_and_policy

    assert hook.can_manage_bot(
        bot_id="bot-1", owner_id="owner", actor_id="owner"
    )
    assert policy.calls == []


def test_collaborator_decision_is_delegated_to_registered_policy(
    hook_and_policy,
) -> None:
    hook, policy = hook_and_policy

    assert hook.can_manage_bot(
        bot_id="bot-1", owner_id="owner", actor_id="manager"
    )
    assert policy.calls == [("bot-1", "owner", "manager", PermissionLevel.MEMBER, True)]


def test_denied_collaborator_fails_closed() -> None:
    policy = _Collaborators(allowed=False)
    hook: BotCapabilityAuthorizationHookProtocol = (
        CollaboratorBotCapabilityAuthorizationHook(policy)
    )

    assert not hook.can_manage_bot(
        bot_id="bot-1", owner_id="owner", actor_id="stranger"
    )
    assert policy.calls == [("bot-1", "owner", "stranger", PermissionLevel.MEMBER, True)]
