from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.system_config import (
    SystemConfigRecord,
    SystemConfigRepository,
)
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestSystemConfigSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: SystemConfigRepository = (
            get_container().repository.system_config_repository()
        )
        conf_key = _generate_uuid()

        config_id = repo.insert_config(
            conf_key=conf_key,
            conf_value="v",
            env=TEST_ENV,
            name="Sqlite Config",
            description="desc",
            creator="u",
            modifier="u",
        )
        assert config_id > 0

        record = repo.get_by_id(config_id)
        assert isinstance(record, SystemConfigRecord)
        assert record.conf_key == conf_key
        assert record.conf_value == "v"
        assert record.env == TEST_ENV
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.system_config_repository()
        assert repo.get_by_id(99999999) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.system_config_repository()
        conf_key = _generate_uuid()

        config_id = repo.insert_config(
            conf_key=conf_key,
            conf_value=None,
            env=TEST_ENV,
            name="Null Config",
            description=None,
            creator="u",
            modifier="u",
        )
        record = repo.get_by_id(config_id)
        assert record is not None
        assert record.conf_value is None
        assert record.description is None

    def test_deep_update_config(self):
        repo = get_container().repository.system_config_repository()
        conf_key = _generate_uuid()

        config_id = repo.insert_config(
            conf_key=conf_key,
            conf_value="original",
            env=TEST_ENV,
            name="Before",
            description="desc",
            creator="orig",
            modifier="orig",
        )
        repo.update_config(
            config_id=config_id,
            conf_value="updated",
            name="After",
            description="updated desc",
            modifier="updater",
        )
        record = repo.get_by_id(config_id)
        assert record is not None
        assert record.conf_value == "updated"
        assert record.name == "After"
        assert record.creator == "orig"

    def test_insert_config_and_get_by_id(self):
        repo: SystemConfigRepository = (
            get_container().repository.system_config_repository()
        )
        conf_key = f"equiv_key_{_generate_uuid()[:12]}"

        config_id = repo.insert_config(
            conf_key=conf_key,
            conf_value="test",
            env=TEST_ENV,
            name="Test Config",
            description="desc",
            creator="u",
            modifier="u",
        )
        assert config_id > 0

        record = repo.get_by_id(config_id)
        assert isinstance(record, SystemConfigRecord)
        assert record.id == config_id
        assert record.conf_key == conf_key
        assert record.conf_value == "test"
        assert record.env == TEST_ENV
        assert record.name == "Test Config"
        assert record.description == "desc"
        assert record.creator == "u"
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_env_and_key(self):
        repo = get_container().repository.system_config_repository()
        conf_key = f"equiv_key_{_generate_uuid()[:12]}"

        repo.insert_config(
            conf_key=conf_key,
            conf_value="test",
            env=TEST_ENV,
            name="By env+key test",
            description="desc",
            creator="u",
            modifier="u",
        )

        record = repo.get_by_env_and_key(TEST_ENV, conf_key)
        assert record is not None
        assert record.conf_key == conf_key

    def test_get_by_env_and_key_nonexistent(self):
        repo = get_container().repository.system_config_repository()
        assert (
            repo.get_by_env_and_key(TEST_ENV, f"nonexistent_{_generate_uuid()}") is None
        )

    def test_update_config(self):
        repo = get_container().repository.system_config_repository()
        conf_key = f"equiv_key_{_generate_uuid()[:12]}"

        config_id = repo.insert_config(
            conf_key=conf_key,
            conf_value="original",
            env=TEST_ENV,
            name="Original Name",
            description="desc",
            creator="u",
            modifier="u",
        )
        repo.update_config(
            config_id=config_id,
            conf_value="updated",
            name="Updated Name",
            description="updated desc",
            modifier="admin",
        )

        record = repo.get_by_id(config_id)
        assert record is not None
        assert record.conf_value == "updated"
        assert record.name == "Updated Name"
        assert record.description == "updated desc"

    def test_delete_config(self):
        repo = get_container().repository.system_config_repository()
        conf_key = f"equiv_key_{_generate_uuid()[:12]}"

        config_id = repo.insert_config(
            conf_key=conf_key,
            conf_value="test",
            env=TEST_ENV,
            name="To delete",
            description="desc",
            creator="u",
            modifier="u",
        )
        count = repo.delete_config(config_id=config_id)
        assert count == 1

        result = repo.get_by_id(config_id)
        assert result is None

    def test_list_configs(self):
        repo = get_container().repository.system_config_repository()
        conf_key = f"equiv_key_{_generate_uuid()[:12]}"

        repo.insert_config(
            conf_key=conf_key,
            conf_value="test",
            env=TEST_ENV,
            name="List test config",
            description="desc",
            creator="u",
            modifier="u",
        )

        total, records = repo.list_configs(env=TEST_ENV, page=1, page_size=10)
        assert total >= 1
        keys = {r.conf_key for r in records}
        assert conf_key in keys
