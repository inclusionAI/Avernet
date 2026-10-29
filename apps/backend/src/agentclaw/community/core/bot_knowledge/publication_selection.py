"""Best-effort version observations; never a publish or runtime prerequisite."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.core.bot_knowledge.repository.store import (
    require_editor,
    require_space,
    resolve_bot,
)
from agentclaw.community.core.repository.protocols.config import (
    BotCommonConfigRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishStatus,
)
from agentclaw.community.kernel.knowledge import (
    BotKnowledgeScope,
    KnowledgeError,
    require_knowledge_id,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotCommonConfig, BotModel
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()
_MAX_BYTES = 65535


class _SelectionRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[1]
    avernet_tenant: str = Field(min_length=1, max_length=64)
    env: str = Field(min_length=1, max_length=128)
    source_bot_pk: int = Field(gt=0)
    space_id: int = Field(gt=0)
    publication_id: int = Field(gt=0)
    version: int = Field(gt=0)
    kb_ids: list[str] = Field(max_length=100)
    captured_at: AwareDatetime

    @field_validator("kb_ids")
    @classmethod
    def valid_ids(cls, value: list[str]) -> list[str]:
        for kb_id in value:
            require_knowledge_id(kb_id, "kb_id")
        if value != sorted(set(value)):
            raise ValueError("knowledge IDs must be sorted and unique")
        return value


def _identity(bot: BotModel, publication: BotPublishModel) -> dict:
    return dict(
        schema_version=1,
        avernet_tenant=bot.avernet_tenant,
        env=bot.env,
        source_bot_pk=bot.id,
        space_id=bot.space_id,
        publication_id=publication.id,
        version=publication.version,
    )


def _coordinates(identity: dict) -> dict[str, str]:
    return dict(
        bot_id=f"knowledge:{identity['source_bot_pk']}",
        entity_id="tenant:"
        + sha256(identity["avernet_tenant"].encode("utf-8")).hexdigest(),
        env=identity["env"],
        config_key=f"publication:{identity['publication_id']}",
    )


def _require_publication_id(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError("publication_id must be a positive integer")


class BotKnowledgePublicationSelection:
    def __init__(
        self, db: DatabasePlugin, configs: BotCommonConfigRepositoryProtocol
    ) -> None:
        self.db, self.configs = db, configs

    def record_build_selection(self, *, publication_id: int) -> bool:
        """Independent optional write. False is deliberately NOT publish failure.

        Rebuilding a version replaces its last observation when this succeeds;
        failed writes may leave an older observation with its original timestamp.
        No transaction, task result or publish ext is shared with the caller.
        """
        try:
            _require_publication_id(publication_id)
            with self.db.orm_session() as session:
                publication = (
                    session.query(BotPublishModel)
                    .filter_by(
                        id=publication_id,
                        env=get_current_env(),
                        status=PublishStatus.BUILDING,
                    )
                    .one_or_none()
                )
                if publication is None:
                    raise KnowledgeError("PUBLICATION_NOT_BUILDING", 404)
                bot = (
                    session.query(BotModel)
                    .filter_by(
                        id=publication.source_bot_pk,
                        bot_id=publication.source_bot_id,
                        env=publication.env,
                        is_delete=0,
                        bot_type="service",
                    )
                    .one_or_none()
                )
                if bot is None:
                    raise KnowledgeError("BOT_NOT_FOUND", 404)
                require_space(session, bot)
                identity = _identity(bot, publication)
                ids = (
                    session.query(BotKnowledgeBindingModel.kb_id)
                    .filter_by(
                        bot_pk=bot.id,
                        env=bot.env,
                        space_id=bot.space_id,
                        enabled=True,
                    )
                    .order_by(BotKnowledgeBindingModel.kb_id)
                    .limit(101)
                    .all()
                )
                payload = _SelectionRecord(
                    **identity,
                    kb_ids=sorted(row[0] for row in ids),
                    captured_at=datetime.now(UTC),
                ).model_dump_json()
            if len(payload.encode("utf-8")) > _MAX_BYTES:
                raise ValueError("knowledge observation exceeds storage limit")
            self.configs.put(**_coordinates(identity), config_value=payload)
            return True
        except Exception:
            logger.exception(
                "Knowledge observation not recorded: publication_id=%s", publication_id
            )
            return False

    def _read_record(self, identity: dict) -> str | None:
        # Generic configs.get treats a missing table as absence for legacy
        # policy callers. Observations must distinguish that outage from an
        # ordinary historical record that was never captured.
        with self.db.orm_session() as session:
            row = (
                session.query(BotCommonConfig)
                .filter_by(
                    **_coordinates(identity),
                    is_delete=0,
                )
                .one_or_none()
            )
            return row.config_value if row is not None else None

    def get_publication_selection(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        publication_id: int,
    ) -> dict:
        _require_publication_id(publication_id)
        # Access/resource checks are NOT optional observation-store operations.
        with self.db.orm_session() as session:
            bot = resolve_bot(session, scope)
            require_editor(session, bot, actor_id)
            if bot.bot_type != "service":
                raise KnowledgeError("SERVICE_BOT_REQUIRED", 400)
            publication = (
                session.query(BotPublishModel)
                .filter_by(
                    id=publication_id,
                    source_bot_pk=bot.id,
                    source_bot_id=bot.bot_id,
                    env=bot.env,
                )
                .one_or_none()
            )
            if publication is None:
                raise KnowledgeError("PUBLICATION_NOT_FOUND", 404)
            identity = _identity(bot, publication)
        result = dict(
            publication_id=publication_id,
            version=identity["version"],
            observation_status="unrecorded",
            kb_ids=None,
            captured_at=None,
        )
        try:
            raw = self._read_record(identity)
        except Exception:
            logger.exception(
                "Knowledge observation unavailable: publication_id=%s", publication_id
            )
            return {**result, "observation_status": "unavailable"}
        if raw is None:
            return result
        try:
            if len(raw.encode("utf-8")) > _MAX_BYTES:
                raise ValueError("knowledge observation exceeds storage limit")
            stored = _SelectionRecord.model_validate_json(raw)
            if any(getattr(stored, key) != value for key, value in identity.items()):
                raise ValueError("knowledge observation identity mismatch")
        except (ValueError, TypeError, AttributeError):
            logger.warning(
                "Invalid knowledge observation: publication_id=%s", publication_id
            )
            return result
        return {
            **result,
            "observation_status": "last_recorded",
            "kb_ids": stored.kb_ids,
            "captured_at": stored.model_dump(mode="json")["captured_at"],
        }
