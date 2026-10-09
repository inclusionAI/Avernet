from __future__ import annotations

from datetime import datetime, timezone

from agentclaw.community.adapters.http.bbs.author_enricher import (
    enrich_bot_display_name,
    enrich_bot_display_names,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.forum.models import (
    AUTHOR_TYPE_BOT,
    AUTHOR_TYPE_HUMAN,
    ForumPostRecord,
    ForumTopicRecord,
)

_NOW = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)


class _State:
    def __init__(self, injector):
        self.injector = injector


class _App:
    def __init__(self, injector):
        self.state = _State(injector)


class _Request:
    def __init__(self, injector):
        self.app = _App(injector)


class _Injector:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, key):
        return self._mapping.get(key)


class _BotService:
    def __init__(self, rows):
        self._rows = rows
        self.calls: list[str] = []

    def get_bot_by_id(self, bot_id):
        self.calls.append(bot_id)
        return self._rows.get(bot_id)


def _topic(author_type, author_id, *, display_name=None, avatar_url=None):
    return ForumTopicRecord(
        topic_id=f"topic_{author_id}",
        author_type=author_type,
        author_id=author_id,
        title="t",
        body="b",
        status="OPEN",
        created_at=_NOW,
        updated_at=_NOW,
        display_name=display_name,
        avatar_url=avatar_url,
    )


def _post(author_type, author_id, *, display_name=None, avatar_url=None):
    return ForumPostRecord(
        post_id=f"post_{author_id}",
        topic_id="topic_1",
        author_type=author_type,
        author_id=author_id,
        body="r",
        created_at=_NOW,
        updated_at=_NOW,
        display_name=display_name,
        avatar_url=avatar_url,
    )


def test_bot_display_name_enriched_from_bot_service_when_null():
    bot_service = _BotService({"bot-a": {"bot_name": "Bot Alpha"}})
    injector = _Injector({BotServiceProtocol: bot_service})
    out = enrich_bot_display_names([_topic(AUTHOR_TYPE_BOT, "bot-a")], request=_Request(injector))
    assert out[0].display_name == "Bot Alpha"
    assert bot_service.calls == ["bot-a"]


def test_bot_display_name_enriched_on_post_record():
    bot_service = _BotService({"bot-x": {"bot_name": "Bot X"}})
    injector = _Injector({BotServiceProtocol: bot_service})
    out = enrich_bot_display_names([_post(AUTHOR_TYPE_BOT, "bot-x")], request=_Request(injector))
    assert out[0].display_name == "Bot X"


def test_enrich_bot_display_name_single_record():
    bot_service = _BotService({"bot-a": {"bot_name": "Bot Alpha"}})
    injector = _Injector({BotServiceProtocol: bot_service})
    out = enrich_bot_display_name(_topic(AUTHOR_TYPE_BOT, "bot-a"), request=_Request(injector))
    assert out.display_name == "Bot Alpha"


def test_bot_avatar_url_is_never_filled():
    # bots carry no avatar on this surface — only display_name is enriched
    bot_service = _BotService({"bot-a": {"bot_name": "Bot Alpha"}})
    injector = _Injector({BotServiceProtocol: bot_service})
    out = enrich_bot_display_names([_topic(AUTHOR_TYPE_BOT, "bot-a")], request=_Request(injector))
    assert out[0].avatar_url is None


def test_bot_with_stored_snapshot_keeps_it():
    # a write-side snapshot already present wins — not overwritten by lookup
    bot_service = _BotService({"bot-a": {"bot_name": "Bot Alpha"}})
    injector = _Injector({BotServiceProtocol: bot_service})
    record = _topic(AUTHOR_TYPE_BOT, "bot-a", display_name="Snapshot")
    out = enrich_bot_display_names([record], request=_Request(injector))
    assert out[0].display_name == "Snapshot"
    assert bot_service.calls == []  # no lookup performed


def test_human_record_is_never_enriched():
    bot_service = _BotService({"149844": {"bot_name": "should not match"}})
    injector = _Injector({BotServiceProtocol: bot_service})
    out = enrich_bot_display_names([_topic(AUTHOR_TYPE_HUMAN, "149844")], request=_Request(injector))
    assert out[0].display_name is None
    assert bot_service.calls == []


def test_no_injector_is_noop():
    records = [_topic(AUTHOR_TYPE_BOT, "bot-a")]
    out = enrich_bot_display_names(records, request=_Request(injector=None))
    assert out is not records  # new list built
    assert out[0].display_name is None


def test_unbound_bot_service_is_noop():
    injector = _Injector({})  # no BotService bound
    out = enrich_bot_display_names([_topic(AUTHOR_TYPE_BOT, "bot-a")], request=_Request(injector))
    assert out[0].display_name is None


def test_lookup_miss_keeps_null():
    bot_service = _BotService({})  # get_bot_by_id returns None
    injector = _Injector({BotServiceProtocol: bot_service})
    out = enrich_bot_display_names([_topic(AUTHOR_TYPE_BOT, "ghost")], request=_Request(injector))
    assert out[0].display_name is None


def test_bot_service_exception_is_swallowed():
    class _Boom:
        def get_bot_by_id(self, bot_id):
            raise RuntimeError("boom")

    injector = _Injector({BotServiceProtocol: _Boom()})
    out = enrich_bot_display_names([_topic(AUTHOR_TYPE_BOT, "bot-a")], request=_Request(injector))
    assert out[0].display_name is None


def test_batches_duplicate_bot_authors():
    bot_service = _BotService({"bot-a": {"bot_name": "Bot A"}, "bot-b": {"bot_name": None}})
    injector = _Injector({BotServiceProtocol: bot_service})
    out = enrich_bot_display_names(
        [_topic(AUTHOR_TYPE_BOT, "bot-a"), _topic(AUTHOR_TYPE_BOT, "bot-a"), _topic(AUTHOR_TYPE_BOT, "bot-b")],
        request=_Request(injector),
    )
    assert sorted(bot_service.calls) == ["bot-a", "bot-b"]  # dedup
    assert out[0].display_name == "Bot A"
    assert out[1].display_name == "Bot A"
    assert out[2].display_name is None  # bot-b had no name → null


def test_empty_input_returns_empty_list():
    assert enrich_bot_display_names([], request=_Request(injector=None)) == []
