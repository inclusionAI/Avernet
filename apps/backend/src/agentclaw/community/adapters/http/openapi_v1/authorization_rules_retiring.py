"""Retiring-address authorization rows — the ``deprecated/`` half of the table.

Split from :mod:`authorization_rules` when the merged inventory crossed the
1000-line module cap (Rule 9): both halves are one table keyed identically,
and ``AUTHORIZATION`` unions this dict in unchanged — no row is added,
removed or re-decided by the move. Import-light like its parent: a policy
table that must import without standing up the HTTP stack.
"""

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from .models_authorization import (
    EDIT_LOCK,
    INHERITED,
    Authorization,
    Check,
)

#: Retiring addresses in ``deprecated/``: rows for the operations whose
#: replacements moved. Keyed as ``AUTHORIZATION`` is, consumed only through
#: that union; no consumer ever reads this dict directly.
RETIRING_AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("GET", "/openapi/v1/bots/approvals/{bot_id}/mode"): Check(PermissionLevel.MEMBER),
    ("PUT", "/openapi/v1/bots/approvals/{bot_id}/mode"): Check(PermissionLevel.MEMBER, explicit=True),
    ("GET", "/openapi/v1/bots/approvals/{bot_id}/modes"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/connection/{bot_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/engine/{bot_id}/available"): Check(
        PermissionLevel.MEMBER
    ),
    ("GET", "/openapi/v1/bots/engine/{bot_id}/capabilities"): Check(
        PermissionLevel.MEMBER
    ),
    ("GET", "/openapi/v1/bots/engine/{bot_id}/status"): Check(PermissionLevel.MEMBER),
    # Identity's retiring addresses mirror the replacement's new rows, not
    # INHERITED — a forced move, named as such. The chain has no exit:
    # a ``Check`` replacement leaves an ``INHERITED`` twin abandoned by the
    # twin guard unless it is exempted; the exemption exists only for twins
    # with no ``{bot_id}`` on the path (a ``Check`` row's gate cannot read a
    # query-string bot), and these paths carry the bot; and a ``Check`` row's
    # handler must consume ``OwnerIdDep``, so honouring a *pinned* owner is
    # not available either. So these retiring addresses publish and honour
    # ``owner_id`` — a capability their frozen contract did not have, the one
    # the resources/routines retirements below refuse via the pin — recorded
    # here rather than hidden: the alternative was an unadjudicated owner
    # read at an address the handler still serves. ``relocate`` does not
    # carry route-level gate dependencies either, so the rows are what makes
    # the twin's gate attach at all. The resources and routines retiring
    # addresses below stay INHERITED for that opposite reason: their bots
    # travel as query/body parameters these paths cannot offer a ``Check``
    # row's gate, and their shims pin the owner to the caller instead
    # (``deprecated._requery``).
    ("GET", "/openapi/v1/bots/identity/{bot_id}"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): Check(
        PermissionLevel.MEMBER
    ),
    ("PUT", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): Check(
        PermissionLevel.ADMIN, EDIT_LOCK
    ),
    ("GET", "/openapi/v1/bots/models/{bot_id}"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/models/{bot_id}/{model_id:path}"): Check(
        PermissionLevel.MEMBER
    ),
    ("DELETE", "/openapi/v1/bots/resources"): INHERITED,
    ("GET", "/openapi/v1/bots/resources"): INHERITED,
    ("GET", "/openapi/v1/bots/resources/download"): INHERITED,
    ("POST", "/openapi/v1/bots/resources/mkdir"): INHERITED,
    ("GET", "/openapi/v1/bots/resources/preview"): INHERITED,
    ("GET", "/openapi/v1/bots/resources/stat"): INHERITED,
    ("POST", "/openapi/v1/bots/resources/upload"): INHERITED,
    ("GET", "/openapi/v1/bots/routines"): INHERITED,
    ("POST", "/openapi/v1/bots/routines"): INHERITED,
    ("DELETE", "/openapi/v1/bots/routines/{routine_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/routines/{routine_id}"): INHERITED,
    ("PATCH", "/openapi/v1/bots/routines/{routine_id}"): INHERITED,
    ("POST", "/openapi/v1/bots/routines/{routine_id}/run"): INHERITED,
    ("GET", "/openapi/v1/bots/routines/{routine_id}/runs"): INHERITED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}"): INHERITED,
    ("POST", "/openapi/v1/bots/sessions/{bot_id}"): INHERITED,
    ("DELETE", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): INHERITED,
    ("PATCH", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): INHERITED,
    ("DELETE", "/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages"): INHERITED,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages"): INHERITED,
    ("GET", "/openapi/v1/bots/skills"): INHERITED,
    ("POST", "/openapi/v1/bots/skills/upload"): INHERITED,
    ("DELETE", "/openapi/v1/bots/skills/{skill_id}"): INHERITED,
    ("GET", "/openapi/v1/bots/skills/{skill_id}"): INHERITED,
    ("POST", "/openapi/v1/bots/skills/{skill_id}/activate"): INHERITED,
    ("POST", "/openapi/v1/bots/skills/{skill_id}/deactivate"): INHERITED,
    ("GET", "/openapi/v1/bots/{bot_id}/auth-status"): INHERITED,
    ("GET", "/openapi/v1/bots/{bot_id}/engine-config"): INHERITED,
    ("PUT", "/openapi/v1/bots/{bot_id}/engine-config"): INHERITED,
}