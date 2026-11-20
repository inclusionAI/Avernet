"""Read-time presentation refresh for work-order lists and details.

Aged rows predate nickname rendering, so the service rebuilds their display
copy at read time; the persisted row itself is never written back. This
module holds that refresh logic so ``work_order_service`` stays within the
repository architecture line cap — one cohesive concern per file, with
guards and formulas extracted verbatim (tests live with the service).
"""

from __future__ import annotations

import json

from agentclaw.community.core.repository.protocols.spaces import (
    SpaceRepositoryProtocol,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderEventType,
    WorkOrderMessageContent,
    skill_collaborator_applicant_display,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileLookupError,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.work_no import normalize_work_no_for_lookup

logger = get_logger()


class WorkOrderPresentationRefresher:
    """Rebuild aged work-order display copy at read time; never persists."""

    def __init__(
        self,
        *,
        staff_dept: StaffDeptPlugin,
        spaces: SpaceRepositoryProtocol,
    ) -> None:
        self._staff_dept = staff_dept
        self._spaces = spaces

    def get_applicant_name(self, *, applicant_user_id: str) -> str:
        try:
            profile = self._staff_dept.get_profile_by_work_no(
                work_no=normalize_work_no_for_lookup(applicant_user_id)
            )
        except StaffProfileLookupError:
            logger.warning(
                "failed to resolve applicant nickname; falling back to user id",
                extra={"user_id": applicant_user_id},
                exc_info=True,
            )
            return applicant_user_id

        nickname = (profile.nick_name or "").strip()
        return nickname[:128] or applicant_user_id

    def get_reviewer_name(self, *, reviewer_user_id: str | None) -> str | None:
        if not reviewer_user_id:
            return None
        try:
            profile = self._staff_dept.get_profile_by_work_no(
                work_no=normalize_work_no_for_lookup(reviewer_user_id)
            )
        except StaffProfileLookupError:
            logger.warning(
                "failed to resolve reviewer nickname",
                extra={"user_id": reviewer_user_id},
                exc_info=True,
            )
            return None

        nickname = (profile.nick_name or "").strip()
        return nickname[:128] or None

    def present_skill_collaborator_item(self, item):
        """Refresh pending Skill-request copy so historical rows gain a nickname."""

        work_order = item.work_order
        notification = item.notification
        if (
            work_order is None
            or notification is None
            or work_order.biz_type != WorkOrderBizType.SKILL_COLLABORATOR.value
            or notification.event_type
            != WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value
        ):
            return item
        skill_name = str(_business_data(work_order.biz_data).get("skill_name") or "")
        if not skill_name:
            return item
        applicant_user_id = work_order.applicant_user_id
        applicant_name = self.get_applicant_name(applicant_user_id=applicant_user_id)
        content = WorkOrderMessageContent.SKILL_COLLABORATOR_PENDING.value.format(
            applicant_display=skill_collaborator_applicant_display(
                applicant_user_id=applicant_user_id,
                applicant_name=applicant_name,
            ),
            skill_name=skill_name,
        )
        return item.model_copy(
            update={
                "notification": notification.model_copy(update={"content": content})
            }
        )

    def present_bot_collaborator_item(self, item):
        """Refresh historical Bot-editor copy so aged rows gain a nickname.

        The guard is the notification *species* (`BOT_COLLABORATOR_APPLIED`),
        never the work-order status: APPROVED/REJECTED rows whose inbox copy is
        the applicant-facing applied text re-render with today's nickname, the
        same read-time refresh contract as Skill rows. The persisted row is
        never written back; when biz_data lost the bot name, or the rebuild
        reproduces the stored copy verbatim, the item is returned unchanged.
        """

        work_order = item.work_order
        notification = item.notification
        if (
            work_order is None
            or notification is None
            or work_order.biz_type != WorkOrderBizType.BOT_COLLABORATOR.value
            or notification.event_type
            != WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value
        ):
            return item
        bot_name = str(_business_data(work_order.biz_data).get("bot_name") or "")
        if not bot_name:
            return item
        if _content_text(notification.content) is not None:
            # Envelope-shaped applied copies have no writer today (creation
            # always writes plain text); rebuilding them as plain text would
            # change the persisted shape in the presentation. Keep them
            # verbatim, mirroring the guard `_refresh_detail_applied_copy`
            # applies on the detail side.
            return item
        applicant_user_id = work_order.applicant_user_id
        applicant_name = self.get_applicant_name(applicant_user_id=applicant_user_id)
        content = WorkOrderMessageContent.BOT_COLLABORATOR_PENDING.value.format(
            applicant_name=applicant_name,
            bot_name=bot_name,
        )
        if content == notification.content:
            return item
        return item.model_copy(
            update={
                "notification": notification.model_copy(update={"content": content})
            }
        )

    def present_space_join_item(self, item):
        """Refresh historical Space-join copy so aged rows gain a nickname.

        Space-join rows persist no business data: the display copy embeds the
        applicant and space names inside a ``{"text": ...}`` JSON envelope. The
        space name is re-resolved from the referenced Space at read time;
        when the reverse lookup fails, the space lost its name, or the stored
        copy is not an envelope, the item is returned unchanged — never worse
        than the stored copy. The persisted row itself is never written back.
        """

        work_order = item.work_order
        notification = item.notification
        if (
            work_order is None
            or notification is None
            or work_order.biz_type != WorkOrderBizType.SPACE_JOIN.value
            or notification.event_type
            != WorkOrderEventType.SPACE_JOIN_APPLIED.value
        ):
            return item
        stored_text = _content_text(notification.content)
        if stored_text is None:
            return item
        content = self.rebuild_space_join_pending_copy(
            applicant_user_id=work_order.applicant_user_id,
            space_name=self.lookup_space_name(work_order.biz_id),
            stored_text=stored_text,
        )
        if content is None:
            return item
        return item.model_copy(
            update={
                "notification": notification.model_copy(update={"content": content})
            }
        )

    def rebuild_space_join_pending_copy(
        self,
        *,
        applicant_user_id: str | None,
        space_name: str | None,
        stored_text: str,
    ) -> str | None:
        """Re-render the pending copy in its persisted ``{"text": ...}`` shape."""

        if not applicant_user_id or not space_name:
            return None
        text = WorkOrderMessageContent.SPACE_JOIN_PENDING.value.format(
            applicant_name=self.get_applicant_name(
                applicant_user_id=applicant_user_id
            ),
            space_name=space_name,
        )
        if text == stored_text:
            return None
        return json.dumps({"text": text}, ensure_ascii=False)

    def rebuild_bot_editor_pending_copy(
        self,
        *,
        applicant_user_id: str | None,
        biz_data: str | None,
        stored_text: str,
    ) -> str | None:
        """Re-render the pending copy with the same formula as the list path."""

        if not applicant_user_id or not stored_text:
            return None
        bot_name = str(_business_data(biz_data).get("bot_name") or "")
        if not bot_name:
            return None
        text = WorkOrderMessageContent.BOT_COLLABORATOR_PENDING.value.format(
            applicant_name=self.get_applicant_name(
                applicant_user_id=applicant_user_id
            ),
            bot_name=bot_name,
        )
        if text == stored_text:
            return None
        return text

    def lookup_space_name(self, space_ref: str | None) -> str | None:
        """Reverse-resolve the referenced Space name, or give up quietly.

        Uses the repository read behind ``require_space`` — the same query the
        creation path locks — so a missing or renamed Space degrades to keeping
        the stored copy instead of raising during presentation.
        """

        if not space_ref:
            return None
        try:
            space = self._spaces.get_space(
                space_id=int(space_ref), env=get_current_env()
            )
        except (TypeError, ValueError):
            return None
        if space is None:
            return None
        name = getattr(space, "name", None)
        return name.strip() if isinstance(name, str) and name.strip() else None

    def refresh_detail_applied_copy(self, detail: WorkOrderDetail) -> str | None:
        """Rebuild the applied copy in details, the list refresh in lockstep.

        The detail view exposes no dedicated applicant field for the adapter
        to render, so historical rows gain today's nickname only when their
        persisted ``content`` is rebuilt with the same formulas the list
        projection uses. Rows without an applied notification, rows whose
        rebuild reproduces the stored copy verbatim, and space reverse-lookup
        failures return ``None`` so the stored copy survives untouched; the
        persisted row is never written back. Like the list refresh the guard
        is the notification species, never the work-order status.
        """

        work_order = detail.work_order
        if detail.event_type == WorkOrderEventType.SPACE_JOIN_APPLIED.value:
            stored_text = _content_text(detail.content)
            if stored_text is None:
                return None
            # Accepted divergence from the list path (which resolves the
            # space name solely through `_lookup_space_name`): get_detail's
            # joined space row predates this feature and ignores soft-delete,
            # so a soft-deleted space can surface its name here while the
            # list keeps the stored copy. For live spaces both sources
            # produce the identical name.
            return self.rebuild_space_join_pending_copy(
                applicant_user_id=work_order.applicant_user_id,
                space_name=detail.space_name
                or self.lookup_space_name(work_order.biz_id),
                stored_text=stored_text,
            )
        if detail.event_type == WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value:
            stored_text = detail.content
            if not isinstance(stored_text, str) or not stored_text.strip():
                return None
            # Envelope-shaped content would change its persisted shape; only
            # the plain-text applied copy is rebuilt, list in lockstep.
            if _content_text(stored_text) is not None:
                return None
            return self.rebuild_bot_editor_pending_copy(
                applicant_user_id=work_order.applicant_user_id,
                biz_data=work_order.biz_data,
                stored_text=stored_text,
            )
        return None


def _business_data(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _content_text(raw: str | None) -> str | None:
    """Extract the persisted ``{"text": ...}`` envelope payload, if any."""

    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    text = parsed.get("text")
    if isinstance(text, str) and text.strip():
        return text
    return None
