from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.ac_bot import AcBotRepository
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestAcBotSqliteOrmEquivalence:
    def test_record_all_fields_populated_on_match(self):
        repo: AcBotRepository = get_container().repository.ac_bot_repository()
        record = repo.get_by_entity_id_bot_id_env(
            entity_id="staff_12345",
            bot_id=_generate_uuid(),
            env=TEST_ENV,
        )
        if record is not None:
            expected_fields = {
                "id",
                "bot_id",
                "bot_name",
                "bot_desc",
                "entity_id",
                "entity_type",
                "creator_id",
                "owner_id",
                "engine_types",
                "status",
                "binding_id",
                "gmt_create",
                "gmt_modified",
                "modifier_id",
                "share_policy",
                "is_delete",
                "active_engine",
                "device_id",
                "env",
                "owner_name",
                "public",
                "ext",
                "bot_type",
            }
            record_fields = set(record.__slots__)
            missing = expected_fields - record_fields
            assert not missing, f"AcBotRecord missing fields: {missing}"

    def test_negative_nonexistent_entity(self):
        repo: AcBotRepository = get_container().repository.ac_bot_repository()
        result = repo.get_by_entity_id_bot_id_env(
            entity_id="nonexistent_entity_" + _generate_uuid(),
            bot_id=_generate_uuid(),
            env="__test_nonexistent_env__",
        )
        assert result is None

    def test_negative_exclude_default_raises(self):
        repo = get_container().repository.ac_bot_repository()
        with pytest.raises(ValueError, match="cannot be 'default'"):
            repo.get_by_bot_id_env_exclude_default(bot_id="default", env=TEST_ENV)

    def test_negative_exclude_default_nonexistent(self):
        repo = get_container().repository.ac_bot_repository()
        result = repo.get_by_bot_id_env_exclude_default(
            bot_id="nonexistent_bot_" + _generate_uuid(),
            env="__test_nonexistent_env__",
        )
        assert result is None
