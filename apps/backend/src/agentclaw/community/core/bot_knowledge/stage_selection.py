"""Bounded auxiliary stage state. Native publication state is read, never written."""

import json

from sqlalchemy import update

from agentclaw.community.core.bot_knowledge.publication_selection import (
    SelectionRecord,
    selection_coordinates,
    selection_identity,
)
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.bot_knowledge.repository.store import require_space
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel as Publication,
    PublishStatus,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotCommonConfig, BotModel
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

logger = get_logger()


def _workflow(publication) -> int:
    ext = json.loads(publication.ext or "{}")
    value = ext.get("publish", {}).get("online")
    if type(value) is not int or value <= 0:
        raise ValueError("missing native online workflow")
    return value


def _record(raw, identity):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65535:
        raise ValueError("invalid knowledge snapshot")
    value = SelectionRecord.model_validate_json(raw)
    if any(getattr(value, key) != expected for key, expected in identity.items()):
        raise ValueError("knowledge snapshot identity mismatch")
    return value


def _config(session, identity, key=None, *, lock=False):
    coordinates = selection_coordinates(identity)
    if key is not None:
        coordinates["config_key"] = key
    query = session.query(BotCommonConfig).filter_by(**coordinates, is_delete=0)
    return (query.with_for_update() if lock else query).one_or_none()


