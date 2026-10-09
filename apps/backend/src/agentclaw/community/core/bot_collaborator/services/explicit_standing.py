"""Explicit-standing judgment for the edit/operations permission split.

迭代11《编辑权限申请审批策略》PRD §3.1 keeps a Team Space Bot's edit and
operations surfaces for its Owner and the editors the Owner granted or
approved; added Space members default to view and chat. The Space synthesis
(:meth:`EditorPolicy.space_member`, consumed by
:meth:`CollaboratorService.get_operable_permission_level`) grants those
members an effective MEMBER for the reads, so the edit/operations rows
(``Check … explicit=True``) need a second question: does this user's standing
rest on an *explicit* collaborator row or ownership — the answer this module
isolates, so the grant and the veto stay in one cohesive place and
``collaborator_service.py`` stays about the collaboration mechanics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from agentclaw.community.core.repository.protocols.bot import (
    CollaboratorRepositoryProtocol,
)

from agentclaw.community.utils.env_utils import get_current_env


class ExplicitStandingMixin:
    """The explicit-origin half of the permission policy, as service methods.

    Both answers share the one repository read their callers need, so a page
    that asks both questions pays for its membership read once.
    """

    _collaborator_repo: CollaboratorRepositoryProtocol

    def has_explicit_standing(
        self,
        *,
        bot: Mapping[str, Any],
        user_id: str,
        env: Optional[str] = None,
    ) -> bool:
        """Whether the user's standing rests on a real collaborator row or ownership.

        The gate-side judgment behind ``Check … explicit=True``: the Space
        synthesis deliberately has no voice here, because those rows publish
        the product rule that keeps the edit/operations domain for the Owner
        and the editors the Owner granted or approved.
        """
        if user_id == str(bot.get("owner_id") or ""):
            return True
        resolved_env = env or get_current_env()
        role = self._collaborator_repo.get_user_role(
            int(bot.get("id") or 0), user_id, resolved_env
        )
        return role is not None

    def has_explicit_membership(
        self,
        *,
        bots: Sequence[Mapping[str, Any]],
        user_id: str,
        env: Optional[str] = None,
    ) -> frozenset[int]:
        """Bot pks whose standing rests on an explicit collaborator row or ownership.

        The bulk twin of :meth:`has_explicit_standing` for the inventory read
        model. Owners belong by definition; the Space synthesis deliberately
        has no voice — that is the whole point of the method.
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