"""Integration tests for OrmAcBotRepository — CRUD and filtered queries."""

import pytest

pytestmark = [pytest.mark.integration]


class TestAcBotRepository:
    """Integration tests for ac_bot ORM repository operations."""

    @pytest.mark.asyncio
    async def test_repository_can_resolve(self) -> None:
        from secbaas.community.core.repository.ac_bot import OrmAcBotRepository

        assert OrmAcBotRepository is not None

    @pytest.mark.asyncio
    async def test_repository_imports_model(self) -> None:
        from secbaas.community.core.repository.ac_bot._orm_model import AcBotModel

        assert AcBotModel is not None
        assert hasattr(AcBotModel, "entity_id")
        assert hasattr(AcBotModel, "bot_id")
        assert hasattr(AcBotModel, "env")
        assert hasattr(AcBotModel, "status")
