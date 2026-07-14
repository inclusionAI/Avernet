from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.community.bootstrap import get_container
from secbaas.community.core.repository.ac_bot_publish import (
    AcBotPublishRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestAcBotPublishSqliteOrmEquivalence:
    def test_negative_get_binding_id_nonexistent_bot(self):
        repo: AcBotPublishRepository = (
            get_container().repository.ac_bot_publish_repository()
        )
        result = repo.get_binding_id(
            source_bot_id=f"nonexistent_bot_{_generate_uuid()}"
        )
        assert result is None

    def test_negative_get_binding_id_nonexistent_status(self):
        repo = get_container().repository.ac_bot_publish_repository()
        result = repo.get_binding_id(
            source_bot_id=f"nonexistent_bot_{_generate_uuid()}",
            status="nonexistent_status",
        )
        assert result is None

    def test_negative_get_binding_id_with_owner_id(self):
        repo = get_container().repository.ac_bot_publish_repository()
        result = repo.get_binding_id(
            source_bot_id=f"nonexistent_bot_{_generate_uuid()}",
            status="success",
            owner_id=_generate_uuid(),
        )
        assert result is None
