"""Pool confirmation retains explicit isolation and fresh-session admission."""

from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.repository.implementations.skills_pool.layout import SkillsPoolLayoutRepository
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


@pytest.mark.parametrize("preconnected, actual_isolation", [
    (False, "READ COMMITTED"), (True, "READ COMMITTED"), (False, "AUTOCOMMIT"),
])
def test_pool_confirmation_selects_mysql_isolation_before_any_read(preconnected, actual_isolation):
    # SQLAlchemy/DB driver is the external boundary. The repository and its
    # transaction guard remain real, including the fail-closed checks.
    session = MagicMock()
    session.in_transaction.return_value = preconnected
    session.get_bind.return_value.dialect.name = "mysql"
    connection = session.connection.return_value
    connection.get_isolation_level.return_value = actual_isolation
    session.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = None
    database = MagicMock()
    database.orm_session.return_value = nullcontext(session)
    database.transactional_orm_session.side_effect = AssertionError("Guard owns the transaction setup")
    repository = SkillsPoolLayoutRepository(database)
    kwargs = dict(
        scope=BotSkillLayoutScope(env="pre", entity_id="entity", bot_id="bot"),
        layout_contract_version="skills-pool-p3-v1", binding_id=17, startup_identity="startup-1",
    )

    if preconnected:
        with pytest.raises(RuntimeError, match="fresh ORM Session"):
            repository.confirm_pool_initializing(**kwargs)
        session.connection.assert_not_called()
        session.query.assert_not_called()
    elif actual_isolation == "AUTOCOMMIT":
        with pytest.raises(RuntimeError, match="transaction isolation mismatch"):
            repository.confirm_pool_initializing(**kwargs)
        session.rollback.assert_called_once()
        session.query.assert_not_called()
    else:
        assert repository.confirm_pool_initializing(**kwargs) is False
        session.query.assert_called_once()
    if not preconnected:
        session.connection.assert_called_once_with(
            execution_options={"isolation_level": "READ COMMITTED"}
        )
    connection.exec_driver_sql.assert_not_called()
