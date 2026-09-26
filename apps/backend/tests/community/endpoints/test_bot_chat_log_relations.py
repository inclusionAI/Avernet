"""Endpoint coverage for bot-chat biz relation ingest."""
from __future__ import annotations

from agentclaw.community.core.bot_chat.models import AcOtelLogBizRef
from agentclaw.community.core.repository.implementations.chat.db import BotChatDbRepository
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_PATH = "/api/bot-chat/log-relations"


_BODY = {
    "biz_scene": "harness_eval",
    "biz_task_id": "case-001",
    "engine": "openclaw",
    "collector": "ocb_scheduler",
    "user_id": "197444",
    "bot_id": "default",
    "refs": [
        {
            "ref_type": "session_key",
            "ref_value": "agent:main:session:abc:user:197444",
        },
        {
            "ref_type": "trace_id",
            "ref_value": "trace-biz-relation-001",
            "metadata": {"source_field": "traceId"},
        },
    ],
    "metadata": {
        "source": "harness",
    },
}


def _assert_persisted(response, world) -> None:
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        rows = (
            session.query(AcOtelLogBizRef)
            .filter(
                AcOtelLogBizRef.biz_scene == "harness_eval",
                AcOtelLogBizRef.biz_task_id == "case-001",
            )
            .order_by(AcOtelLogBizRef.ref_type.asc())
            .all()
        )
        values = [
            {
                "ref_type": row.ref_type,
                "ref_value": row.ref_value,
                "engine": row.engine,
                "collector": row.collector,
                "user_id": row.user_id,
                "bot_id": row.bot_id,
            }
            for row in rows
        ]

    assert len(values) == 2
    assert values == [
        {
            "ref_type": "session_key",
            "ref_value": "agent:main:session:abc:user:197444",
            "engine": "openclaw",
            "collector": "ocb_scheduler",
            "user_id": "197444",
            "bot_id": "default",
        },
        {
            "ref_type": "trace_id",
            "ref_value": "trace-biz-relation-001",
            "engine": "openclaw",
            "collector": "ocb_scheduler",
            "user_id": "197444",
            "bot_id": "default",
        },
    ]


def _seed_existing(world) -> None:
    db = world.get(DatabasePlugin)
    BotChatDbRepository(db).upsert_biz_refs(_BODY)


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="happy",
    input=CaseInput(json_body=_BODY),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "inserted": 2,
                "updated": 0,
                "total": 2,
            },
        },
    ),
    extra_assertions=(_assert_persisted,),
)
def bot_chat_log_relations_happy():
    """Declarative case; the framework owns invocation."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="idempotent",
    seed=_seed_existing,
    input=CaseInput(json_body=_BODY),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "inserted": 0,
                "updated": 2,
                "total": 2,
            },
        },
    ),
    extra_assertions=(_assert_persisted,),
)
def bot_chat_log_relations_idempotent():
    """Declarative case; the framework owns invocation."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_refs",
    input=CaseInput(
        json_body={
            "biz_scene": "harness_eval",
            "biz_task_id": "case-001",
            "refs": [],
        }
    ),
    expect=ExpectError(
        status=422,
        json_contains={
            "detail": [
                {
                    "loc": ["body", "refs"],
                }
            ]
        },
    ),
)
def bot_chat_log_relations_missing_refs():
    """Declarative case; the framework owns invocation."""