class BotKnowledgeStageSelection:
    def __init__(self, db: DatabasePlugin):
        self.db = db

    def read(self, *, bot, runtime_stage):
        if runtime_stage not in {"draft", "verify", "online"}:
            raise KnowledgeError("INVALID_KNOWLEDGE_INPUT", 400)
        if bot["bot_type"] != "service":
            if runtime_stage != "draft":
                raise KnowledgeError("INVALID_KNOWLEDGE_INPUT", 400)
            return None  # Preserve the personal/current wire contract exactly.
        result = dict(
            selection_status="current" if runtime_stage == "draft" else "unrecorded",
            publication_id=None,
            captured_at=None,
            kb_ids=None,
        )
        if runtime_stage == "draft":
            return result
        try:
            with self.db.orm_session() as session:
                query = session.query(Publication).filter_by(
                    source_bot_pk=bot["id"], source_bot_id=bot["bot_id"], env=bot["env"]
                )
                if runtime_stage == "online":
                    query = query.filter_by(status=PublishStatus.SUCCESS)
                else:
                    # Only the latest candidate may represent prepublication;
                    # never resurrect an older validating row behind a draft.
                    query = query.order_by(
                        Publication.version.desc(), Publication.id.desc()
                    )
                publication = query.order_by(
                    Publication.version.desc(), Publication.id.desc()
                ).first()
                if publication is None or (
                    runtime_stage == "verify"
                    and publication.status
                    not in {
                        PublishStatus.VALIDATING,
                        PublishStatus.ONLINE_PUB,
                    }
                ):
                    return result
                result["publication_id"] = publication.id
                identity = dict(
                    schema_version=1,
                    avernet_tenant=get_current_avernet_tenant(),
                    env=bot["env"],
                    source_bot_pk=bot["id"],
                    space_id=bot["space_id"],
                    publication_id=publication.id,
                    version=publication.version,
                )
                if runtime_stage == "verify":
                    row = _config(session, identity)
                    if row is None:
                        return result
                    value = _record(row.config_value, identity)
                    ids = value.kb_ids
                else:
                    row = _config(session, identity, "projection:online")
                    if row is None:
                        return result
                    marker = json.loads(row.config_value)
                    if type(marker["workflow_id"]) is not int or marker[
                        "workflow_id"
                    ] != _workflow(publication):
                        return result
                    value = _record(json.dumps(marker["selection"]), identity)
                    # Read the projection through the same binding rows; a marker
                    # is provenance, not a substitute for persisted enablement.
                    rows = (
                        session.query(Binding.kb_id)
                        .filter(
                            Binding.bot_pk == bot["id"],
                            Binding.env == bot["env"],
                            Binding.space_id == bot["space_id"],
                            Binding.kb_id.in_(value.kb_ids),
                            Binding.online_enabled.is_(True),
                        )
                        .limit(101)
                        .all()
                        if value.kb_ids
                        else []
                    )
                    ids = sorted(row[0] for row in rows)
                    if ids != value.kb_ids:
                        return result
                return dict(
                    selection_status="last_recorded",
                    publication_id=publication.id,
                    captured_at=value.model_dump(mode="json")["captured_at"],
                    kb_ids=ids,
                )
        except (ValueError, KeyError, TypeError, AttributeError):
            return result
        except Exception:
            logger.exception(
                "Knowledge stage selection unavailable: bot_pk=%s", bot["id"]
            )
            return {**result, "selection_status": "unavailable"}

    def project_online(self, *, publication_id: int) -> bool:
        try:
            with self.db.orm_session() as session:
                publication = (
                    session.query(Publication)
                    .filter_by(
                        id=publication_id,
                        env=get_current_env(),
                        status=PublishStatus.SUCCESS,
                    )
                    .one_or_none()
                )
                if publication is None:
                    return False
                bot = (
                    session.query(BotModel)
                    .filter_by(
                        id=publication.source_bot_pk,
                        bot_id=publication.source_bot_id,
                        env=publication.env,
                        bot_type="service",
                        is_delete=0,
                    )
                    .one_or_none()
                )
                if bot is None:
                    return False
                identity = selection_identity(bot, publication) | {
                    "space_id": require_space(session, bot)
                }
                marker = _config(session, identity, "projection:online", lock=True)
                # Serialize only auxiliary writes. Never lock or write native rows.
                if marker is not None:
                    session.execute(
                        update(BotCommonConfig)
                        .where(BotCommonConfig.id == marker.id)
                        .values(id=marker.id)
                    )
                session.refresh(publication)
                if publication.status != PublishStatus.SUCCESS:
                    return False
                current = (
                    session.query(Publication.id)
                    .filter_by(
                        source_bot_pk=bot.id,
                        source_bot_id=bot.bot_id,
                        env=bot.env,
                        status=PublishStatus.SUCCESS,
                    )
                    .order_by(Publication.version.desc(), Publication.id.desc())
                    .first()
                )
                if current is None or current[0] != publication_id:
                    return False
                workflow = _workflow(publication)
                snapshot = _config(session, identity)
                if snapshot is None:
                    return False
                value = _record(snapshot.config_value, identity)
                previous_ids = []
                if marker is not None:
                    previous = json.loads(marker.config_value)
                    # A delayed older deploy must not replace a later rollback.
                    if (
                        type(previous["workflow_id"]) is not int
                        or previous["workflow_id"] > workflow
                    ):
                        return False
                    previous_selection = SelectionRecord.model_validate_json(
                        json.dumps(previous["selection"])
                    )
                    if (
                        previous_selection.source_bot_pk != bot.id
                        or previous_selection.env != bot.env
                        or previous_selection.avernet_tenant != bot.avernet_tenant
                    ):
                        raise ValueError("foreign projection marker")
                    previous_ids = previous_selection.kb_ids
                ids = sorted(set(previous_ids) | set(value.kb_ids))
                rows = (
                    session.query(Binding)
                    .filter(
                        Binding.bot_pk == bot.id,
                        Binding.env == bot.env,
                        Binding.kb_id.in_(ids),
                    )
                    .limit(201)
                    .all()
                    if ids
                    else []
                )
                existing = {row.kb_id: row for row in rows}
                for kb_id in ids:
                    row = existing.get(kb_id)
                    if row is None:
                        row = Binding(
                            env=bot.env,
                            bot_pk=bot.id,
                            space_id=identity["space_id"],
                            kb_id=kb_id,
                            enabled=False,
                            revision=0,
                            updated_by=bot.owner_id,
                        )
                        session.add(row)
                    if row.space_id != identity["space_id"]:
                        if kb_id in value.kb_ids:
                            raise ValueError(
                                "projection binding moved to another space"
                            )
                        continue
                    row.online_enabled = kb_id in value.kb_ids
                payload = json.dumps(
                    dict(workflow_id=workflow, selection=value.model_dump(mode="json"))
                )
                if len(payload.encode("utf-8")) > 65535:
                    raise ValueError("online projection exceeds storage limit")
                if marker is None:
                    coordinates = selection_coordinates(identity) | {
                        "config_key": "projection:online"
                    }
                    session.add(BotCommonConfig(**coordinates, config_value=payload))
                else:
                    marker.config_value = payload
            return True
        except Exception:
            logger.exception(
                "Knowledge online projection not saved: publication_id=%s",
                publication_id,
            )
            return False
