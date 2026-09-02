"""Regression tests for transactional Caller identity lock fencing."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel
from agentclaw.community.core.caller_identity.models import (
    BotMcpCallConfigModel,
    McpCallType,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityEngineChangedError,
    CallerIdentityLockMismatchError,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityIrreversibleError,
)
from agentclaw.community.core.repository.implementations.identity.caller_identity import CallerIdentityRepository
from agentclaw.community.plugin_api.models import BotModel


class InMemorySqliteDB:
    """Expose the transaction seam used by Caller identity persistence."""

    def __init__(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def transactional_orm_session(self):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def orm_session(self):
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()


@pytest.fixture
def database() -> InMemorySqliteDB:
    return InMemorySqliteDB()


@pytest.fixture
def repository(database: InMemorySqliteDB) -> CallerIdentityRepository:
    return CallerIdentityRepository(database)


def _add_lock(database: InMemorySqliteDB) -> None:
    with database.transactional_orm_session() as session:
        session.add(
            BotCollabLockModel(
                lock_key="bot:1",
                holder_user_id="other-editor",
                env="dev",
            )
        )


def _add_caller_bot(database: InMemorySqliteDB) -> None:
    with database.transactional_orm_session() as session:
        session.add(
            BotModel(
                id=1,
                bot_id="bot-1",
                entity_id="entity-1",
                entity_type="staff",
                creator_id="owner",
                owner_id="owner",
                active_engine="openclaw",
                call_type=McpCallType.CALLER.value,
                env="dev",
            )
        )
        session.add(
            BotMcpCallConfigModel(
                bot_pk=1,
                server_code="mcp-a",
                engine_type="openclaw",
                call_type=McpCallType.CALLER.value,
                modifier_id="owner",
                env="dev",
            )
        )


def _add_owner_bot(database: InMemorySqliteDB, *, engine: str = "openclaw") -> None:
    with database.transactional_orm_session() as session:
        session.add(
            BotModel(
                id=1,
                bot_id="bot-1",
                entity_id="entity-1",
                entity_type="staff",
                creator_id="owner",
                owner_id="owner",
                active_engine=engine,
                env="dev",
            )
        )


def _add_owner_lock(database: InMemorySqliteDB) -> int:
    with database.transactional_orm_session() as session:
        lock = BotCollabLockModel(
            lock_key="bot:1",
            holder_user_id="owner",
            env="dev",
        )
        session.add(lock)
        session.flush()
        return int(lock.id)


def _replace_call_type(
    repository: CallerIdentityRepository, lock_epoch: int | None
) -> None:
    repository.replace_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        effective_server_codes={"mcp-a"},
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=lock_epoch,
    )


def _compensate_call_type(
    repository: CallerIdentityRepository, lock_epoch: int | None
) -> None:
    repository.compensate_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        previous_explicit_call_type=None,
        modifier_id="owner",
        effective_server_codes={"mcp-a"},
        expected_revision=1,
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=lock_epoch,
    )


def test_replace_rejects_missing_exact_lock(
    repository: CallerIdentityRepository,
) -> None:
    with pytest.raises(CallerIdentityLockMismatchError):
        _replace_call_type(repository, lock_epoch=1)


def test_replace_rejects_lock_created_before_unlocked_write(
    repository: CallerIdentityRepository, database: InMemorySqliteDB
) -> None:
    _add_lock(database)

    with pytest.raises(CallerIdentityLockMismatchError):
        _replace_call_type(repository, lock_epoch=None)


def test_compensation_rejects_missing_exact_lock(
    repository: CallerIdentityRepository,
) -> None:
    with pytest.raises(CallerIdentityLockMismatchError):
        _compensate_call_type(repository, lock_epoch=1)


def test_compensation_rejects_lock_created_before_unlocked_rollback(
    repository: CallerIdentityRepository, database: InMemorySqliteDB
) -> None:
    _add_lock(database)

    with pytest.raises(CallerIdentityLockMismatchError):
        _compensate_call_type(repository, lock_epoch=None)


def test_replace_rejects_caller_bot_transition_back_to_owner(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    _add_caller_bot(database)

    with pytest.raises(CallerIdentityIrreversibleError) as error:
        repository.replace_draft_call_type(
            bot_pk=1,
            engine_type="openclaw",
            server_code="mcp-a",
            call_type=McpCallType.OWNER,
            modifier_id="owner",
            effective_server_codes={"mcp-a"},
            lock_key="bot:1",
            lock_holder_user_id="owner",
            lock_epoch=None,
        )

    assert error.value.detail == "CALLER_TO_OWNER_UNSUPPORTED"
    assert repository.list_draft_call_types(1, "openclaw") == {
        "mcp-a": McpCallType.CALLER
    }


def test_replace_allows_owner_mcp_when_bot_remains_caller(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    _add_caller_bot(database)

    result = repository.replace_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-b",
        call_type=McpCallType.OWNER,
        modifier_id="owner",
        effective_server_codes={"mcp-a", "mcp-b"},
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    assert result.bot_call_type is McpCallType.CALLER
    assert repository.list_draft_call_types(1, "openclaw") == {
        "mcp-a": McpCallType.CALLER
    }


def test_cli_caller_updates_bot_aggregate_and_revision(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """A CLI caller must take the same Bot aggregate path as an MCP caller."""
    with database.transactional_orm_session() as session:
        session.add(
            BotModel(
                id=1,
                bot_id="bot-1",
                entity_id="entity-1",
                entity_type="staff",
                creator_id="owner",
                owner_id="owner",
                active_engine="openclaw",
                call_type=McpCallType.OWNER.value,
                caller_config_revision=0,
                env="dev",
            )
        )

    mutation = repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    assert mutation.revision == 1
    assert mutation.bot_call_type is McpCallType.CALLER
    assert mutation.caller_config_revision == 1
    assert repository.list_draft_cli_call_types(1, "openclaw") == {
        "dataphin": McpCallType.CALLER,
    }
    with database.orm_session() as session:
        persisted_bot = session.query(BotModel).filter(BotModel.id == 1).one()
        assert persisted_bot.call_type == McpCallType.CALLER.value
        assert persisted_bot.caller_config_revision == 1


def test_cli_owner_rejects_removing_the_last_caller_from_bot_aggregate(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """Removing the last CLI caller must preserve the existing Bot downgrade guard."""
    _add_owner_bot(database)
    repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    with pytest.raises(CallerIdentityIrreversibleError):
        repository.replace_draft_cli_call_type(
            bot_pk=1,
            engine_type="openclaw",
            cli_code="dataphin",
            call_type=McpCallType.OWNER,
            modifier_id="owner",
            lock_key="bot:1",
            lock_holder_user_id="owner",
            lock_epoch=None,
        )

    assert repository.list_draft_cli_call_types(1, "openclaw") == {
        "dataphin": McpCallType.CALLER,
    }


def test_mcp_update_keeps_bot_aggregate_caller_when_a_cli_is_caller(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """An MCP owner update cannot downgrade a Bot that still has a CLI caller."""
    _add_owner_bot(database)
    repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    mcp_mutation = repository.replace_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        call_type=McpCallType.OWNER,
        modifier_id="owner",
        effective_server_codes={"mcp-a"},
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    assert mcp_mutation.bot_call_type is McpCallType.CALLER


def test_cli_compensation_deletes_only_its_new_sparse_row(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """A failed AgentPass write restores owner by deleting only the new CLI row."""
    with database.transactional_orm_session() as session:
        session.add(
            BotModel(
                id=1,
                bot_id="bot-1",
                entity_id="entity-1",
                entity_type="staff",
                creator_id="owner",
                owner_id="owner",
                active_engine="openclaw",
                env="dev",
            )
        )

    mutation = repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    assert repository.compensate_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        previous_explicit_call_type=mutation.previous_explicit_call_type,
        modifier_id="owner",
        expected_revision=mutation.revision,
        expected_caller_config_revision=mutation.caller_config_revision,
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    ) is True
    assert repository.list_draft_cli_call_types(1, "openclaw") == {}


def test_cli_compensation_restores_deleted_caller_row_with_new_revision(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """Owner rollback reinstates the exact prior caller override, not MCP state."""
    with database.transactional_orm_session() as session:
        session.add(
            BotModel(
                id=1,
                bot_id="bot-1",
                entity_id="entity-1",
                entity_type="staff",
                creator_id="owner",
                owner_id="owner",
                active_engine="openclaw",
                env="dev",
            )
        )
    repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )
    repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="deepinsight-cli",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )
    owner_mutation = repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.OWNER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    assert repository.compensate_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        previous_explicit_call_type=owner_mutation.previous_explicit_call_type,
        modifier_id="owner",
        expected_revision=owner_mutation.revision,
        expected_caller_config_revision=owner_mutation.caller_config_revision,
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    ) is True
    assert repository.list_draft_cli_call_types(1, "openclaw") == {
        "dataphin": McpCallType.CALLER,
        "deepinsight-cli": McpCallType.CALLER,
    }


def test_cli_compensation_does_not_delete_a_newer_sparse_row(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """CAS failure leaves a later editor's CLI override intact."""
    with database.transactional_orm_session() as session:
        session.add(
            BotModel(
                id=1,
                bot_id="bot-1",
                entity_id="entity-1",
                entity_type="staff",
                creator_id="owner",
                owner_id="owner",
                active_engine="openclaw",
                env="dev",
            )
        )
    repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    assert repository.compensate_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        previous_explicit_call_type=None,
        modifier_id="owner",
        expected_revision=2,
        expected_caller_config_revision=2,
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    ) is False
    assert repository.list_draft_cli_call_types(1, "openclaw") == {
        "dataphin": McpCallType.CALLER
    }


