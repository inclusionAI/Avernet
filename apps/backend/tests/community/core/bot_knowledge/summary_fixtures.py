"""Explicit current-result fixtures; raw legacy flags are not a current summary."""

import json

from agentclaw.community.core.bot_knowledge.repository.online_selection import (
    config_scope,
)
from agentclaw.community.kernel.knowledge import KNOWLEDGE_ONLINE_CONFIG_KEY
from agentclaw.community.plugin_api.models import BotCommonConfig, BotModel


def seed_summary(session, source, publish, ids, workflow=0):
    scope = config_scope(source.id, source.bot_id, source.entity_id, source.env)
    row = session.query(BotCommonConfig).filter_by(**scope).one_or_none()
    if row is None:
        row = BotCommonConfig(**scope, config_value="{}")
        session.add(row)
    row.config_value = json.dumps(
        dict(
            schema_version=1,
            source_bot_pk=source.id,
            source_bot_id=source.bot_id,
            source_entity_id=source.entity_id,
            env=source.env,
            publish_id=publish.id,
            deployment_id=workflow,
            status="applied",
            owner_id=source.owner_id,
            avernet_tenant=source.avernet_tenant,
            space_id=source.space_id,
            enabled_kb_ids=sorted(ids),
            materialized=dict(
                avernet_tenant=source.avernet_tenant,
                space_id=source.space_id,
                kb_ids=sorted(ids),
            ),
        )
    )
    return row


def seed_online(db, publish, ids, workflow=0):
    with db.orm_session() as session:
        source = session.get(BotModel, publish.source_bot_pk)
        seed_summary(session, source, publish, ids, workflow)


def current_row(session):
    return (
        session.query(BotCommonConfig)
        .filter_by(
            config_key=KNOWLEDGE_ONLINE_CONFIG_KEY,
        )
        .one()
    )
