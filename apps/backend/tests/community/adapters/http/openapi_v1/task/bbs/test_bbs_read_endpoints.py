from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.task.bbs.router import (
    get_topic,
    list_posts,
    list_topics,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.core.task.forum.models import (
    ForumPostPage,
    ForumPostRecord,
    ForumTopicPage,
    ForumTopicRecord,
    ForumTopicAnalysisRecord,
    ForumTopicViewerRecord,
    ForumPollSelection,
)


def _request(path: str) -> Request:
    request = Request(scope={"type": "http", "method": "GET", "path": path})
    request.state.trace_id = "trace-read"
    return request


def _topic(body: str = "description") -> ForumTopicRecord:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    return ForumTopicRecord(
        topic_id="topic_1",
        author_type="HUMAN",
        author_id="user-a",
        title="Topic title",
        body=body,
        status="OPEN",
        analysis=ForumTopicAnalysisRecord(),
        created_at=now,
        updated_at=now,
    )


def _post() -> ForumPostRecord:
    now = datetime(2026, 9, 20, 8, 1, tzinfo=timezone.utc)
    return ForumPostRecord(
        post_id="post_1",
        topic_id="topic_1",
        author_type="BOT",
        author_id="bot-a",
        body="Reply body",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_list_topics_returns_preview_without_full_body():
    topic = replace(_topic("x" * 501), is_hot=True)

    class Service:
        def list_topics(self, **kwargs):
            assert kwargs == {
                "keyword": "query",
                "status": "open",
                "topic_type": None,
                "author_id": None,
                "page": 2,
                "page_size": 5,
            }
            return ForumTopicPage(total=6, items=(topic,))

    payload = await list_topics(
        request=_request("/openapi/v1/collaboration/tasks/bbs/topics"),
        page_params=PageParams(page=2, page_size=5),
        keyword="query",
        status="open",
        topic_type=None,
        author_id=None,
        service=Service(),
    )

    assert payload.data is not None
    assert payload.data.total == 6
    assert len(payload.data.items[0].body_preview) == 500
    assert payload.data.items[0].body_truncated is True
    assert payload.data.items[0].topic_type == "DISCUSSION"
    assert payload.data.items[0].is_hot is True
    assert "body" not in payload.data.items[0].model_dump()


@pytest.mark.asyncio
async def test_get_topic_returns_full_body_viewer_state_without_posts():
    viewer = ForumTopicViewerRecord(
        has_voted=True,
        vote_post_id="post_vote",
        poll_selection=ForumPollSelection(option_indices=(0,)),
    )

    class Service:
        def get_topic(self, **kwargs):
            assert kwargs == {
                "topic_id": "topic_1",
                "viewer_author_type": "HUMAN",
                "viewer_author_id": "user-a",
            }
            return replace(_topic("full description"), viewer=viewer)

    payload = await get_topic(
        topic_id="topic_1",
        request=_request("/openapi/v1/collaboration/tasks/bbs/topics/topic_1"),
        viewer_author_type="HUMAN",
        viewer_author_id="user-a",
        service=Service(),
    )

    assert payload.data is not None
    assert payload.data.body == "full description"
    assert payload.data.viewer is not None
    assert payload.data.viewer.has_voted is True
    assert payload.data.viewer.vote_post_id == "post_vote"
    assert payload.data.viewer.poll_selection is not None
    assert payload.data.viewer.poll_selection.option_indices == [0]
    assert "posts" not in payload.data.model_dump()


@pytest.mark.asyncio
async def test_list_posts_returns_first_level_page_with_child_group():
    child = replace(
        _post(),
        post_id="post_2",
        body="Child reply",
        reply_to_post_id="post_1",
        reply_to_floor_no=1,
    )
    parent = replace(_post(), floor_no=3, helpful=True, children=(child,))

    class Service:
        def list_posts(self, **kwargs):
            assert kwargs == {"topic_id": "topic_1", "page": 1, "page_size": 20}
            return ForumPostPage(total=1, items=(parent,))

    payload = await list_posts(
        topic_id="topic_1",
        request=_request("/openapi/v1/collaboration/tasks/bbs/topics/topic_1/posts"),
        page_params=PageParams(),
        service=Service(),
    )

    assert payload.data is not None
    assert payload.data.total == 1
    item = payload.data.items[0]
    assert item.model_dump()["post_id"] == "post_1"
    assert item.floor_no == 3
    assert item.helpful is True
    assert item.children[0].post_id == "post_2"
    assert item.children[0].reply_to_post_id == "post_1"
    assert item.children[0].reply_to_floor_no == 1
    assert item.children[0].helpful is False
    assert item.children[0].children == []


@pytest.mark.asyncio
async def test_list_topics_surfaces_stored_author_display():
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    topic = ForumTopicRecord(
        topic_id="topic_1",
        author_type="HUMAN",
        author_id="149844",
        title="Topic title",
        body="description",
        status="OPEN",
        analysis=ForumTopicAnalysisRecord(),
        created_at=now,
        updated_at=now,
        display_name="国科",
        avatar_url="https://av/nk",
    )

    class Service:
        def list_topics(self, **kwargs):
            return ForumTopicPage(total=1, items=(topic,))

    payload = await list_topics(
        request=_request("/openapi/v1/collaboration/tasks/bbs/topics"),
        page_params=PageParams(page=1, page_size=10),
        keyword=None,
        status=None,
        topic_type=None,
        author_id=None,
        service=Service(),
    )

    assert payload.data is not None and payload.data.items[0].display_name == "国科"
    assert payload.data.items[0].avatar_url == "https://av/nk"
