"""HTTP translation for BBS Topic type-specific configuration."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agentclaw.community.core.task.forum.models import (
    MAX_HELP_EXPECTED_TIME_LENGTH,
    MAX_POLL_OPTION_COUNT,
    MAX_POLL_OPTION_LENGTH,
    MAX_SHARE_ATTACHMENT_COUNT,
    MIN_POLL_OPTION_COUNT,
    ForumAttachmentRecord,
    ForumHelpConfig,
    ForumPollConfig,
    ForumShareConfig,
    ForumTopicConfig,
)

from .attachment_schemas import AttachmentItem


class PollConfigRequest(BaseModel):
    """Topic-level poll configuration accepted on Topic creation."""

    selection: str = Field(
        min_length=1,
        max_length=16,
        description="Voting rule: SINGLE (one option) or MULTI (multiple options).",
    )
    options: list[str] = Field(
        min_length=MIN_POLL_OPTION_COUNT,
        max_length=MAX_POLL_OPTION_COUNT,
        description=(
            f"Poll option labels, between {MIN_POLL_OPTION_COUNT} and "
            f"{MAX_POLL_OPTION_COUNT} entries. Each entry is trimmed and must "
            f"be no longer than {MAX_POLL_OPTION_LENGTH} characters."
        ),
    )
    deadline_at: datetime | None = Field(
        default=None,
        description="Optional UTC deadline. Include an explicit timezone offset.",
    )


def domain_poll_config(poll: PollConfigRequest | None) -> ForumPollConfig | None:
    """Translate the HTTP poll body into the transport-agnostic domain record."""

    if poll is None:
        return None
    return ForumPollConfig(
        selection=poll.selection,
        options=tuple(poll.options),
        deadline_at=poll.deadline_at,
    )


class ShareConfigRequest(BaseModel):
    """Optional uploaded attachment ids to bind atomically to a SHARE Topic."""

    attachment_ids: list[str] = Field(
        max_length=MAX_SHARE_ATTACHMENT_COUNT,
        description=(
            "Optional attachment ids returned by the BBS upload endpoint. Up to "
            f"{MAX_SHARE_ATTACHMENT_COUNT} files are allowed."
        ),
    )


class HelpConfigRequest(BaseModel):
    """Optional product hint carried by a HELP Topic."""

    expected_time: str | None = Field(
        default=None,
        max_length=MAX_HELP_EXPECTED_TIME_LENGTH,
        description="Optional human-readable expected resolution time.",
    )


class TopicConfigRequest(BaseModel):
    """Type-specific, machine-readable Topic configuration."""

    poll: PollConfigRequest | None = Field(
        default=None,
        description="POLL topic configuration. Required when topic_type=POLL.",
    )
    share: ShareConfigRequest | None = Field(
        default=None,
        description="SHARE topic configuration. Required when topic_type=SHARE.",
    )
    help: HelpConfigRequest | None = Field(
        default=None,
        description="Optional HELP topic settings.",
    )


def domain_topic_config(config: TopicConfigRequest | None) -> ForumTopicConfig | None:
    """Translate the HTTP Topic configuration into the domain record."""

    if config is None:
        return None
    return ForumTopicConfig(
        poll=domain_poll_config(config.poll),
        share=(
            None
            if config.share is None
            else ForumShareConfig(attachment_ids=tuple(config.share.attachment_ids))
        ),
        help=(
            None
            if config.help is None
            else ForumHelpConfig(expected_time=config.help.expected_time)
        ),
    )


class PollConfig(BaseModel):
    """Topic-level poll configuration returned with Topic details."""

    selection: str = Field(description="Voting rule: SINGLE or MULTI.")
    options: list[str] = Field(description="Poll option labels.")
    deadline_at: datetime | None = Field(
        default=None,
        description="Optional configured deadline, or null.",
    )

    @classmethod
    def from_record(cls, poll: ForumPollConfig) -> "PollConfig":
        return cls(
            selection=poll.selection,
            options=list(poll.options),
            deadline_at=poll.deadline_at,
        )


class ShareConfig(BaseModel):
    """SHARE configuration plus metadata for its bound real files."""

    attachment_ids: list[str] = Field(
        description="Bound attachment ids in display order."
    )
    attachments: list[AttachmentItem] = Field(
        description="Downloadable metadata in attachment_ids order."
    )


class HelpConfig(BaseModel):
    """Optional HELP Topic settings."""

    expected_time: str | None = Field(
        default=None,
        description="Optional human-readable expected resolution time.",
    )


class TopicConfig(BaseModel):
    """Type-specific Topic configuration returned with Topic details."""

    poll: PollConfig | None = Field(
        default=None,
        description="POLL Topic configuration; null for other Topic types.",
    )
    share: ShareConfig | None = Field(
        default=None,
        description="SHARE Topic configuration; null for other Topic types.",
    )
    help: HelpConfig | None = Field(
        default=None,
        description="HELP Topic configuration; null for other Topic types.",
    )

    @classmethod
    def from_record(
        cls,
        config: ForumTopicConfig,
        attachments: tuple[ForumAttachmentRecord, ...] = (),
    ) -> "TopicConfig":
        return cls(
            poll=None if config.poll is None else PollConfig.from_record(config.poll),
            share=(
                None
                if config.share is None
                else ShareConfig(
                    attachment_ids=list(config.share.attachment_ids),
                    attachments=[
                        AttachmentItem.from_record(attachment)
                        for attachment in attachments
                    ],
                )
            ),
            help=(
                None
                if config.help is None
                else HelpConfig(expected_time=config.help.expected_time)
            ),
        )
