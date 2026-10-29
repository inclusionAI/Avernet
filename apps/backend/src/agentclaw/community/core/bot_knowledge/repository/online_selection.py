"""One bounded current publication result in the existing Bot common config.

The global, immutable source PK is the tenant authority (as for native publish
rows). No current Owner is part of the key: handoff must not fork the watermark.
The payload independently validates native identity and tenant/Owner/space.
Caller owns the Session, locks and commit; no history scan or remote I/O.
"""

from __future__ import annotations

import json
from hashlib import sha256
from dataclasses import dataclass

from sqlalchemy import LargeBinary, cast, literal, select

from agentclaw.community.kernel.knowledge import (
    KNOWLEDGE_ONLINE_CONFIG_KEY,
    KnowledgeError,
    require_knowledge_id,
)
from agentclaw.community.plugin_api.models import BotCommonConfig

MAX_IDS = 100


def config_scope(source_pk, bot_id, entity_id, env):
    # A resource-PK namespace also avoids truncating native entity IDs (up to
    # 1024 chars) into common-config.entity_id (128 chars). No new UUID is made.
    return dict(
        bot_id=bot_id,
        entity_id=f"source-pk:{source_pk}:{sha256(entity_id.encode()).hexdigest()}",
        env=env,
        config_key=KNOWLEDGE_ONLINE_CONFIG_KEY,
    )


def _ids(value):
    if (
        not isinstance(value, list)
        or len(value) > MAX_IDS
        or any(not isinstance(kb, str) for kb in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("invalid current selection IDs")
    for kb in value:
        require_knowledge_id(kb, "kb_id")
    return value


def decode_summary(raw, *, source_pk, bot_id, entity_id, env):
    """Missing/corrupt state is not a legacy flag or historical fallback."""
    try:
        if raw is not None and len(raw.encode("utf-8")) > 65535:
            return None
        value = json.loads(raw) if raw else None
        if (
            not isinstance(value, dict)
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
        ):
            return None
        for key, expected in dict(
            source_bot_pk=source_pk,
            source_bot_id=bot_id,
            source_entity_id=entity_id,
            env=env,
        ).items():
            if type(value.get(key)) is not type(expected) or value[key] != expected:
                return None
        if (
            value.get("status") not in ("applied", "unknown")
            or type(value.get("deployment_id")) is not int
            or value["deployment_id"] < 0
            or type(value.get("publish_id")) is not int
            or value["publish_id"] <= 0
        ):
            return None
        _ids(value.get("enabled_kb_ids"))
        materialized = value.get("materialized")
        if materialized is not None:
            if (
                not isinstance(materialized, dict)
                or not isinstance(materialized.get("avernet_tenant"), str)
                or type(materialized.get("space_id")) not in (int, type(None))
            ):
                return None
            if "kb_ids" not in materialized and value["status"] == "applied":
                # Applied state has exactly one ID set. Persisting it twice can
                # overflow TEXT for 100 valid multibyte 128-character IDs.
                materialized["kb_ids"] = value["enabled_kb_ids"]
            _ids(materialized.get("kb_ids"))
        if value["status"] == "applied":
            if (
                not isinstance(value.get("owner_id"), str)
                or not value["owner_id"]
                or not isinstance(value.get("avernet_tenant"), str)
                or not 1 <= len(value["avernet_tenant"]) <= 64
                or type(value.get("space_id")) not in (int, type(None))
                or materialized is None
                or materialized["avernet_tenant"] != value["avernet_tenant"]
                or materialized["space_id"] != value["space_id"]
                or set(materialized["kb_ids"]) != set(value["enabled_kb_ids"])
            ):
                return None
        elif value["enabled_kb_ids"]:
            return None
        return value
    except (ValueError, TypeError, RecursionError):
        return None


@dataclass(frozen=True)
class OnlineSelection:
    scope: dict
    raw: str | None
    value: dict | None
    ids: tuple[str, ...]

    def unchanged(self, session):
        """Re-check the exact summary in the SAME flags/page SQL statement."""
        if not self.ids or self.raw is None:
            return literal(False)
        current = (
            select(BotCommonConfig.config_value)
            .filter_by(
                **self.scope,
                is_delete=0,
            )
            .scalar_subquery()
        )
        # MySQL TEXT often inherits a case-insensitive collation. A bytewise
        # comparison is required for an exact snapshot fence, not just JSON text
        # that differs in case. SQLite/PostgreSQL use their text equality here.
        if session.get_bind().dialect.name == "mysql":
            return cast(current, LargeBinary) == self.raw.encode("utf-8")
        return current == self.raw


def read_selection(session, bot):
    scope = config_scope(bot.id, bot.bot_id, bot.entity_id, bot.env)
    row = session.query(BotCommonConfig).filter_by(**scope, is_delete=0).one_or_none()
    raw = row.config_value if row is not None else None
    value = decode_summary(
        raw,
        source_pk=bot.id,
        bot_id=bot.bot_id,
        entity_id=bot.entity_id,
        env=bot.env,
    )
    ids = ()
    if (
        value is not None
        and value["status"] == "applied"
        and all(
            type(value.get(key)) is type(expected) and value.get(key) == expected
            for key, expected in dict(
                avernet_tenant=bot.avernet_tenant,
                owner_id=bot.owner_id,
                space_id=bot.space_id,
            ).items()
        )
    ):
        ids = tuple(value["enabled_kb_ids"])
    return OnlineSelection(scope, raw, value, ids)


def lock_summary(session, publish):
    """A current locking read after the shared Bot lock, including on MySQL RR."""
    scope = config_scope(
        publish.source_bot_pk, publish.source_bot_id, publish.owner_id, publish.env
    )
    row = (
        session.query(BotCommonConfig)
        .filter_by(**scope)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    value = decode_summary(
        row.config_value if row is not None and not row.is_delete else None,
        source_pk=publish.source_bot_pk,
        bot_id=publish.source_bot_id,
        entity_id=publish.owner_id,
        env=publish.env,
    )
    return row, value


def write_summary(session, row, *, publish, source, outcome, ids, materialized):
    materialized = dict(materialized) if materialized is not None else None
    if materialized is not None and outcome["status"] == "applied":
        materialized.pop("kb_ids", None)  # decode from the sole enabled_kb_ids set
    value = dict(
        schema_version=1,
        source_bot_pk=publish.source_bot_pk,
        source_bot_id=publish.source_bot_id,
        source_entity_id=publish.owner_id,
        env=publish.env,
        publish_id=publish.id,
        **outcome,
        avernet_tenant=source.avernet_tenant if source is not None else None,
        space_id=source.space_id if source is not None else None,
        enabled_kb_ids=sorted(ids),
        materialized=materialized,
    )
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > 65535:
        raise KnowledgeError("KNOWLEDGE_CURRENT_SUMMARY_TOO_LARGE")
    if row is None:
        row = BotCommonConfig(
            **config_scope(
                publish.source_bot_pk,
                publish.source_bot_id,
                publish.owner_id,
                publish.env,
            ),
            config_value=encoded,
        )
        session.add(row)
    else:
        row.config_value = encoded
        row.is_delete = 0
    # Deliberately flush in the caller's transaction. Never independent put().
    session.flush()
