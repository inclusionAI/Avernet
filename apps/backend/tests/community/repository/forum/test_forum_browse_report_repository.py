from __future__ import annotations

from agentclaw.community.core.forum.repository.models import ForumBrowseReportModel
from agentclaw.community.core.repository.implementations.forum.forum_repository import (
    ForumRepository,
)


def _submit_report(
    repo,
    *,
    bot_id="bot-a",
    status="SUCCESS",
    request_id="req-1",
    message=None,
):
    return repo.create_browse_report(
        bot_id=bot_id,
        status=status,
        client_request_id=request_id,
        message=message,
    )


def test_create_browse_report_persists_record(db):
    repo = ForumRepository(db)
    result = _submit_report(repo, message="all good")

    assert result.created is True
    assert result.report.report_id.startswith("br_")
    assert result.report.bot_id == "bot-a"
    assert result.report.status == "SUCCESS"
    assert result.report.message == "all good"

    with db.orm_session() as session:
        rows = session.query(ForumBrowseReportModel).all()
        assert len(rows) == 1
        assert rows[0].client_request_id == "req-1"
        assert rows[0].env is not None
        assert rows[0].avernet_tenant is not None


def test_create_browse_report_is_idempotent_by_client_request_id(db):
    repo = ForumRepository(db)
    first = _submit_report(repo)
    second = _submit_report(
        repo, status="FAILED", message="should not overwrite"
    )

    assert second.created is False
    assert second.report.report_id == first.report.report_id
    assert second.report.status == "SUCCESS"
    assert second.report.message is None

    with db.orm_session() as session:
        assert session.query(ForumBrowseReportModel).count() == 1


def test_create_browse_report_distinguishes_different_request_ids(db):
    repo = ForumRepository(db)
    first = _submit_report(repo)
    second = _submit_report(repo, request_id="req-2")

    assert second.created is True
    assert first.report.report_id != second.report.report_id

    with db.orm_session() as session:
        assert session.query(ForumBrowseReportModel).count() == 2
