"""RenderScreen owner-scope 漂移启动订正(backfill)测试。"""
import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.render_screen.backfill import (
    RenderScreenOwnerScopeBackfill,
    backfill_render_screen_owner_scope,
    compute_drift_repairs,
)
from agentclaw.community.core.bot_management.render_screen.models import (
    RenderScreenRecord,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.bot.render_screen import (
    RenderScreenRepository,
)

from tests.community.core.bot_management.test_resource_handoff import (
    Database,
)


@pytest.fixture
def sqlite_db(monkeypatch):
    """SQLite 内存库 + 会话,与 test_resource_handoff 同一桩。"""
    monkeypatch.setenv("SERVER_ENV", "dev")
    db = Database()
    yield db
    db.engine.dispose()


def _row(*, id, bot_id="bot_x", owner_id="user_001") -> RenderScreenRecord:
    return RenderScreenRecord(
        id=id,
        bot_id=bot_id,
        owner_id=owner_id,
        name=f"组件库{id}",
        cdn_url=f"https://cdn.example.com/v{id}.js",
        env="dev",
        creator_id="user_001",
        is_delete=0,
        gmt_create=datetime(2026, 11, 5, 10, 0, 0),
        gmt_modified=datetime(2026, 11, 5, 10, 0, 0),
    )


def _bot(*, owner_id="owner_001", entity_id="owner_001", **extra):
    base = {"bot_id": "bot_x", "owner_id": owner_id, "entity_id": entity_id}
    base.update(extra)
    return base


class TestComputeDriftRepairs:
    def test_canonical_entity_row_is_untouched(self):
        rows = [_row(id=1, owner_id="owner_001")]  # == entity
        repairs = compute_drift_repairs(rows, {"bot_x": [_bot(entity_id="owner_001")]})
        assert repairs == []

    def test_owner_raw_value_row_is_untouched(self):
        # 归属者裸值(历史遗留形态)仍属合法 scope
        rows = [_row(id=1, owner_id="999888")]
        bots = [_bot(owner_id="999888", entity_id="owner_001")]
        assert compute_drift_repairs(rows, {"bot_x": bots}) == []

    def test_drift_on_unique_bot_gets_repair_to_entity(self):
        rows = [_row(id=1, owner_id="collab_001")]
        bots = {"bot_x": [_bot(owner_id="owner_001", entity_id="owner_001")]}
        repairs = compute_drift_repairs(rows, bots)
        assert [(r.record_id, r.to_owner_id) for r in repairs] == [(1, "owner_001")]

    def test_drift_on_shared_bot_id_is_skipped(self):
        # 'default' 一类共享 bot_id:live bot 多行,归属无法单值判定
        rows = [_row(id=1, bot_id="default", owner_id="collab_001")]
        bots = {
            "default": [
                _bot(owner_id="owner_001", entity_id="owner_001"),
                _bot(owner_id="owner_002", entity_id="owner_002"),
            ]
        }
        assert compute_drift_repairs(rows, bots) == []

    def test_drift_without_live_bot_row_is_skipped(self):
        rows = [_row(id=1, owner_id="collab_001")]
        assert compute_drift_repairs(rows, {}) == []

    def test_empty_entity_id_is_skipped(self):
        rows = [_row(id=1, owner_id="collab_001")]
        assert compute_drift_repairs(rows, {"bot_x": [_bot(entity_id="")]}) == []


class TestBackfillAgainstSQLite:
    def test_backfill_repairs_drift_and_keeps_others(self, sqlite_db):
        db = sqlite_db
        bots_repo = BotRepository(db)
        screens = RenderScreenRepository(db)
        screens._Model.__table__.create(db.engine, checkfirst=True)

        bots_repo.insert(
            {
                "bot_id": "20260724_tilcdwii",
                "entity_id": "272477",
                "entity_type": "staff",
                "owner_id": "272477",
                "creator_id": "272477",
                "bot_type": "normal",
                "active_engine": "openclaw",
                "status": "ACTIVE",
            }
        )
        # 共享 bot_id:两个 owner 各一行(default 类)
        for owner in ("100074", "100091"):
            bots_repo.insert(
                {
                    "bot_id": "default",
                    "entity_id": owner,
                    "entity_type": "staff",
                    "owner_id": owner,
                    "creator_id": owner,
                    "bot_type": "normal",
                    "active_engine": "openclaw",
                    "status": "ACTIVE",
                }
            )

        canonical = screens.insert(
            bot_id="20260724_tilcdwii", owner_id="272477",
            name="ok-lib", cdn_url="https://cdn.example.com/ok.js",
            creator_id="272477",
        )
        drift = screens.insert(
            bot_id="20260724_tilcdwii", owner_id="378611",
            name="rpp-prod", cdn_url="https://cdn.example.com/rpp.js",
            creator_id="378611",
        )
        shared_user_row = screens.insert(
            bot_id="default", owner_id="100074",
            name="asfui", cdn_url="https://cdn.example.com/asfui.js",
            creator_id="100074",
        )

        assert backfill_render_screen_owner_scope(screens, bots_repo) == 1

        assert screens.get_by_id(canonical).owner_id == "272477"
        assert screens.get_by_id(drift).owner_id == "272477"  # 已归位
        assert screens.get_by_id(shared_user_row).owner_id == "100074"

        # 幂等:再跑一遍不再修复
        assert backfill_render_screen_owner_scope(screens, bots_repo) == 0

    def test_service_list_sees_repaired_row_after_backfill(self, sqlite_db, monkeypatch):
        """端到端语义:订正后,归属者按 owner 维度查询能查到漂移配置。"""
        from agentclaw.community.core.bot_management.render_screen.services.render_screen_service import (
            RenderScreenService,
        )

        db = sqlite_db
        bots_repo = BotRepository(db)
        screens = RenderScreenRepository(db)
        screens._Model.__table__.create(db.engine, checkfirst=True)
        collaborators = MagicMock()

        bots_repo.insert(
            {
                "bot_id": "bot_svc",
                "entity_id": "272477",
                "entity_type": "staff",
                "owner_id": "272477",
                "creator_id": "272477",
                "bot_type": "service",
                "active_engine": "teclaw",
                "status": "ACTIVE",
            }
        )
        drift = screens.insert(
            bot_id="bot_svc", owner_id="435979",
            name="rpp-prod", cdn_url="https://cdn.example.com/rpp.js",
            creator_id="435979",
        )
        service = RenderScreenService(screens, bots_repo, collaborators)

        # 修复前:归属者查询查不到
        assert service.list_render_screens(
            bot_id="bot_svc", owner_id="272477", current_user_id="272477"
        ) == []

        backfill_render_screen_owner_scope(screens, bots_repo)

        rows = service.list_render_screens(
            bot_id="bot_svc", owner_id="272477", current_user_id="272477"
        )
        assert [r.id for r in rows] == [drift]
        # 订正后,归属者接手管理(authorize 通过)
        service.authorize_render_screen_record(record_id=drift, user_id="272477")


class TestLifecycleParticipant:
    def test_startup_is_best_effort_and_swallows_errors(self):
        render_repo = MagicMock()
        render_repo.list_all_for_backfill.side_effect = RuntimeError("db down")
        participant = RenderScreenOwnerScopeBackfill(render_repo, MagicMock())
        # 不抛:数据订正不允许阻塞应用启动
        asyncio.run(participant.startup())

    def test_startup_runs_backfill(self):
        render_repo = MagicMock()
        render_repo.list_all_for_backfill.return_value = []
        bot_repo = MagicMock()
        participant = RenderScreenOwnerScopeBackfill(render_repo, bot_repo)
        asyncio.run(participant.startup())
        render_repo.list_all_for_backfill.assert_called_once()
        bot_repo.list_live_by_bot_id.assert_not_called()  # 无行即零查询
