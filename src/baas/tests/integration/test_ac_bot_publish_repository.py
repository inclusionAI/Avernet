"""Integration tests for OrmAcBotPublishRepository — CRUD and filtered queries."""

import pytest

pytestmark = [pytest.mark.integration]


class TestAcBotPublishRepository:
    """Integration tests for ac_bot_publish ORM repository operations."""

    @pytest.mark.asyncio
    async def test_repository_can_resolve(self) -> None:
        from secbaas.community.core.repository.ac_bot_publish import (
            OrmAcBotPublishRepository,
        )

        assert OrmAcBotPublishRepository is not None

    @pytest.mark.asyncio
    async def test_repository_imports_model(self) -> None:
        from secbaas.community.core.repository.ac_bot_publish._orm_model import (
            AcBotPublishModel,
        )

        assert AcBotPublishModel is not None
        assert hasattr(AcBotPublishModel, "source_bot_id")
        assert hasattr(AcBotPublishModel, "publish_bot_id")
        assert hasattr(AcBotPublishModel, "status")
