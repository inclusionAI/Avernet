"""Skill parameter reads and writes select the Bot runtime's workspace."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.skill_center.factories import SkillParameterServiceFactory
from agentclaw.community.core.skill_center.services.skill_parameter_service import DEFAULT_PARAMETERS_PATH


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["deepseek_harness", "openclaw"])
async def test_parameter_read_and_write_use_engine_workspace(monkeypatch, engine):
    bot = {"id": 11, "bot_id": "bot-1", "entity_id": "space-7", "entity_type": "project",
           "owner_id": "owner-1", "active_engine": engine}
    bots = MagicMock()
    bots.list_matches_by_legacy_locator.return_value = [bot]
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = SimpleNamespace(provider="arca")
    fs = MagicMock()
    fs.read_file = AsyncMock(return_value=b'{"parameters":{"test":{"value":"old"}}}')
    fs.write_file = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = fs
    monkeypatch.setenv("AIDESKTOP_ROOT", "/home/admin/.aidesktop")
    monkeypatch.setenv("SERVER_ENV", "dev")

    service = await SkillParameterServiceFactory(resolver, dispatcher, bots).create("bot-1", "space-7")
    assert service.get_skill_parameters("test") == {"value": "old"}
    await service.save_skill_parameters("test", {"value": "new"})
    expected = ("/aidesktop/aidesktop_dev/bolt_data/project_space-7/bot-1/deepseek_harness/workspace/skills/skill_parameters.json"
                if engine == "deepseek_harness" else DEFAULT_PARAMETERS_PATH)
    assert fs.read_file.await_args.args[0] == expected
    assert fs.write_file.await_args.args[0] == expected
    if engine == "deepseek_harness":
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "space-7", connection_user_id="owner-1")
    else:
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "space-7")


@pytest.mark.asyncio
async def test_private_dsh_entity_locator_uses_current_owner_for_real_device_authorization(monkeypatch):
    from agentclaw.community.core.devices.services.conn_info_builders.arca_builder import ArcaConnInfoBuilder
    from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
    from agentclaw.community.core.devices.services.device_service import DeviceService

    monkeypatch.setenv("SERVER_ENV", "dev")
    bot = {"id": 11, "bot_id": "bot-1", "entity_id": "space-7", "entity_type": "project",
           "owner_id": "owner-1", "active_engine": "deepseek_harness", "bot_type": "personal", "public": "0"}
    bots = MagicMock()
    bots.list_matches_by_legacy_locator.return_value = [bot]
    bots.get_by_binding_id.return_value = bot
    binding = SimpleNamespace(id=1, device_provider="arca", device_id="device-1", status="ACTIVE",
                              entity_id="space-7", entity_type="project",
                              device_props={"bolt_id": "bot-1", "sandbox_id": "sandbox-1"})
    bindings = MagicMock()
    bindings.get_active_by_bot_and_owner.return_value = binding
    bindings.get_by_id.return_value = binding
    service = DeviceService.__new__(DeviceService)
    service._repo = bindings
    service._bot_query = bots
    service._sandbox_client = MagicMock()
    service._sandbox_client.proxy_base_url.return_value = "http://arca"
    service._compose_device_conn_info = MagicMock(return_value=SimpleNamespace(
        target="ARCA_sandbox-1:20003", token="", engine_type="deepseek_harness", type="remote"))
    resolver = DeviceContextResolver(bindings, bots, ArcaConnInfoBuilder(service), MagicMock(), MagicMock(), MagicMock())
    fs = MagicMock()
    fs.read_file = AsyncMock(return_value=b'{"parameters":{}}')
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = fs

    await SkillParameterServiceFactory(resolver, dispatcher, bots).create("bot-1", "space-7")
    bindings.get_active_by_bot_and_owner.assert_called_once_with("bot-1", "space-7")
    assert fs.read_file.await_args.args[0].endswith("/deepseek_harness/workspace/skills/skill_parameters.json")


@pytest.mark.asyncio
async def test_dsh_entity_keeps_unique_binding_when_owner_has_two_default_bots(tmp_path, monkeypatch):
    import json
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
    from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
    from agentclaw.community.core.devices.services.conn_info_builders.arca_builder import ArcaConnInfoBuilder
    from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
    from agentclaw.community.core.devices.services.device_service import DeviceService
    from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
    from agentclaw.community.core.repository.implementations.devices.device import DeviceRepository
    from agentclaw.community.core.service_bot.repository.models import BotPublishModel
    from agentclaw.community.plugin_api.models import BotModel

    monkeypatch.setenv("SERVER_ENV", "dev")
    engine = create_engine(f"sqlite:///{tmp_path / 'bots.db'}")
    for model in (BotModel, EntityDeviceBinding, BotPublishModel, BotCollaboratorModel):
        model.__table__.create(engine)

    class Database:
        @contextmanager
        def orm_session(self):
            session = sessionmaker(bind=engine, autoflush=False)()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        session = orm_session

    db = Database()
    with db.orm_session() as session:
        for entity in ("space-a", "space-b"):
            binding = EntityDeviceBinding(entity_id=entity, entity_type="project", device_id=entity,
                device_provider="arca", status="ACTIVE", env="dev", applied_by="owner-1",
                device_props=json.dumps({"bolt_id": "default", "sandbox_id": f"sandbox-{entity}"}))
            session.add(binding)
            session.flush()
            session.add(BotModel(bot_id="default", bot_name="Bot", entity_id=entity, entity_type="project",
                owner_id="owner-1", owner_name="Owner", creator_id="owner-1", active_engine="deepseek_harness",
                binding_id=binding.id, status="ACTIVE", env="dev"))
    bots, bindings = BotRepository(db), DeviceRepository(db)
    device_service = DeviceService.__new__(DeviceService)
    device_service._repo, device_service._bot_query = bindings, bots
    device_service._sandbox_client = MagicMock()
    device_service._sandbox_client.proxy_base_url.return_value = "http://arca"
    device_service._compose_device_conn_info = MagicMock(return_value=SimpleNamespace(
        target="ARCA_target:20003", token="", engine_type="deepseek_harness", type="remote"))
    resolver = DeviceContextResolver(bindings, bots, ArcaConnInfoBuilder(device_service),
                                     MagicMock(), MagicMock(), MagicMock())
    fs = MagicMock()
    fs.read_file = AsyncMock(return_value=b'{"parameters":{}}')
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = fs

    await SkillParameterServiceFactory(resolver, dispatcher, bots).create("default", "space-a")
    assert dispatcher.dispatch.call_args.args[0].conn_info["sandbox_id"] == "sandbox-space-a"
    assert "/project_space-a/default/deepseek_harness/workspace/skills/" in fs.read_file.await_args.args[0]
