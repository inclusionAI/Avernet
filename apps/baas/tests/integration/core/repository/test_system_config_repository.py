from uuid import uuid4

import pytest

from secbaas.community.core.repository.system_config import (
    SystemConfigRecord,
    SystemConfigRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestSystemConfigRepositoryProtocol:
    # ── 1. insert_config + get_by_id ──

    def test_insert_and_get_by_id(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        config_id = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="test_value",
            env=TEST_ENV,
            name="Test Config",
            description="A test config",
            creator="test_user",
            modifier="test_user",
        )
        assert config_id > 0

        record = system_config_repository.get_by_id(config_id)
        assert record is not None
        assert isinstance(record, SystemConfigRecord)
        assert record.id == config_id
        assert record.conf_key == conf_key
        assert record.conf_value == "test_value"
        assert record.env == TEST_ENV
        assert record.name == "Test Config"
        assert record.description == "A test config"
        assert record.creator == "test_user"
        assert record.modifier == "test_user"
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_returns_none_for_missing(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        result = system_config_repository.get_by_id(99999999)
        assert result is None

    def test_insert_config_with_none_value(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        config_id = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value=None,
            env=TEST_ENV,
            name="Null Value Config",
            description=None,
            creator="test_user",
            modifier="test_user",
        )

        record = system_config_repository.get_by_id(config_id)
        assert record is not None
        assert record.conf_value is None
        assert record.description is None

    # ── 2. get_by_env_and_key ──

    def test_get_by_env_and_key(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="env_key_value",
            env=TEST_ENV,
            name="Env Key Config",
            creator="test_user",
            modifier="test_user",
        )

        record = system_config_repository.get_by_env_and_key(TEST_ENV, conf_key)
        assert record is not None
        assert record.conf_key == conf_key
        assert record.env == TEST_ENV
        assert record.conf_value == "env_key_value"

    def test_get_by_env_and_key_returns_none_for_missing(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        result = system_config_repository.get_by_env_and_key(
            TEST_ENV, "nonexistent.key"
        )
        assert result is None

    def test_get_by_env_and_key_returns_none_for_wrong_env(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="wrong_env_test",
            env=TEST_ENV,
            name="Wrong Env Test",
            creator="test_user",
            modifier="test_user",
        )

        wrong_env = "pre" if TEST_ENV != "pre" else "prod"
        assert system_config_repository.get_by_env_and_key(wrong_env, conf_key) is None

        result = system_config_repository.get_by_env_and_key(TEST_ENV, conf_key)
        assert result is not None
        assert result.conf_key == conf_key

    # ── 3. update_config ──

    def test_update_config_value(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        config_id = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="original_value",
            env=TEST_ENV,
            name="Update Test",
            creator="test_user",
            modifier="test_user",
        )

        rows = system_config_repository.update_config(
            config_id=config_id,
            conf_value="updated_value",
        )
        assert rows == 1

        record = system_config_repository.get_by_id(config_id)
        assert record is not None
        assert record.conf_value == "updated_value"

    def test_update_config_name(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        config_id = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="v1",
            env=TEST_ENV,
            name="Original Name",
            creator="test_user",
            modifier="test_user",
        )

        rows = system_config_repository.update_config(
            config_id=config_id,
            name="Updated Name",
        )
        assert rows == 1

        record = system_config_repository.get_by_id(config_id)
        assert record is not None
        assert record.name == "Updated Name"

    def test_update_config_description_and_modifier(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        config_id = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="v1",
            env=TEST_ENV,
            name="Desc Update",
            description="Before",
            creator="test_user",
            modifier="test_user",
        )

        rows = system_config_repository.update_config(
            config_id=config_id,
            description="After",
            modifier="admin_user",
        )
        assert rows == 1

        record = system_config_repository.get_by_id(config_id)
        assert record is not None
        assert record.description == "After"
        assert record.modifier == "admin_user"

    def test_update_config_multiple_fields(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        config_id = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="v1",
            env=TEST_ENV,
            name="Multi Update",
            description="Old description",
            creator="test_user",
            modifier="test_user",
        )

        rows = system_config_repository.update_config(
            config_id=config_id,
            conf_value="v2",
            name="Multi Updated",
            description="New description",
            modifier="super_admin",
        )
        assert rows == 1

        record = system_config_repository.get_by_id(config_id)
        assert record is not None
        assert record.conf_value == "v2"
        assert record.name == "Multi Updated"
        assert record.description == "New description"
        assert record.modifier == "super_admin"

    def test_update_config_no_fields_returns_zero(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        config_id = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="v1",
            env=TEST_ENV,
            name="No Update",
            creator="test_user",
            modifier="test_user",
        )

        rows = system_config_repository.update_config(config_id=config_id)
        assert rows == 0

        record = system_config_repository.get_by_id(config_id)
        assert record is not None
        assert record.conf_value == "v1"

    def test_update_config_missing_id_returns_zero(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        rows = system_config_repository.update_config(
            config_id=99999999,
            conf_value="should_not_work",
        )
        assert rows == 0

    # ── 4. delete_config ──

    def test_delete_config(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        config_id = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="to_delete",
            env=TEST_ENV,
            name="Delete Me",
            creator="test_user",
            modifier="test_user",
        )

        rows = system_config_repository.delete_config(config_id=config_id)
        assert rows == 1

        record = system_config_repository.get_by_id(config_id)
        assert record is None

    def test_delete_config_missing_id_returns_zero(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        rows = system_config_repository.delete_config(config_id=99999999)
        assert rows == 0

    # ── 5. list_configs ──

    def test_list_configs_returns_records(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="list_test_value",
            env=TEST_ENV,
            name="List Test",
            creator="test_user",
            modifier="test_user",
        )

        total, items = system_config_repository.list_configs(env=TEST_ENV)
        assert total >= 1
        keys = [r.conf_key for r in items]
        assert conf_key in keys

    def test_list_configs_pagination(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        for i in range(3):
            system_config_repository.insert_config(
                conf_key=_generate_uuid(),
                conf_value=f"page_test_{i}",
                env=TEST_ENV,
                name=f"Page Config {i}",
                creator="test_user",
                modifier="test_user",
            )
        total, items = system_config_repository.list_configs(
            env=TEST_ENV, page=1, page_size=2
        )
        assert len(items) <= 2
        assert total >= 3

    def test_list_configs_env_filter(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="env_filter_test",
            env=TEST_ENV,
            name="Env Filter Test",
            creator="test_user",
            modifier="test_user",
        )

        other_env = "pre" if TEST_ENV != "pre" else "prod"
        total, items = system_config_repository.list_configs(env=other_env)
        keys = [r.conf_key for r in items]
        assert conf_key not in keys

    def test_list_configs_no_env_filter(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="no_env_filter",
            env=TEST_ENV,
            name="No Filter",
            creator="test_user",
            modifier="test_user",
        )

        total, items = system_config_repository.list_configs()
        assert total >= 1
        keys = [r.conf_key for r in items]
        assert conf_key in keys

    # ── 6. Env isolation ──

    def test_same_key_different_env_isolation(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        other_env = "pre" if TEST_ENV != "pre" else "prod"

        id_env_a = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="value_in_test_env",
            env=TEST_ENV,
            name="Env A Config",
            creator="test_user",
            modifier="test_user",
        )
        id_env_b = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="value_in_other_env",
            env=other_env,
            name="Env B Config",
            creator="test_user",
            modifier="test_user",
        )
        assert id_env_a > 0
        assert id_env_b > 0
        assert id_env_a != id_env_b

        rec_a = system_config_repository.get_by_env_and_key(TEST_ENV, conf_key)
        assert rec_a is not None
        assert rec_a.conf_value == "value_in_test_env"
        assert rec_a.env == TEST_ENV
        assert rec_a.id == id_env_a

        rec_b = system_config_repository.get_by_env_and_key(other_env, conf_key)
        assert rec_b is not None
        assert rec_b.conf_value == "value_in_other_env"
        assert rec_b.env == other_env
        assert rec_b.id == id_env_b

        system_config_repository.delete_config(config_id=id_env_a)
        assert system_config_repository.get_by_id(id_env_a) is None
        assert system_config_repository.get_by_id(id_env_b) is not None

    def test_env_isolation_update_does_not_cross_envs(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        other_env = "pre" if TEST_ENV != "pre" else "prod"

        id_a = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="original_a",
            env=TEST_ENV,
            name="Env A",
            creator="test_user",
            modifier="test_user",
        )
        id_b = system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="original_b",
            env=other_env,
            name="Env B",
            creator="test_user",
            modifier="test_user",
        )

        system_config_repository.update_config(
            config_id=id_a,
            conf_value="updated_a",
            name="Env A Updated",
        )

        rec_a = system_config_repository.get_by_id(id_a)
        assert rec_a is not None
        assert rec_a.conf_value == "updated_a"
        assert rec_a.name == "Env A Updated"

        rec_b = system_config_repository.get_by_id(id_b)
        assert rec_b is not None
        assert rec_b.conf_value == "original_b"
        assert rec_b.name == "Env B"

    def test_env_isolation_list_configs_filters_by_env(
        self,
        system_config_repository: SystemConfigRepository,
        db_transaction,
    ):
        conf_key = _generate_uuid()
        other_env = "pre" if TEST_ENV != "pre" else "prod"

        system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="list_env_a",
            env=TEST_ENV,
            name="List Env A",
            creator="test_user",
            modifier="test_user",
        )
        system_config_repository.insert_config(
            conf_key=conf_key,
            conf_value="list_env_b",
            env=other_env,
            name="List Env B",
            creator="test_user",
            modifier="test_user",
        )

        _, items_a = system_config_repository.list_configs(env=TEST_ENV)
        vals_a = {r.conf_value for r in items_a if r.conf_key == conf_key}
        assert "list_env_a" in vals_a

        _, items_b = system_config_repository.list_configs(env=other_env)
        vals_b = {r.conf_value for r in items_b if r.conf_key == conf_key}
        assert "list_env_b" in vals_b
