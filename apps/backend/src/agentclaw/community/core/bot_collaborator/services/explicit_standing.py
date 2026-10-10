"""Explicit-standing judgment for the edit/operations permission split.

迭代11《编辑权限申请审批策略》PRD §3.1 keeps a Team Space Bot's edit and
operations surfaces for its Owner and the editors the Owner granted or
approved; added Space members default to view and chat. The Space synthesis
(:meth:`EditorPolicy.space_member`, consumed by
:meth:`CollaboratorService.get_operable_permission_level`) grants those
members an effective MEMBER for the reads, so the edit/operations rows
(``Check … explicit=True``) need a second ladder: does this user's standing
rest on an *explicit* collaborator row or ownership. The answers live here so
the grant and the veto stay in one cohesive place and
``collaborator_service.py`` stays about the collaboration mechanics.

``get_explicit_permission_level`` answers a request with **one** collaborator
read: the row answers both halves (level and origin) at once, where a "raw
level, then a separate standing probe" shape would pay the read twice per
request on the permission path this gate adjudicates. Row answers still pass
the COSEC revocation recheck, so an editor removed from the Space loses
operations on their next request — the same rule the effective ladder
applies, kept because a removal must revoke on both ladders equally.

``has_explicit_membership`` is the page-shaped twin for the inventory read
model: a page pays one read per question (its ``levels`` sibling makes the
same trade), and the two answers together drive the card/action split.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRole,
    PermissionLevel,
)
from agentclaw.community.core.repository.protocols.bot import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.bot_collaborator.services.editor_policy import (
    EditorPolicy,
)
from agentclaw.community.utils.env_utils import get_current_env


class ExplicitStandingMixin:
    """The explicit-origin half of the permission policy, as service methods."""

    _collaborator_repo: CollaboratorRepositoryProtocol
    _editor_policy: EditorPolicy

    def get_explicit_permission_level(
        self,
        *,
        bot: Mapping[str, Any],
        user_id: str,
        env: Optional[str] = None,
    ) -> PermissionLevel:
        """The level resting on an explicit row or ownership; the synthesis has no voice.

        One collaborator read answers both halves of the gate's question:
        the row itself names the level, and a row's existence *is* the
        explicit origin. Owners short-circuit the read — ownership is the
        most explicit relation there is. Row answers still pass the COSEC
        recheck, so a Space removal revokes operations exactly as the
        effective ladder would.
        """
        if user_id == str(bot.get("owner_id") or ""):
            return PermissionLevel.OWNER
        resolved_env = env or get_current_env()
        role = self._collaborator_repo.get_user_role(
            int(bot.get("id") or 0), user_id, resolved_env
        )
        if role is None:
            return PermissionLevel.NONE
        if role == CollaboratorRole.ADMIN:
            level = PermissionLevel.ADMIN
        else:
            level = PermissionLevel.MEMBER
        # COSEC: an editor removed from the Space loses operations at once —
        # the same revocation the effective ladder applies to row answers.
        if not self._editor_policy.allows_editor(bot=bot, user_id=user_id):
            return PermissionLevel.NONE
        return level

    def has_explicit_membership(
        self,
        *,
        bots: Sequence[Mapping[str, Any]],
        user_id: str,
        env: Optional[str] = None,
    ) -> frozenset[int]:
        """Bot pks whose standing rests on an explicit collaborator row or ownership.

        The bulk twin of :meth:`get_explicit_permission_level` for the
        inventory read model. Owners belong by definition; the Space
        synthesis deliberately has no voice — that is the whole point of
        the method.
        """
        resolved_env = env or get_current_env()
        records = self._collaborator_repo.list_by_user(user_id, resolved_env)
        role_pks = {record.bot_pk for record in records}
        explicit: set[int] = {pk for pk in role_pks if pk > 0}
        for bot in bots:
            bot_pk = int(bot.get("id") or 0)
            if bot_pk <= 0:
                continue
            if user_id == str(bot.get("owner_id") or ""):
                explicit.add(bot_pk)
        return frozenset(explicit)


__all__ = ["ExplicitStandingMixin"]