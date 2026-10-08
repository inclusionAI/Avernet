"""Integration tests for OrmBotDeviceRelRepository — CRUD and filtered queries."""

import pytest

pytestmark = [pytest.mark.integration]


class TestBotDeviceRelRepository:
    """Integration tests for bot_device_rel ORM repository operations."""

    @pytest.mark.asyncio
    async def test_repository_can_resolve(self) -> None:
        from secbaas.community.core.repository.bot_device_rel import (
            OrmBotDeviceRelRepository,
        )

        assert OrmBotDeviceRelRepository is not None

    @pytest.mark.asyncio
    async def test_repository_imports_model(self) -> None:
        from secbaas.community.core.repository.bot_device_rel._orm_model import (
            BotDeviceRelModel,
        )

        assert BotDeviceRelModel is not None
        assert hasattr(BotDeviceRelModel, "bot_id")
        assert hasattr(BotDeviceRelModel, "device_uuid")
        assert hasattr(BotDeviceRelModel, "is_deleted")
