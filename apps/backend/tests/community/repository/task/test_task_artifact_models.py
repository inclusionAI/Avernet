"""``task_artifact`` ORM/Record:SQLite 建表、四分支 roundtrip、空值容错口(spec §6)。"""
from __future__ import annotations

from datetime import datetime

import pytest

from agentclaw.community.core.task.repository.models import TaskArtifactModel
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_context.task_artifact import (
    CodeContent,
    FileContent,
    StorageType,
    TextContent,
    UrlLinkContent,
    artifact_content_hash,
    content_to_json,
)

_NOW = datetime(2026, 10, 20, 12, 0, 0)


def _row(
    *,
    artifact_id: str = "task-art-0123456789abcdef",
    artifact_name=None,
    task_id: str = "t-1",
    node_id: str = "n-1",
    attempt=0,
    scope_session_id="sess-1",
    kind: str = "text",
    storage: str = "inline",
    content=None,
    content_hash=None,
    creator="bot-1",
    modifier="bot-1",
) -> TaskArtifactModel:
    return TaskArtifactModel(
        artifact_id=artifact_id,
        artifact_name=artifact_name,
        task_id=task_id,
        node_id=node_id,
        attempt=attempt,
        scope_session_id=scope_session_id,
        kind=kind,
        storage=storage,
        content=content if content is not None else content_to_json(TextContent(text="done")),
        content_hash=content_hash,
        creator=creator,
        modifier=modifier,
        gmt_create=_NOW,
        gmt_modify=_NOW,
    )


def test_table_created_in_sqlite(engine):
    from sqlalchemy import inspect

    names = inspect(engine).get_table_names()
    assert "task_artifact" in names


def test_insert_and_to_record_four_branches(db):
    text = TextContent(text="分析完成")
    file_ws = FileContent(
        storage_type=StorageType.BOT_WORKSPACE,
        storage_id="reports/report.md",
        file_type="text/markdown",
        file_name="report.md",
        file_id=None,
    )
    file_sf = FileContent(
        storage_type=StorageType.SESSION_FILE,
        storage_id="sess-xxx",
        file_type="application/pdf",
        file_name="report.pdf",
        file_id="f-987",
    )
    url_link = UrlLinkContent(url="https://example.com/doc", type="dima需求")
    code = CodeContent(
        code_base_name="avernet",
        code_base_address="https://antcode.example.com/avernet",
        pr_link="https://antcode.example.com/pr/1",
        pr_desc="",
    )
    contents = [text, file_ws, file_sf, url_link, code]
    rows = [
        _row(
            artifact_id=f"task-art-{i:016x}",
            content=content_to_json(content),
            content_hash=artifact_content_hash(content),
            kind=kind,
            storage=storage,
            task_id="t-1",
            node_id="n-1",
        )
        for i, (content, kind, storage) in enumerate(
            [
                (text, "text", "inline"),
                (file_ws, "file", "bot_workspace"),
                (file_sf, "file", "session_file"),
                (url_link, "url_link", "inline"),
                (code, "code", "inline"),
            ]
        )
    ]
    with db.orm_session() as session:
        session.add_all(rows)
    records = []
    with db.orm_session() as session:
        stored = (
            session.query(TaskArtifactModel)
            .filter(TaskArtifactModel.task_id == "t-1")
            .order_by(TaskArtifactModel.artifact_id)
            .all()
        )
        records = [row.to_record() for row in stored]
    assert len(records) == 5
    for record, expected in zip(records, contents, strict=True):
        assert isinstance(record, TaskArtifactRecord)
        assert record.content == expected
        assert record.kind in {"text", "file", "url_link", "code"}
        assert record.task_id == "t-1"
        assert record.node_id == "n-1"
        assert record.creator == "bot-1"
        assert record.modifier == "bot-1"
        assert record.gmt_create == _NOW
        # created_at(ms)由 gmt_create 折算
        assert record.created_at == int(_NOW.timestamp() * 1000)


def test_nullable_columns_tolerate_missing_values(db):
    """node_id/attempt/content_hash/creator/modifier 列可空——空值容错口(人工修数/回放残留)。"""

    row = _row(
        node_id=None,
        attempt=None,
        content_hash=None,
        creator=None,
        modifier=None,
        scope_session_id=None,
        artifact_name=None,
    )
    with db.orm_session() as session:
        session.add(row)
        target_id = row.artifact_id
    with db.orm_session() as session:
        record = session.query(TaskArtifactModel).filter_by(artifact_id=target_id).one().to_record()
    assert record.node_id is None
    assert record.attempt is None
    assert record.content_hash is None
    assert record.creator is None
    assert record.modifier is None
    assert record.scope_session_id is None
    assert record.artifact_name is None
    assert record.content == TextContent(text="done")


def test_required_column_rejected_by_sqlite(db):
    """task_id/content/kind/artifact_id 列级 NOT NULL:缺 kind 直接约束失败。"""

    from sqlalchemy import exc

    with pytest.raises(exc.IntegrityError):
        with db.orm_session() as session:
            session.add(
                TaskArtifactModel(
                    artifact_id="task-art-eeeeeeeeeeeeeeee",
                    task_id="t-2",
                    storage="inline",
                    content='{"kind":"text","text":"x"}',
                    gmt_create=_NOW,
                    gmt_modify=_NOW,
                )
            )


def test_content_parsed_to_domain_object_in_record(db):
    row = _row(
        kind="url_link",
        storage="inline",
        content=content_to_json(UrlLinkContent(url="https://example.com/doc", type="语雀文档")),
        content_hash=artifact_content_hash(
            UrlLinkContent(url="https://example.com/doc", type="语雀文档")
        ),
    )
    with db.orm_session() as session:
        session.add(row)
        target_id = row.artifact_id
    with db.orm_session() as session:
        record = session.query(TaskArtifactModel).filter_by(artifact_id=target_id).one().to_record()
    assert record.content == UrlLinkContent(url="https://example.com/doc", type="语雀文档")
    assert record.content_hash == artifact_content_hash(record.content)