"""Legacy BFF path and mixed-field behavior across a real Owner change."""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.skill_center.skills import _get_path_params
from agentclaw.community.adapters.http.skill_center.skillsets import _get_path_params as set_paths
from agentclaw.community.core.bot_management.render_screen.services.render_screen_service import RenderScreenService
from agentclaw.community.core.repository.implementations.bot.render_screen import RenderScreenRepository
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from tests.community.core.bot_management.test_resource_handoff import persisted, handoff  # noqa: F401


@pytest.mark.parametrize("resolve", [_get_path_params, set_paths])
@pytest.mark.parametrize("locator", ["A", "B"])
def test_bff_uses_entity_after_owner_change(persisted, resolve, locator):
    db, bots, bot = persisted
    handoff(db, bot["id"])
    ctx = RequestContext(user_id="B", bot_id="service")
    entity, bot_id, engine, runtime, kind, desktop = resolve(
        ctx, locator, bot_repo=bots
    )
    assert (entity, bot_id, engine, runtime, kind, desktop) == (
        "A", "service", "moltis", "moltis", "staff", False
    )


def test_skill_factory_keeps_entity_pool_path_with_distinct_current_owner(persisted):
    db, bots, bot = persisted
    handoff(db, bot["id"])
    pool = MagicMock(return_value=None)
    factory = SkillServiceFactory(
        MagicMock(), bots, MagicMock(), MagicMock(), MagicMock(), MagicMock(),
        MagicMock(), MagicMock(), MagicMock(), pool,
    )
    service = factory.create(entity_id="A", bot_owner_id="B", bot_id="service", engine_type="moltis")
    pool.assert_called_once_with("A", "service", "moltis")
    assert service._device_owner_id == "B"


def test_render_resource_follows_bot_but_creator_and_personal_customizations_do_not(persisted):
    db, bots, bot = persisted
    # Register this lazily loaded model before creating its real table.
    repo = RenderScreenRepository(db)
    repo._Model.__table__.create(db.engine, checkfirst=True)
    owner_record = repo.insert(bot_id="service", owner_id="A", name="owned", cdn_url="/owned.js", creator_id="A")
    private_record = repo.insert(bot_id="service", owner_id="C", name="private", cdn_url="/private.js", creator_id="C")
    handoff(db, bot["id"])
    service = RenderScreenService(repo, bots, MagicMock())
    for locator in ["A", "B"]:
        rows = service.list_render_screens(bot_id="service", owner_id=locator, current_user_id="B")
        assert [(x.id, x.owner_id, x.creator_id) for x in rows] == [(owner_record, "B", "A")]
    service.authorize_render_screen_record(record_id=owner_record, user_id="B")
    with pytest.raises(PermissionError):
        service.authorize_render_screen_record(record_id=owner_record, user_id="A")
    service.authorize_render_screen_record(record_id=private_record, user_id="C")
    with pytest.raises(PermissionError):
        service.authorize_render_screen_record(record_id=private_record, user_id="B")
    created = service.create_render_screen(bot_id="service", owner_id="B", name="new", cdn_url="/new.js", creator_id="B", current_user_id="B")
    assert repo.get_by_id(created).owner_id == "A"
    assert repo.get_by_id(created).creator_id == "B"
