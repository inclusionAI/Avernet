"""The bars the collaborator-access migration decided, pinned where enforced.

The groups moving off ``OWNER_SCOPED`` take the config-manifest's split —
reading how a bot is set up is MEMBER work, deciding what it is (persona,
startup script, workspace contents) is ADMIN behind the edit lock, and
operational commands (restart, running a routine) are MEMBER behind the lock.
This pins that decision per row so a later edit cannot quietly relax one group
while its neighbours keep the bar; the gate's own behaviour is
``test_bot_access.py``'s, and the retiring-address exemptions are
``test_authorization_inventory.py``'s.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    Check,
    EDIT_LOCK,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel

MEMBER_READS = [
    ("GET", "/openapi/v1/bots/{bot_id}"),
    ("GET", "/openapi/v1/bots/{bot_id}/identity"),
    ("GET", "/openapi/v1/bots/{bot_id}/identity/{file_type}"),
    ("GET", "/openapi/v1/bots/identity/{bot_id}"),
    ("GET", "/openapi/v1/bots/identity/{bot_id}/{file_type}"),
    ("GET", "/openapi/v1/bots/{bot_id}/data-init"),
    ("GET", "/openapi/v1/bots/{bot_id}/startup-script"),
    ("GET", "/openapi/v1/bots/{bot_id}/resources"),
    ("GET", "/openapi/v1/bots/{bot_id}/resources/download"),
    ("GET", "/openapi/v1/bots/{bot_id}/resources/download-dir"),
    ("GET", "/openapi/v1/bots/{bot_id}/resources/preview"),
    ("GET", "/openapi/v1/bots/{bot_id}/resources/stat"),
    ("GET", "/openapi/v1/bots/{bot_id}/routines"),
    ("GET", "/openapi/v1/bots/{bot_id}/routines/{routine_id}"),
    ("GET", "/openapi/v1/bots/{bot_id}/routines/{routine_id}/runs"),
]

ADMIN_LOCKED_WRITES = [
    ("PUT", "/openapi/v1/bots/{bot_id}/identity/{file_type}"),
    ("PUT", "/openapi/v1/bots/identity/{bot_id}/{file_type}"),
    ("PUT", "/openapi/v1/bots/{bot_id}/startup-script"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/startup-script"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/resources"),
    ("POST", "/openapi/v1/bots/{bot_id}/resources/mkdir"),
    ("POST", "/openapi/v1/bots/{bot_id}/resources/upload"),
    ("POST", "/openapi/v1/bots/{bot_id}/routines"),
    ("PATCH", "/openapi/v1/bots/{bot_id}/routines/{routine_id}"),
    ("DELETE", "/openapi/v1/bots/{bot_id}/routines/{routine_id}"),
]

MEMBER_LOCKED_OPERATIONS = [
    ("POST", "/openapi/v1/bots/{bot_id}/restart"),
    ("POST", "/openapi/v1/bots/{bot_id}/routines/{routine_id}/run"),
]

#: The one share-barred command that stays with the owner: its credential
#: collection (the caller's IAM token, spent by the device callback) only
#: means what it should when the actor is the owner.
OWNER_LOCKED_OPERATIONS = [
    ("POST", "/openapi/v1/bots/{bot_id}/data-init"),
]


def _bar_of(key):
    rule = AUTHORIZATION[key]
    assert isinstance(rule, Check), (
        f"{key[0]} {key[1]} is not adjudicated — the collaborator-access "
        "migration's rows must sit on the seam, not on a scaffold"
    )
    return rule


def test_reads_are_member_barred():
    """Reading how a shared bot is set up is part of working on it."""
    for key in MEMBER_READS:
        rule = _bar_of(key)
        assert rule.level is PermissionLevel.MEMBER, (
            f"{key[0]} {key[1]} is not the member-level read the migration decided"
        )
        assert rule.edit_lock is None, f"{key[0]} {key[1]} locks what is a read"


def test_shaping_writes_are_admin_behind_the_lock():
    """Deciding what a bot is — persona, script, workspace — is ADMIN work."""
    for key in ADMIN_LOCKED_WRITES:
        rule = _bar_of(key)
        assert rule.level is PermissionLevel.ADMIN, (
            f"{key[0]} {key[1]} moved off the ADMIN bar the migration decided"
        )
        assert rule.edit_lock is EDIT_LOCK, f"{key[0]} {key[1]} lost its edit lock"


def test_operational_commands_are_member_behind_the_lock():
    """Restart and firing a routine are operations, not configuration."""
    for key in MEMBER_LOCKED_OPERATIONS:
        rule = _bar_of(key)
        assert rule.level is PermissionLevel.MEMBER, (
            f"{key[0]} {key[1]} moved off the MEMBER bar the migration decided"
        )
        assert rule.edit_lock is EDIT_LOCK, (
            f"{key[0]} {key[1]} is a command on the shared draft and needs the lock"
        )


def test_the_credential_collecting_trigger_stays_with_the_owner():
    """data-init collects the caller's token on the owner's record — owner only.

    The trigger persists the *caller's* IAM token into the owner's bot ext,
    where the device-ready callback spends it as a bearer. An actor who is
    not the owner would leave somebody else's live credential on a record the
    owner can see, so this is the one migrated write collaborators may not
    reach at any level.
    """
    for key in OWNER_LOCKED_OPERATIONS:
        rule = _bar_of(key)
        assert rule.level is PermissionLevel.OWNER, (
            f"{key[0]} {key[1]} moved off the OWNER bar the migration decided"
        )
        assert rule.edit_lock is EDIT_LOCK, f"{key[0]} {key[1]} lost its edit lock"