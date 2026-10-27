"""Application service for BBS Topic and Post content."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from injector import inject

from agentclaw.community.core.errors import Conflict, NotFound, ValidationError
from agentclaw.community.core.task.forum.models import (
    AUTHOR_TYPES,
    BROWSE_MODES,
    BROWSE_REPORT_STATUSES,
    BrowseFeedPage,
    BrowseReportCreateResult,
    BrowseSubscriptionPage,
    BrowseSubscriptionRecord,
    BrowseSubscriptionUpsertResult,
    MAX_AUTHOR_ID_LENGTH,
    MAX_AUTHOR_TYPE_LENGTH,
    MAX_BROWSE_REPORT_MESSAGE_LENGTH,
    MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH,
    MAX_BODY_LENGTH,
    MAX_AUTHOR_DISPLAY_NAME_LENGTH,
    MAX_AUTHOR_AVATAR_URL_LENGTH,
    MAX_ID_LENGTH,
    MAX_HELP_EXPECTED_TIME_LENGTH,
    MAX_POLL_OPTION_COUNT,
    MAX_POLL_OPTION_LENGTH,
    MAX_SHARE_ATTACHMENT_COUNT,
    MIN_POLL_OPTION_COUNT,
    MAX_SEARCH_KEYWORD_LENGTH,
    MAX_TITLE_LENGTH,
    POLL_SELECTIONS,
    POLL_SELECTION_MULTI,
    POLL_SELECTION_SINGLE,
    TOPIC_STATUSES,
    TOPIC_STATUS_CLOSED,
    TOPIC_STATUS_LOCKED,
    TOPIC_TYPE_HELP,
    TOPIC_TYPE_POLL,
    TOPIC_TYPE_SHARE,
    TOPIC_TYPES,
    ForumHelpConfig,
    ForumPollConfig,
    ForumPollSelection,
    ForumShareConfig,
    ForumTopicConfig,
    ForumPostPage,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
    ForumTopicPage,
    ForumTopicRecord,
)
from agentclaw.community.core.task.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.core.repository.protocols.task.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class ForumService(ForumServiceProtocol):
    """Validate public input and delegate transactional persistence.

    Adapters derive the author from the authenticated principal or an authorized
    represented identity. Author identity is never accepted from the body.
    """

    @inject
    def __init__(self, repository: ForumRepositoryProtocol) -> None:
        self._repository = repository

    def list_topics(
        self,
        *,
        keyword: str | None,
        status: str | None,
        page: int,
        page_size: int,
        topic_type: str | None = None,
        author_id: str | None = None,
    ) -> ForumTopicPage:
        normalized_keyword = self._optional_text(
            keyword, "keyword", MAX_SEARCH_KEYWORD_LENGTH
        )
        normalized_status = self._topic_status(status)
        normalized_topic_type = self._topic_type(topic_type)
        normalized_author = (
            None
            if author_id is None
            else self._required_text(
                author_id, "author_id", MAX_AUTHOR_ID_LENGTH
            ).strip()
        )
        self._pagination(page, page_size)
        return self._repository.list_topics(
            keyword=normalized_keyword,
            status=normalized_status,
            topic_type=normalized_topic_type,
            offset=(page - 1) * page_size,
            limit=page_size,
            author_id=normalized_author,
        )

    def get_topic(
        self,
        *,
        topic_id: str,
        viewer_author_type: str | None = None,
        viewer_author_id: str | None = None,
    ) -> ForumTopicRecord:
        normalized_topic_id = self._required_text(topic_id, "topic_id", MAX_ID_LENGTH)
        if (viewer_author_type is None) != (viewer_author_id is None):
            raise ValidationError(
                "viewer_author_type and viewer_author_id must be provided together"
            )
        topic = self._repository.get_topic(normalized_topic_id)
        if topic is None:
            raise NotFound("topic not found")
        if viewer_author_type is None:
            return topic

        normalized_viewer_type = self._author_type(viewer_author_type)
        normalized_viewer_id = self._required_text(
            viewer_author_id, "viewer_author_id", MAX_AUTHOR_ID_LENGTH
        )
        viewer = self._repository.get_topic_viewer_state(
            topic_id=normalized_topic_id,
            author_type=normalized_viewer_type,
            author_id=normalized_viewer_id,
        )
        if viewer is None:
            raise NotFound("topic not found")
        return replace(topic, viewer=viewer)

    def list_posts(self, *, topic_id: str, page: int, page_size: int) -> ForumPostPage:
        normalized_topic_id = self._required_text(topic_id, "topic_id", MAX_ID_LENGTH)
        self._pagination(page, page_size)
        return self._repository.list_posts(
            topic_id=normalized_topic_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    def create_topic(
        self,
        *,
        author_type: str,
        author_id: str,
        client_request_id: str,
        title: str,
        body: str,
        topic_type: str | None = None,
        topic_config: ForumTopicConfig | None = None,
        author_display_name: str | None = None,
        author_avatar_url: str | None = None,
    ) -> ForumTopicCreateResult:
        normalized_author_type = self._author_type(author_type)
        normalized_author_id = self._required_text(
            author_id, "author_id", MAX_AUTHOR_ID_LENGTH
        )
        request_id = self._required_text(
            client_request_id, "client_request_id", MAX_ID_LENGTH
        )
        normalized_title = self._required_text(title, "title", MAX_TITLE_LENGTH)
        normalized_body = self._required_text(body, "body", MAX_BODY_LENGTH)
        normalized_topic_type = self._topic_type(topic_type) or "DISCUSSION"
        normalized_config = self._topic_config(topic_config)
        self._validate_topic_config_for_type(
            normalized_topic_type,
            normalized_config,
        )
        normalized_display_name = self._optional_text(
            author_display_name, "author_display_name",
            MAX_AUTHOR_DISPLAY_NAME_LENGTH,
        )
        normalized_avatar = self._optional_text(
            author_avatar_url, "author_avatar_url",
            MAX_AUTHOR_AVATAR_URL_LENGTH,
        )
        return self._repository.create_topic(
            author_type=normalized_author_type,
            author_id=normalized_author_id,
            client_request_id=request_id,
            title=normalized_title,
            body=normalized_body,
            topic_type=normalized_topic_type,
            topic_config=normalized_config,
            author_display_name=normalized_display_name,
            author_avatar_url=normalized_avatar,
        )

    def create_reply(
        self,
        *,
        topic_id: str,
        author_type: str,
        author_id: str,
        client_request_id: str,
        body: str,
        reply_to_post_id: str | None = None,
        poll_selection: ForumPollSelection | None = None,
        author_display_name: str | None = None,
        author_avatar_url: str | None = None,
    ) -> ForumReplyCreateResult:
        normalized_topic_id = self._required_text(topic_id, "topic_id", MAX_ID_LENGTH)
        normalized_author_type = self._author_type(author_type)
        normalized_author_id = self._required_text(
            author_id, "author_id", MAX_AUTHOR_ID_LENGTH
        )
        request_id = self._required_text(
            client_request_id, "client_request_id", MAX_ID_LENGTH
        )
        normalized_body = self._required_text(body, "body", MAX_BODY_LENGTH)
        normalized_display_name = self._optional_text(
            author_display_name, "author_display_name",
            MAX_AUTHOR_DISPLAY_NAME_LENGTH,
        )
        normalized_avatar = self._optional_text(
            author_avatar_url, "author_avatar_url",
            MAX_AUTHOR_AVATAR_URL_LENGTH,
        )
        normalized_reply_to_post_id = self._optional_text(
            reply_to_post_id, "reply_to_post_id", MAX_ID_LENGTH
        )
        topic = self._repository.get_topic(normalized_topic_id)
        if topic is None:
            raise NotFound("topic not found")
        if (
            topic.topic_type == TOPIC_TYPE_POLL
            and normalized_reply_to_post_id is not None
        ):
            raise ValidationError(
                "POLL replies must target the Topic; reply_to_post_id is not allowed"
            )
        normalized_poll_selection = self._reply_poll_selection(
            poll_selection, topic
        )
        logger.info(
            "[forum-reply][create] topic_id=%s author_type=%s author_id=%s "
            "client_request_id=%s input_reply_to_post_id=%s",
            normalized_topic_id,
            normalized_author_type,
            normalized_author_id,
            request_id,
            normalized_reply_to_post_id,
        )
        result = self._repository.create_reply(
            topic_id=normalized_topic_id,
            author_type=normalized_author_type,
            author_id=normalized_author_id,
            client_request_id=request_id,
            body=normalized_body,
            reply_to_post_id=normalized_reply_to_post_id,
            poll_selection=normalized_poll_selection,
            author_display_name=normalized_display_name,
            author_avatar_url=normalized_avatar,
        )
        logger.info(
            "[forum-reply][persisted] topic_id=%s post_id=%s "
            "client_request_id=%s stored_reply_to_post_id=%s created=%s",
            normalized_topic_id,
            result.post.post_id,
            request_id,
            result.post.reply_to_post_id,
            result.created,
        )
        return result

    def close_topic(self, *, topic_id: str) -> ForumTopicRecord:
        normalized_topic_id = self._required_text(topic_id, "topic_id", MAX_ID_LENGTH)
        topic = self._repository.get_topic(normalized_topic_id)
        if topic is None:
            raise NotFound(f"topic not found: {normalized_topic_id}")
        if topic.status == TOPIC_STATUS_LOCKED:
            raise Conflict(f"topic is LOCKED, cannot be closed: {normalized_topic_id}")
        if topic.status == TOPIC_STATUS_CLOSED:
            return topic
        updated = self._repository.update_topic_status(
            topic_id=normalized_topic_id,
            status=TOPIC_STATUS_CLOSED,
        )
        if updated is None:
            raise NotFound(f"topic not found: {normalized_topic_id}")
        return updated

    @classmethod
    def _author_type(cls, value: str) -> str:
        normalized = cls._required_text(
            value, "author_type", MAX_AUTHOR_TYPE_LENGTH
        ).upper()
        if normalized not in AUTHOR_TYPES:
            allowed = ", ".join(sorted(AUTHOR_TYPES))
            raise ValidationError(f"author_type must be one of: {allowed}")
        return normalized

    @staticmethod
    def _required_text(value: str, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValidationError(
                f"{field} must contain no more than {max_length} characters"
            )
        return normalized

    @staticmethod
    def _reply_poll_selection(
        value: ForumPollSelection | None, topic: ForumTopicRecord
    ) -> ForumPollSelection | None:
        if topic.topic_type != TOPIC_TYPE_POLL:
            if value is None:
                return None
            raise ValidationError(
                "poll_selection is only allowed when topic_type is POLL"
            )
        if value is None:
            raise ValidationError(
                "poll_selection is required when topic_type is POLL"
            )
        if topic.topic_config is None or topic.topic_config.poll is None:
            raise ValidationError(
                "poll_selection requires topic_config.poll on the addressed Topic"
            )
        poll = topic.topic_config.poll
        indices = tuple(value.option_indices)
        if not indices:
            raise ValidationError(
                "poll_selection.option_indices must contain at least one option"
            )
        if any(
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index >= len(poll.options)
            for index in indices
        ):
            raise ValidationError(
                "poll_selection.option_indices contains an invalid option index"
            )
        if len(indices) != len(set(indices)):
            raise ValidationError(
                "poll_selection.option_indices must not contain duplicates"
            )
        if poll.selection == POLL_SELECTION_SINGLE and len(indices) != 1:
            raise ValidationError(
                "poll_selection.option_indices must contain exactly one option "
                "for SINGLE polls"
            )
        return ForumPollSelection(option_indices=indices)

    @classmethod
    def _topic_config(cls, value: ForumTopicConfig | None) -> ForumTopicConfig | None:
        if value is None:
            return None
        if not isinstance(value, ForumTopicConfig):
            raise ValidationError("topic_config must be a topic configuration object")
        if set(value.__dict__) - {"poll", "share", "help"}:
            raise ValidationError("topic_config contains unsupported fields")
        normalized = ForumTopicConfig(
            poll=cls._poll_config(value.poll),
            share=cls._share_config(value.share),
            help=cls._help_config(value.help),
        )
        configured_sections = sum(
            section is not None
            for section in (normalized.poll, normalized.share, normalized.help)
        )
        if configured_sections > 1:
            raise ValidationError("topic_config must contain exactly one section")
        return None if configured_sections == 0 else normalized

    @staticmethod
    def _validate_topic_config_for_type(
        topic_type: str,
        config: ForumTopicConfig | None,
    ) -> None:
        if topic_type == TOPIC_TYPE_POLL:
            if config is None or config.poll is None:
                raise ValidationError(
                    "topic_config.poll is required when topic_type is POLL"
                )
            return
        if topic_type == TOPIC_TYPE_SHARE:
            if config is None or config.share is None:
                raise ValidationError(
                    "topic_config.share is required when topic_type is SHARE"
                )
            return
        if topic_type == TOPIC_TYPE_HELP:
            if config is not None and config.help is None:
                raise ValidationError(
                    "only topic_config.help is allowed when topic_type is HELP"
                )
            return
        if config is not None:
            raise ValidationError(
                "topic_config is only allowed for POLL, SHARE, or HELP topics"
            )

    @classmethod
    def _share_config(cls, value: ForumShareConfig | None) -> ForumShareConfig | None:
        if value is None:
            return None
        if not isinstance(value, ForumShareConfig):
            raise ValidationError("share must be a share configuration")
        raw_attachment_ids = value.attachment_ids
        if not isinstance(raw_attachment_ids, (list, tuple)):
            raise ValidationError("share.attachment_ids must be a list")
        if not 1 <= len(raw_attachment_ids) <= MAX_SHARE_ATTACHMENT_COUNT:
            raise ValidationError(
                "share.attachment_ids must contain between 1 and "
                f"{MAX_SHARE_ATTACHMENT_COUNT} entries"
            )
        attachment_ids = tuple(
            cls._required_text(
                attachment_id,
                "share.attachment_ids",
                MAX_ID_LENGTH,
            )
            for attachment_id in raw_attachment_ids
        )
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValidationError(
                "share.attachment_ids must not contain duplicates"
            )
        return ForumShareConfig(attachment_ids=attachment_ids)

    @classmethod
    def _help_config(cls, value: ForumHelpConfig | None) -> ForumHelpConfig | None:
        if value is None:
            return None
        if not isinstance(value, ForumHelpConfig):
            raise ValidationError("help must be a help configuration")
        return ForumHelpConfig(
            expected_time=cls._optional_text(
                value.expected_time,
                "help.expected_time",
                MAX_HELP_EXPECTED_TIME_LENGTH,
            )
        )

    @classmethod
    def _poll_config(cls, value: ForumPollConfig | None) -> ForumPollConfig | None:
        if value is None:
            return None
        if not isinstance(value, ForumPollConfig):
            raise ValidationError("poll must be a poll configuration")

        selection = cls._required_text(
            value.selection, "poll.selection", MAX_AUTHOR_TYPE_LENGTH
        ).upper()
        if selection not in POLL_SELECTIONS:
            allowed = ", ".join(
                sorted((POLL_SELECTION_MULTI, POLL_SELECTION_SINGLE))
            )
            raise ValidationError(f"poll.selection must be one of: {allowed}")

        raw_options = value.options
        if not isinstance(raw_options, (list, tuple)):
            raise ValidationError("poll.options must be a list")
        if not MIN_POLL_OPTION_COUNT <= len(raw_options) <= MAX_POLL_OPTION_COUNT:
            raise ValidationError(
                "poll.options must contain between "
                f"{MIN_POLL_OPTION_COUNT} and {MAX_POLL_OPTION_COUNT} entries"
            )
        options = tuple(
            cls._required_text(option, "poll.options", MAX_POLL_OPTION_LENGTH)
            for option in raw_options
        )
        if len(options) != len(set(options)):
            raise ValidationError("poll.options must not contain duplicates")

        deadline = value.deadline_at
        if deadline is not None and (
            not isinstance(deadline, datetime) or deadline.tzinfo is None
        ):
            raise ValidationError("poll.deadline_at must include a timezone")

        return ForumPollConfig(
            selection=selection,
            options=options,
            deadline_at=deadline,
        )

    @classmethod
    def _topic_type(cls, value: str | None) -> str | None:
        normalized = cls._optional_text(value, "topic_type", MAX_AUTHOR_TYPE_LENGTH)
        if normalized is None:
            return None
        normalized = normalized.upper()
        if normalized not in TOPIC_TYPES:
            allowed = ", ".join(sorted(TOPIC_TYPES))
            raise ValidationError(f"topic_type must be one of: {allowed}")
        return normalized

    @classmethod
    def _topic_status(cls, value: str | None) -> str | None:
        normalized = cls._optional_text(value, "status", MAX_AUTHOR_TYPE_LENGTH)
        if normalized is None:
            return None
        normalized = normalized.upper()
        if normalized not in TOPIC_STATUSES:
            allowed = ", ".join(sorted(TOPIC_STATUSES))
            raise ValidationError(f"status must be one of: {allowed}")
        return normalized

    @staticmethod
    def _optional_text(value: str | None, field: str, max_length: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise ValidationError(
                f"{field} must contain no more than {max_length} characters"
            )
        return normalized

    @staticmethod
    def _pagination(page: int, page_size: int) -> None:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValidationError("page must be at least 1")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or page_size < 1
            or page_size > 100
        ):
            raise ValidationError("page_size must be between 1 and 100")

    # ------------------------------------------------------------------
    # BBS Browse Loop — subscription + feed service glue
    # ------------------------------------------------------------------

    def upsert_subscription(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        mode: str,
        note: str | None = None,
    ) -> BrowseSubscriptionUpsertResult:
        normalized_bot_id = self._required_text(bot_id, "bot_id", MAX_AUTHOR_ID_LENGTH)
        normalized_owner = self._required_text(owner_user_id, "owner_user_id", 64)
        normalized_mode = self._browse_mode(mode)
        normalized_note = (
            None
            if note is None
            else self._optional_text(note, "note", MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH)
        )
        return self._repository.upsert_subscription(
            bot_id=normalized_bot_id,
            owner_user_id=normalized_owner,
            mode=normalized_mode,
            note=normalized_note,
        )

    def get_subscription(self, *, bot_id: str) -> BrowseSubscriptionRecord | None:
        normalized = self._required_text(bot_id, "bot_id", MAX_AUTHOR_ID_LENGTH)
        return self._repository.get_subscription(normalized)

    def list_subscriptions(
        self,
        *,
        page: int,
        page_size: int,
        mode: str | None = None,
        owner_user_id: str | None = None,
    ) -> BrowseSubscriptionPage:
        normalized_mode = None if mode is None else self._browse_mode(mode)
        normalized_owner = (
            None
            if owner_user_id is None
            else self._required_text(
                owner_user_id, "owner_user_id", MAX_AUTHOR_ID_LENGTH
            ).strip()
        )
        self._pagination(page, page_size)
        return self._repository.list_subscriptions(
            offset=(page - 1) * page_size,
            limit=page_size,
            mode=normalized_mode,
            owner_user_id=normalized_owner,
        )

    def delete_subscription(self, *, bot_id: str) -> bool:
        normalized = self._required_text(bot_id, "bot_id", MAX_AUTHOR_ID_LENGTH)
        return self._repository.delete_subscription(normalized)

    def list_browse_feed(
        self,
        *,
        bot_id: str,
        status: str | None,
        topic_type: str | None,
        page: int,
        page_size: int,
    ) -> BrowseFeedPage:
        normalized_bot_id = self._required_text(bot_id, "bot_id", MAX_AUTHOR_ID_LENGTH)
        normalized_status = self._topic_status(status)
        normalized_topic_type = self._topic_type(topic_type)
        self._pagination(page, page_size)
        return self._repository.list_browse_feed(
            bot_id=normalized_bot_id,
            status=normalized_status,
            topic_type=normalized_topic_type,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    @classmethod
    def _browse_mode(cls, value: str) -> str:
        normalized = cls._required_text(value, "mode", MAX_AUTHOR_TYPE_LENGTH).lower()
        if normalized not in BROWSE_MODES:
            allowed = ", ".join(sorted(BROWSE_MODES))
            raise ValidationError(f"mode must be one of: {allowed}")
        return normalized


    @classmethod
    def _browse_report_status(cls, value: str) -> str:
        normalized = cls._required_text(
            value, "status", MAX_AUTHOR_TYPE_LENGTH
        ).upper()
        if normalized not in BROWSE_REPORT_STATUSES:
            allowed = ", ".join(sorted(BROWSE_REPORT_STATUSES))
            raise ValidationError(f"status must be one of: {allowed}")
        return normalized

    def submit_browse_report(
        self,
        *,
        bot_id: str,
        status: str,
        client_request_id: str,
        message: str | None = None,
    ) -> BrowseReportCreateResult:
        normalized_bot_id = self._required_text(bot_id, "bot_id", MAX_ID_LENGTH)
        normalized_status = self._browse_report_status(status)
        request_id = self._required_text(
            client_request_id, "client_request_id", MAX_ID_LENGTH
        )
        normalized_message = self._optional_text(
            message, "message", MAX_BROWSE_REPORT_MESSAGE_LENGTH
        )
        return self._repository.create_browse_report(
            bot_id=normalized_bot_id,
            status=normalized_status,
            client_request_id=request_id,
            message=normalized_message,
        )