def test_cli_replace_supports_exact_collaboration_lock(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """The DB transaction accepts the current owner's exact lock epoch only."""
    _add_owner_bot(database)
    lock_epoch = _add_owner_lock(database)

    mutation = repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=lock_epoch,
    )

    assert mutation.revision == 1


def test_cli_replace_rejects_missing_or_unlocked_collaboration_lock(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """Both a stale epoch and a newly acquired lock fence the write transaction."""
    _add_owner_bot(database)

    with pytest.raises(CallerIdentityLockMismatchError):
        repository.replace_draft_cli_call_type(
            bot_pk=1,
            engine_type="openclaw",
            cli_code="dataphin",
            call_type=McpCallType.CALLER,
            modifier_id="owner",
            lock_key="bot:1",
            lock_holder_user_id="owner",
            lock_epoch=999,
        )

    _add_lock(database)
    with pytest.raises(CallerIdentityLockMismatchError):
        repository.replace_draft_cli_call_type(
            bot_pk=1,
            engine_type="openclaw",
            cli_code="dataphin",
            call_type=McpCallType.CALLER,
            modifier_id="owner",
            lock_key="bot:1",
            lock_holder_user_id="owner",
            lock_epoch=None,
        )


def test_cli_replace_updates_existing_row_and_rejects_engine_change(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """CLI rows increment independently, and a changed engine makes them inactive."""
    _add_owner_bot(database)
    repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )
    mutation = repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="editor-2",
        lock_key="bot:1",
        lock_holder_user_id="editor-2",
        lock_epoch=None,
    )

    assert mutation.revision == 2
    with pytest.raises(CallerIdentityEngineChangedError):
        repository.replace_draft_cli_call_type(
            bot_pk=1,
            engine_type="claude_code",
            cli_code="dataphin",
            call_type=McpCallType.CALLER,
            modifier_id="owner",
            lock_key="bot:1",
            lock_holder_user_id="owner",
            lock_epoch=None,
        )


