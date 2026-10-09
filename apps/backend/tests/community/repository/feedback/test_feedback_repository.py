from __future__ import annotations

from agentclaw.community.core.feedback.models import FeedbackPage, FeedbackCreateResult
from agentclaw.community.core.repository.implementations.feedback.feedback_repository import (
    FeedbackRepository,
)


def _result(repo, *, reporter_id="149844", module="bbs", content="hello"):
    return repo.create_feedback(
        reporter_id=reporter_id, module=module, content=content
    )


def test_create_feedback_persists_a_row_with_an_id(db):
    repo = FeedbackRepository(db)
    result = repo.create_feedback(
        reporter_id="  149844  ", module="bbs", content="some note"
    )

    assert isinstance(result, FeedbackCreateResult)
    assert result.created is True
    rec = result.feedback
    assert rec.id > 0
    assert rec.reporter_id == "  149844  "
    assert rec.module == "bbs"
    assert rec.content == "some note"
    assert rec.created_at is not None
    assert rec.created_at == rec.updated_at


def test_create_feedback_distinct_rows_get_distinct_ids(db):
    repo = FeedbackRepository(db)

    first = repo.create_feedback(reporter_id="u1", module="bbs", content="a")
    second = repo.create_feedback(reporter_id="u2", module="task", content="b")

    assert first.feedback.id != second.feedback.id


def test_list_feedback_returns_newest_first(db):
    repo = FeedbackRepository(db)
    oldest = repo.create_feedback(reporter_id="u1", module="bbs", content="old")
    newest = repo.create_feedback(reporter_id="u1", module="bbs", content="new")

    page = repo.list_feedback(module=None, reporter_id=None, offset=0, limit=10)

    assert isinstance(page, FeedbackPage)
    assert page.total == 2
    assert [item.content for item in page.items] == ["new", "old"]
    assert page.items[0].id == newest.feedback.id
    assert page.items[1].id == oldest.feedback.id


def test_list_feedback_filters_by_module(db):
    repo = FeedbackRepository(db)
    repo.create_feedback(reporter_id="u1", module="bbs", content="c1")
    repo.create_feedback(reporter_id="u1", module="task", content="c2")
    repo.create_feedback(reporter_id="u1", module="bbs", content="c3")

    page = repo.list_feedback(module="bbs", reporter_id=None, offset=0, limit=10)

    assert page.total == 2
    assert {item.module for item in page.items} == {"bbs"}


def test_list_feedback_filters_by_reporter(db):
    repo = FeedbackRepository(db)
    repo.create_feedback(reporter_id="u1", module="bbs", content="a")
    repo.create_feedback(reporter_id="u2", module="bbs", content="b")

    page = repo.list_feedback(module=None, reporter_id="u2", offset=0, limit=10)

    assert page.total == 1
    assert page.items[0].reporter_id == "u2"


def test_list_feedback_combines_filters(db):
    repo = FeedbackRepository(db)
    repo.create_feedback(reporter_id="u1", module="bbs", content="a")
    repo.create_feedback(reporter_id="u1", module="task", content="b")
    repo.create_feedback(reporter_id="u2", module="bbs", content="c")

    page = repo.list_feedback(module="bbs", reporter_id="u2", offset=0, limit=10)

    assert page.total == 1
    assert page.items[0].content == "c"


def test_list_feedback_paginates(db):
    repo = FeedbackRepository(db)
    for n in range(5):
        repo.create_feedback(reporter_id="u1", module="bbs", content=str(n))

    first = repo.list_feedback(module=None, reporter_id=None, offset=0, limit=2)
    second = repo.list_feedback(module=None, reporter_id=None, offset=2, limit=2)

    assert first.total == 5
    assert len(first.items) == 2
    assert len(second.items) == 2
    # newest first: the second page follows the first by id
    assert first.items[0].id > first.items[1].id
    assert first.items[-1].id > second.items[0].id


def test_list_feedback_empty_when_no_matches(db):
    repo = FeedbackRepository(db)

    page = repo.list_feedback(module="bbs", reporter_id="nobody", offset=0, limit=10)

    assert page.total == 0
    assert page.items == ()
