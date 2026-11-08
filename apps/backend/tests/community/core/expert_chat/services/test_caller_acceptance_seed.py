"""Caller acceptance publications must address their real persisted source Bot."""

import json

import httpx
import pytest
from sqlalchemy import create_engine, select, text

from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from tests.community.acceptance.expert_chat.test_caller_connection_api import (
    _seed_service_bot,
    _seed_successful_publish,
)


@pytest.mark.parametrize("other_bots", [1, 3])
def test_acceptance_publication_uses_its_own_bot_primary_key(other_bots):
    engine = create_engine("sqlite://")
    BotModel.__table__.create(engine)
    BotPublishModel.__table__.create(engine)

    def execute_sql(request):
        assert request.url.path == "/local/sql/execute"
        results = []
        with engine.begin() as connection:
            for statement in json.loads(request.content)["statements"]:
                result = connection.execute(text(statement["sql"]), statement["params"])
                row_id = result.scalar_one() if result.returns_rows else result.lastrowid
                results.append({"lastrowid": row_id})
        return httpx.Response(200, json={"results": results})

    try:
        with httpx.Client(
            base_url="http://backend.test", transport=httpx.MockTransport(execute_sql),
        ) as client:
            for index in range(other_bots):
                _seed_service_bot(
                    client, owner_id="other",
                    bot_id="target" if index == 0 else f"unrelated-{index}", bot_name="Other",
                )
            _seed_service_bot(client, owner_id="owner", bot_id="target", bot_name="Target")
            publication_id = _seed_successful_publish(client, bot_id="target", owner_id="owner")

        with engine.connect() as connection:
            target_pk = connection.execute(
                select(BotModel.id).where(BotModel.bot_id == "target", BotModel.owner_id == "owner"),
            ).scalar_one()
            publication = connection.execute(
                select(BotPublishModel.source_bot_pk, BotPublishModel.status).where(
                    BotPublishModel.id == publication_id,
                ),
            ).one()
        assert target_pk != 1
        assert publication.source_bot_pk == target_pk
        assert publication.status == "success"
    finally:
        engine.dispose()
