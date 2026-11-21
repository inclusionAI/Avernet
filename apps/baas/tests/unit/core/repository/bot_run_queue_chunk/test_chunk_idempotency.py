"""Real SQLite persistence and independent-session contention regressions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    CheckConstraint,
    Integer,
    MetaData,
    create_engine,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from secbaas.community.core.repository.bot_run_queue_chunk import (
    OrmBotRunQueueChunkRepository,
)
from secbaas.community.core.repository.bot_run_queue_chunk._orm_model import (
    BotRunQueueChunkModel,
)


@pytest.fixture
def storage(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chunks.db'}", connect_args={"timeout": 10}
    )
    table = BotRunQueueChunkModel.__table__.to_metadata(MetaData())
    table.c.id.type = Integer()
    table.append_constraint(CheckConstraint("seq > 0", name="positive_seq"))
    table.create(engine)
    factory = sessionmaker(engine)

    @contextmanager
    def orm_session():
        with factory.begin() as session:
            yield session

    db = SimpleNamespace(orm_session=orm_session)
    yield OrmBotRunQueueChunkRepository(db), engine, table
    engine.dispose()


def snapshot(engine, table):
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(select(table).order_by(table.c.id)).mappings()
        ]


def test_duplicate_is_noop_preserving_entire_first_row(storage):
    repo, engine, table = storage
    repo.insert_chunk(
        run_id="replay",
        seq=1,
        chunk_type="delta",
        content="original",
        metadata='{"a":1}',
    )
    before = snapshot(engine, table)
    repo.insert_chunk(
        run_id="replay",
        seq=1,
        chunk_type="delta",
        content="original",
        metadata='{"a":1}',
    )
    repo.insert_chunk(
        run_id="replay",
        seq=1,
        chunk_type="final",
        content="replacement",
        metadata='{"a":2}',
    )
    assert snapshot(engine, table) == before
    repo.insert_chunk(run_id="replay", seq=2, chunk_type="final", content="done")
    repo.insert_chunk(run_id="other", seq=1, chunk_type="delta", content="independent")
    assert repo.get_max_seq("replay") == 2
    assert repo.get_max_seq("missing") is None
    assert [c.seq for c in repo.get_chunks_after("replay", 0, limit=1)] == [1]
    assert [c.seq for c in repo.get_chunks_after("replay", 1)] == [2]
    repo.delete_chunks_by_run("replay")
    assert repo.get_chunks_after("replay", 0) == []
    assert len(repo.get_chunks_after("other", 0)) == 1


def test_concurrent_independent_sessions_keep_one_complete_winner(storage):
    repo, engine, table = storage
    barrier = Barrier(8)

    def write(i):
        barrier.wait(timeout=10)
        repo.insert_chunk(
            run_id="contended",
            seq=1,
            chunk_type="delta",
            content=f"payload-{i}",
            metadata=f"meta-{i}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))
    rows = snapshot(engine, table)
    assert len(rows) == 1
    winner = int(rows[0]["content"].split("-")[1])
    assert rows[0]["metadata"] == f"meta-{winner}"
    repo.insert_chunk(run_id="contended", seq=2, chunk_type="final", content="done")
    assert repo.get_max_seq("contended") == 2


@pytest.mark.parametrize(
    "field,value", [("chunk_type", None), ("seq", -1), ("content", "taken")]
)
@pytest.mark.parametrize("existing", [False, True])
def test_other_real_constraints_propagate(storage, field, value, existing):
    repo, engine, table = storage
    if field == "content":
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX unique_content ON baas_bot_run_queue_chunk(content)"
            )
    repo.insert_chunk(run_id="other", seq=1, chunk_type="delta", content="taken")
    if existing:
        repo.insert_chunk(run_id="target", seq=1, chunk_type="delta", content="first")
    kwargs = dict(run_id="target", seq=1, chunk_type="delta", content="new")
    kwargs[field] = value
    before = snapshot(engine, table)
    if existing and field == "content":
        # Native target-conflict no-op can precede another unique check.
        repo.insert_chunk(**kwargs)
    else:
        with pytest.raises(IntegrityError):
            repo.insert_chunk(**kwargs)
    assert snapshot(engine, table) == before
