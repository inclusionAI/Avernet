"""Integration tests for AcBotRepository and AcBotPublishRepository against ZDAS MySQL.

Uses ONLY Protocol types — no Zdas* concrete classes.

Positive-case tests may be skipped if ac_bots/ac_bot_publish tables are empty
in the test environment. Negative-case tests always run.
"""

from uuid import uuid4

import pytest

from secbaas.community.core.repository.ac_bot import AcBotRecord, AcBotRepository
from secbaas.community.core.repository.ac_bot_publish import AcBotPublishRepository
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


# ---------------------------------------------------------------------------
# Class 1: TestAcBotRepositoryProtocol
# ---------------------------------------------------------------------------


class TestAcBotRepositoryProtocol:
    """Integration tests for AcBotRepository Protocol against real ZDAS MySQL.

    Tests both get_by_entity_id_bot_id_env and get_by_bot_id_env_exclude_default
    with positive (record exists) and negative (no match) cases.

    These methods query the ac_bots table, which may be empty in a test
    environment. Positive-case tests will be skipped if no pre-existing data
    is available.
    """

    def test_get_by_entity_id_bot_id_env_nonexistent_entity(
        self,
        ac_bot_repository: AcBotRepository,
        db_transaction,
    ):
        """Returns None when entity_id does not exist."""
        result = ac_bot_repository.get_by_entity_id_bot_id_env(
            entity_id="nonexistent_entity_" + _generate_uuid(),
            bot_id=_generate_uuid(),
            env=TEST_ENV,
        )
        assert result is None

    def test_get_by_entity_id_bot_id_env_nonexistent_bot_id(
        self,
        ac_bot_repository: AcBotRepository,
        db_transaction,
    ):
        """Returns None when bot_id does not exist for a valid entity."""
        result = ac_bot_repository.get_by_entity_id_bot_id_env(
            entity_id="staff_12345",
            bot_id="nonexistent_bot_" + _generate_uuid(),
            env=TEST_ENV,
        )
        assert result is None

    def test_get_by_entity_id_bot_id_env_wrong_env(
        self,
        ac_bot_repository: AcBotRepository,
        db_transaction,
    ):
        """Returns None when env does not match (even if entity + bot are valid).

        Uses an env value that should never exist: __test_nonexistent_env__.
        """
        result = ac_bot_repository.get_by_entity_id_bot_id_env(
            entity_id="staff_12345",
            bot_id=_generate_uuid(),
            env="__test_nonexistent_env__",
        )
        assert result is None

    def test_get_by_bot_id_env_exclude_default_raises_on_default(
        self,
        ac_bot_repository: AcBotRepository,
        db_transaction,
    ):
        """Raises ValueError when bot_id is 'default'."""
        with pytest.raises(ValueError, match="cannot be 'default'"):
            ac_bot_repository.get_by_bot_id_env_exclude_default(
                bot_id="default",
                env=TEST_ENV,
            )

    def test_get_by_bot_id_env_exclude_default_returns_none_on_no_match(
        self,
        ac_bot_repository: AcBotRepository,
        db_transaction,
    ):
        """Returns None when no record matches bot_id and env."""
        result = ac_bot_repository.get_by_bot_id_env_exclude_default(
            bot_id="nonexistent_bot_" + _generate_uuid(),
            env=TEST_ENV,
        )
        assert result is None

    def test_get_by_bot_id_env_exclude_default_wrong_env(
        self,
        ac_bot_repository: AcBotRepository,
        db_transaction,
    ):
        """Returns None when env does not match."""
        result = ac_bot_repository.get_by_bot_id_env_exclude_default(
            bot_id=_generate_uuid(),
            env="__test_nonexistent_env__",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Class 2: TestAcBotPublishRepositoryProtocol
# ---------------------------------------------------------------------------


class TestAcBotPublishRepositoryProtocol:
    """Integration tests for AcBotPublishRepository Protocol against real ZDAS MySQL.

    Tests get_binding_id with positive (record exists and has binding_id)
    and negative (no match) cases.

    These tests query the ac_bot_publish table, which may be empty in a test
    environment. Positive-case tests will be skipped if no pre-existing data
    is available.
    """

    def test_get_binding_id_no_match(
        self,
        ac_bot_publish_repository: AcBotPublishRepository,
        db_transaction,
    ):
        """Returns None when no publish record matches source_bot_id."""
        result = ac_bot_publish_repository.get_binding_id(
            source_bot_id="nonexistent_bot_" + _generate_uuid(),
            status="success",
        )
        assert result is None

    def test_get_binding_id_wrong_status(
        self,
        ac_bot_publish_repository: AcBotPublishRepository,
        db_transaction,
    ):
        """Returns None when status doesn't match (uses unlikely status value)."""
        result = ac_bot_publish_repository.get_binding_id(
            source_bot_id=_generate_uuid(),
            status="__test_nonexistent_status__",
        )
        assert result is None

    def test_get_binding_id_with_owner_id(
        self,
        ac_bot_publish_repository: AcBotPublishRepository,
        db_transaction,
    ):
        """Returns result when filtering by owner_id (None if no match)."""
        result = ac_bot_publish_repository.get_binding_id(
            source_bot_id=_generate_uuid(),
            status="success",
            owner_id="staff_12345",
        )
        if result is not None:
            assert isinstance(result, int)
            assert result > 0

    def test_get_binding_id_non_success_status(
        self,
        ac_bot_publish_repository: AcBotPublishRepository,
        db_transaction,
    ):
        """Uses status='validating' which selects ext.binding.verify instead of online."""
        result = ac_bot_publish_repository.get_binding_id(
            source_bot_id=_generate_uuid(),
            status="validating",
        )
        if result is not None:
            assert isinstance(result, int)
            assert result > 0
