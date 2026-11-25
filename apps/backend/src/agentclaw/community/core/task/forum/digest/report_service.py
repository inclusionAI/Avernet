"""Batch orchestration for BBS high-value digest confirmation.

A Bot submits one whole batch. The backend resolves the owner, persists the
batch, creates a single Human/Bot confirmation session, and notifies the owner
with a deep link. The Bot reports one final decision after it has rendered the
items to the owner and completed its own persistence work.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from injector import inject

from agentclaw.community.core.bot_management.bot_service_protocol import (
    BotServiceProtocol,
)
from agentclaw.community.core.errors import (
    Forbidden,
    InternalError,
    NotFound,
    ValidationError,
)
from agentclaw.community.core.repository.protocols.task.forum_digest import (
    ForumDigestReportRepositoryProtocol,
)
from agentclaw.community.core.task.forum.digest.models import (
    DIGEST_NOTIFICATION_STATUS_FAILED,
    DIGEST_NOTIFICATION_STATUS_SUBMITTED,
    DIGEST_REPORT_FINAL_DECISIONS,
    ForumDigestReportBrief,
    ForumDigestReportCreateResult,
    ForumDigestReportDecisionResult,
    ForumDigestReportItem,
    ForumDigestReportRecord,
    MAX_DIGEST_BRIEF_HIGHLIGHT_COUNT,
    MAX_DIGEST_BRIEF_HIGHLIGHT_LENGTH,
    MAX_DIGEST_BRIEF_SUMMARY_LENGTH,
    MAX_DIGEST_ITEM_REASON_LENGTH,
    MAX_DIGEST_ITEM_SOURCE_LENGTH,
    MAX_DIGEST_ITEM_TAG_LENGTH,
    MAX_DIGEST_ITEM_TEXT_LENGTH,
    MAX_DIGEST_ITEM_TITLE_LENGTH,
    MAX_DIGEST_REPORT_ITEM_COUNT,
    MAX_DIGEST_RETENTION_TARGET_LENGTH,
)
from agentclaw.community.core.task.forum.models import MAX_ID_LENGTH
from agentclaw.community.core.task.forum.digest.report_service_protocol import (
    ForumDigestReportServiceProtocol,
)
from agentclaw.community.core.task.forum.digest.session_link import (
    DigestSessionLinkBuilder,
    FrontendUrlProviderProtocol,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    OpenApiBotPort,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

logger = get_logger()


class BbsDigestReportService(ForumDigestReportServiceProtocol):
    """Create and settle batch-level confirmation flows."""

    @inject
    def __init__(
        self,
        *,
        repository: ForumDigestReportRepositoryProtocol,
        bot_service: BotServiceProtocol,
        openapi_bot: OpenApiBotPort,
        notify_sender: NotifySenderPlugin,
        frontend_url_provider: FrontendUrlProviderProtocol,
    ) -> None:
        self._repository = repository
        self._bot_service = bot_service
        self._openapi_bot = openapi_bot
        self._notify_sender = notify_sender
        self._session_links = DigestSessionLinkBuilder(frontend_url_provider)

    async def create_report(
        self,
        *,
        bot_id: str,
        client_request_id: str,
        brief: ForumDigestReportBrief,
        items: Sequence[ForumDigestReportItem],
    ) -> ForumDigestReportCreateResult:
        normalized_bot_id = self._required_text(bot_id, "bot_id", MAX_ID_LENGTH)
        request_id = self._required_text(
            client_request_id, "client_request_id", MAX_ID_LENGTH
        )
        normalized_brief = self._normalize_brief(brief)
        normalized_items = self._normalize_items(items)
        owner_user_id = self._resolve_owner(normalized_bot_id)

        created = self._repository.create_report(
            bot_id=normalized_bot_id,
            owner_user_id=owner_user_id,
            client_request_id=request_id,
            brief=normalized_brief,
            items=normalized_items,
        )
        if not created.created:
            logger.info(
                "[bbs-digest-report][replay] digest=%s bot=%s request=%s",
                created.digest.digest_id,
                normalized_bot_id,
                request_id,
            )
            return created

        try:
            send_result = await self._openapi_bot.send_message(
                bot_id=normalized_bot_id,
                message=self._build_confirmation_prompt(
                    created.digest, normalized_brief, normalized_items
                ),
                metadata={
                    "source": "bbs_digest_report",
                    "digest_id": created.digest.digest_id,
                    "item_count": len(normalized_items),
                    "confirmation": {
                        "scope": "BATCH",
                        "accepted_decisions": ["CONFIRMED", "REJECTED"],
                    },
                },
            )
            session_id = send_result.session_id or send_result.run_id
        except Exception as exc:
            logger.warning(
                "[bbs-digest-report][session-failed] digest=%s bot=%s error=%s",
                created.digest.digest_id,
                normalized_bot_id,
                exc,
            )
            self._repository.mark_failed(created.digest.digest_id)
            raise InternalError(f"failed to create confirmation session: {exc}") from exc

        session_url = self._build_session_url(
            bot_id=normalized_bot_id,
            owner_user_id=owner_user_id,
            session_id=session_id,
            digest_id=created.digest.digest_id,
        )
        notification_status = self._send_notification(
            digest=created.digest,
            owner_user_id=owner_user_id,
            session_url=session_url,
        )
        updated = self._repository.update_delivery(
            digest_id=created.digest.digest_id,
            session_id=session_id,
            session_url=session_url,
            notification_status=notification_status,
        )
        if updated is None:
            raise InternalError("digest report disappeared while updating delivery")

        logger.info(
            "[bbs-digest-report][created] digest=%s bot=%s session=%s notify=%s",
            updated.digest_id,
            normalized_bot_id,
            session_id,
            notification_status,
        )
        return ForumDigestReportCreateResult(digest=updated, created=True)

    async def report_decision(
        self,
        *,
        digest_id: str,
        bot_id: str,
        decision: str,
        decision_client_request_id: str,
    ) -> ForumDigestReportDecisionResult:
        normalized_digest_id = self._required_text(digest_id, "digest_id", MAX_ID_LENGTH)
        normalized_bot_id = self._required_text(bot_id, "bot_id", MAX_ID_LENGTH)
        normalized_decision = self._required_text(decision, "decision", 16).upper()
        if normalized_decision not in DIGEST_REPORT_FINAL_DECISIONS:
            allowed = ", ".join(sorted(DIGEST_REPORT_FINAL_DECISIONS))
            raise ValidationError(f"decision must be one of: {allowed}")
        normalized_decision_request = self._required_text(
            decision_client_request_id,
            "client_request_id",
            MAX_ID_LENGTH,
        )

        existing = self._repository.get_report(normalized_digest_id)
        if existing is None:
            raise NotFound("digest report not found")
        if existing.bot_id != normalized_bot_id:
            raise Forbidden("digest report belongs to another bot")

        result = self._repository.apply_decision(
            digest_id=normalized_digest_id,
            decision=normalized_decision,
            decision_client_request_id=normalized_decision_request,
        )
        logger.info(
            "[bbs-digest-report][decision] digest=%s bot=%s decision=%s changed=%s",
            normalized_digest_id,
            normalized_bot_id,
            normalized_decision,
            result.changed,
        )
        return result

    def _resolve_owner(self, bot_id: str) -> str:
        try:
            bot = self._bot_service.get_bot_by_id(bot_id)
        except Exception as exc:
            raise InternalError(f"failed to resolve bot owner: {exc}") from exc

        if not isinstance(bot, dict):
            raise NotFound(f"bot not found: {bot_id}")
        owner_user_id = str(bot.get("owner_id") or "").strip()
        if not owner_user_id:
            raise NotFound(f"bot owner is missing: {bot_id}")
        return owner_user_id

    def _normalize_brief(
        self, brief: ForumDigestReportBrief
    ) -> ForumDigestReportBrief:
        summary = self._required_text(
            brief.summary,
            "brief.summary",
            MAX_DIGEST_BRIEF_SUMMARY_LENGTH,
        )
        highlights = tuple(
            self._required_text(
                highlight,
                "brief.highlights",
                MAX_DIGEST_BRIEF_HIGHLIGHT_LENGTH,
            )
            for highlight in brief.highlights
        )
        if not 1 <= len(highlights) <= MAX_DIGEST_BRIEF_HIGHLIGHT_COUNT:
            raise ValidationError(
                "brief.highlights must contain between 1 and "
                f"{MAX_DIGEST_BRIEF_HIGHLIGHT_COUNT} entries"
            )
        return ForumDigestReportBrief(summary=summary, highlights=highlights)

    def _normalize_items(
        self, items: Sequence[ForumDigestReportItem]
    ) -> tuple[ForumDigestReportItem, ...]:
        if not 1 <= len(items) <= MAX_DIGEST_REPORT_ITEM_COUNT:
            raise ValidationError(
                f"items must contain between 1 and {MAX_DIGEST_REPORT_ITEM_COUNT} entries"
            )

        normalized: list[ForumDigestReportItem] = []
        for item in items:
            normalized.append(
                ForumDigestReportItem(
                    source=self._required_text(
                        item.source, "items.source", MAX_DIGEST_ITEM_SOURCE_LENGTH
                    ).upper(),
                    topic_id=self._required_text(
                        item.topic_id, "items.topic_id", MAX_ID_LENGTH
                    ),
                    post_id=self._optional_text(
                        item.post_id, "items.post_id", MAX_ID_LENGTH
                    ),
                    title=self._required_text(
                        item.title, "items.title", MAX_DIGEST_ITEM_TITLE_LENGTH
                    ),
                    content=self._required_text(
                        item.content, "items.content", MAX_DIGEST_ITEM_TEXT_LENGTH
                    ),
                    reason=self._required_text(
                        item.reason, "items.reason", MAX_DIGEST_ITEM_REASON_LENGTH
                    ),
                    tags=self._normalize_tags(item.tags),
                    retention_target=self._required_text(
                        item.retention_target,
                        "items.retention_target",
                        MAX_DIGEST_RETENTION_TARGET_LENGTH,
                    ),
                )
            )
        return tuple(normalized)

    @staticmethod
    def _normalize_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            BbsDigestReportService._required_text(
                tag, "items.tags", MAX_DIGEST_ITEM_TAG_LENGTH
            )
            for tag in tags
        )
        duplicates: list[str] = []
        for tag in normalized:
            if normalized.count(tag) > 1 and tag not in duplicates:
                duplicates.append(tag)
        if duplicates:
            raise ValidationError(f"items.tags contains duplicates: {duplicates[0]}")
        return normalized

    @staticmethod
    def _required_text(value: object, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValidationError(f"{field} exceeds {max_length} characters")
        return normalized

    @staticmethod
    def _optional_text(value: object | None, field: str, max_length: int) -> str | None:
        if value is None:
            return None
        return BbsDigestReportService._required_text(value, field, max_length)

    def _build_session_url(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        session_id: str,
        digest_id: str,
    ) -> str:
        try:
            return self._session_links.build(
                bot_id=bot_id,
                owner_user_id=owner_user_id,
                session_id=session_id,
            )
        except Exception as exc:
            self._repository.mark_failed(digest_id)
            raise InternalError(f"failed to build confirmation session link: {exc}") from exc

    @staticmethod
    def _build_confirmation_prompt(
        digest: ForumDigestReportRecord,
        brief: ForumDigestReportBrief,
        items: tuple[ForumDigestReportItem, ...],
    ) -> str:
        lines = [
            "你的 Bot 从 BBS 中整理了一批可能值得长期沉淀的内容。",
            "请在本会话中向用户完整展示以下全部条目，请用户对“整批内容”做出一个整体确认或整体拒绝。",
            "用户确认后，请先完成你要执行的长期记忆或 Soul 写入，再调用 BBS digest decision 接口上报最终结果。",
            "",
            f"digest_id: {digest.digest_id}",
            f"摘要: {brief.summary}",
            "",
        ]
        lines.extend(f"- {highlight}" for highlight in brief.highlights)
        lines.extend(
            [
                "",
                f"条目数: {len(items)}",
                "",
            ]
        )
        for index, item in enumerate(items, start=1):
            source = f"post {item.post_id}" if item.post_id else "topic"
            lines.extend(
                [
                    f"{index}. [{item.retention_target}] {item.title}",
                    f"   来源: BBS {source} {item.topic_id}",
                    f"   内容: {item.content}",
                    f"   沉淀理由: {item.reason}",
                    f"   标签: {', '.join(item.tags) if item.tags else '-'}",
                    "",
                ]
            )
        lines.append("请用户明确回复整批确认或整批拒绝，不要逐条勾选。")
        return "\n".join(lines)

    def _send_notification(
        self,
        *,
        digest: ForumDigestReportRecord,
        owner_user_id: str,
        session_url: str,
    ) -> str:
        message = NotifyMessage(
            title="BBS 高价值内容待确认",
            body=(
                f"{digest.brief.summary}\n"
                f"你的 Bot 整理了 {len(digest.items)} 条高价值 BBS 内容。"
                "请进入对话查看整批内容，并整体确认或拒绝沉淀。"
            ),
            recipient=owner_user_id,
            deep_link=session_url,
            extra={
                "channel": "tc_card",
                "card_biz_id": f"bbs_digest_{digest.digest_id}",
                "card_data": json.dumps(
                    {
                        "click": "",
                        "digest_id": digest.digest_id,
                        "summary": digest.brief.summary,
                        "highlights": list(digest.brief.highlights),
                        "item_count": len(digest.items),
                        "session_url": session_url,
                    },
                    ensure_ascii=False,
                ),
                "session_url": session_url,
            },
        )
        result = self._notify_sender.send(message, channel="tc_card")
        return (
            DIGEST_NOTIFICATION_STATUS_SUBMITTED
            if result is not None and str(result)
            else DIGEST_NOTIFICATION_STATUS_FAILED
        )


__all__ = ["BbsDigestReportService"]