def test_cli_compensation_restores_current_row_and_rejects_engine_change(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """CAS rollback can restore a same-row override but never an obsolete engine."""
    _add_owner_bot(database)
    mutation = repository.replace_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    assert repository.compensate_draft_cli_call_type(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        previous_explicit_call_type=McpCallType.CALLER,
        modifier_id="owner",
        expected_revision=mutation.revision,
        expected_caller_config_revision=mutation.caller_config_revision,
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    ) is True
    with pytest.raises(CallerIdentityEngineChangedError):
        repository.compensate_draft_cli_call_type(
            bot_pk=1,
            engine_type="claude_code",
            cli_code="dataphin",
            previous_explicit_call_type=McpCallType.CALLER,
            modifier_id="owner",
            expected_revision=2,
            expected_caller_config_revision=2,
            lock_key="bot:1",
            lock_holder_user_id="owner",
            lock_epoch=None,
        )


def test_mcp_persistence_upserts_a_sparse_caller_row_and_rejects_engine_change(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """Existing MCP paths still protect engine fencing while updating one row."""
    _add_owner_bot(database)
    result = repository.replace_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        call_type=McpCallType.CALLER,
        modifier_id="owner",
        effective_server_codes={"mcp-a"},
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )
    updated = repository.replace_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        call_type=McpCallType.CALLER,
        modifier_id="editor-2",
        effective_server_codes={"mcp-a"},
        lock_key="bot:1",
        lock_holder_user_id="editor-2",
        lock_epoch=None,
    )

    assert result.bot_call_type is McpCallType.CALLER
    assert updated.revision == 2
    with pytest.raises(CallerIdentityEngineChangedError):
        repository.replace_draft_call_type(
            bot_pk=1,
            engine_type="claude_code",
            server_code="mcp-a",
            call_type=McpCallType.CALLER,
            modifier_id="owner",
            effective_server_codes={"mcp-a"},
            lock_key="bot:1",
            lock_holder_user_id="owner",
            lock_epoch=None,
        )


def test_mcp_compensation_restores_prior_caller_row(
    repository: CallerIdentityRepository,
    database: InMemorySqliteDB,
) -> None:
    """MCP rollback remains intact while CLI uses its independent compensation."""
    _add_owner_bot(database)
    with database.transactional_orm_session() as session:
        session.add(
            BotMcpCallConfigModel(
                bot_pk=1,
                server_code="mcp-a",
                engine_type="openclaw",
                call_type=McpCallType.CALLER.value,
                modifier_id="owner",
                env="dev",
            )
        )

    mutation = repository.replace_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        call_type=McpCallType.OWNER,
        modifier_id="owner",
        effective_server_codes={"mcp-a"},
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    compensation = repository.compensate_draft_call_type(
        bot_pk=1,
        engine_type="openclaw",
        server_code="mcp-a",
        previous_explicit_call_type=mutation.previous_explicit_call_type,
        modifier_id="owner",
        effective_server_codes={"mcp-a"},
        expected_revision=mutation.revision,
        lock_key="bot:1",
        lock_holder_user_id="owner",
        lock_epoch=None,
    )

    assert compensation.applied is True
    assert compensation.bot_call_type is McpCallType.CALLER
    assert repository.list_draft_call_types(1, "openclaw") == {
        "mcp-a": McpCallType.CALLER
    }
