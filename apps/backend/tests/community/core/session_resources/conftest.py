"""Independent-connection database fixture for withdrawal recovery tests."""

import pytest
from agentclaw.community.core.session_resources.repository.models import (
    SessionResourceModel,
)
from agentclaw.community.core.session_resources.withdrawal_models import (
    ResourceWithdrawalModel,
)
from agentclaw.community.core.repository.implementations.platform.session_resource import (
    SessionResourceRepository,
)
from agentclaw.community.core.repository.implementations.platform.resource_withdrawal import (
    ResourceWithdrawalRepository,
)
from agentclaw.community.plugins.community.database import CommunityDatabase


@pytest.fixture
def store(tmp_path):
    db = CommunityDatabase(
        f"sqlite:///{tmp_path / 'withdrawal.db'}", create_schema=False
    )
    SessionResourceModel.__table__.create(db._engine)
    ResourceWithdrawalModel.__table__.create(db._engine)
    yield db, SessionResourceRepository(db), ResourceWithdrawalRepository(db)
    db._engine.dispose()
