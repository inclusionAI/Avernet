"""Manifest configuration actions through Apply Engine and real persistence."""

from datetime import datetime
import json

import pytest
from sqlalchemy import event

from agentclaw.community.core.apply_engine.outcomes import ApplyStatus, EntryOutcome
from agentclaw.community.core.models.mcp import BotMCPConfig, BotMCPInstallation
from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import ScopedMCPConfigRepository

from .test_capability_full_replace_contract import _context, _rig
from ._mcp_config_support import McpWorld


@pytest.mark.asyncio
async def test_bare_manifest_preserves_the_installed_bots_configuration():
    db, _reader, _runtime, _uploads, engine, _parsed, bot, _skill = _rig(overlap=False)
    with db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.explicit",
            config='{"endpoint_env":"PRE","headers":{"X-Project":"saved"}}', env="dev",
        ))

    report = await engine.apply(
        _context(bot, apply_id="preserve"),
        {"schema_version": 1, "manifest": {"mcp": [{"server_code": "mcp.explicit"}]}},
        apply_id="preserve", trigger="explicit", started_at=datetime.now(),
    )

    assert report.status is ApplyStatus.SUCCEEDED
    entry = next(e for e in report.categories[0].entries if e.identity == "mcp.explicit")
    assert entry.outcome is EntryOutcome.UNCHANGED
    with db.orm_session() as session:
        saved = session.query(BotMCPConfig).filter_by(server_code="mcp.explicit").one()
        assert saved.config == '{"endpoint_env":"PRE","headers":{"X-Project":"saved"}}'


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [None, {"endpoint_env": "PRE"}])
async def test_bare_manifest_can_establish_direct_supply_without_replacing_saved_configuration(config):
    db, _reader, _runtime, _uploads, engine, _parsed, bot, _skill = _rig(overlap=False)
    with db.transactional_orm_session() as session:
        session.query(BotMCPInstallation).filter_by(server_code="mcp.explicit").delete()
        if config is not None:
            session.add(BotMCPConfig(
                bot_id="bot-1", owner_id="entity-1", server_code="mcp.explicit",
                config=json.dumps(config), env="dev",
            ))

    for attempt in range(2):
        report = await engine.apply(
            _context(bot, apply_id=f"preserve-{attempt}"),
            {"schema_version": 1, "manifest": {"mcp": [{"server_code": "mcp.explicit"}]}},
            apply_id=f"preserve-{attempt}", trigger="explicit", started_at=datetime.now(),
        )
        assert report.status is ApplyStatus.SUCCEEDED
        entry = next(e for e in report.categories[0].entries if e.identity == "mcp.explicit")
        assert entry.outcome is (EntryOutcome.CREATED if attempt == 0 else EntryOutcome.UNCHANGED)
        with db.orm_session() as session:
            assert session.query(BotMCPInstallation).filter_by(server_code="mcp.explicit").count() == 1
            saved = session.query(BotMCPConfig).filter_by(server_code="mcp.explicit").one_or_none()
            assert (json.loads(saved.config) if saved else None) == config


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [{}, {"headers": {"X-New": "new"}}])
async def test_explicit_configuration_clears_or_completely_replaces_the_override(config):
    db, _reader, _runtime, _uploads, engine, _parsed, bot, _skill = _rig(overlap=False)
    with db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.explicit",
            config='{"url":"https://old.example/mcp","endpoint_env":"PRE","headers":{"X-Old":"old"}}', env="dev",
        ))
    for attempt in range(2):
        report = await engine.apply(
            _context(bot, apply_id=f"configure-{attempt}"),
            {"schema_version": 1, "manifest": {"mcp": [{"server_code": "mcp.explicit", "config": config}]}},
            apply_id=f"configure-{attempt}", trigger="explicit", started_at=datetime.now(),
        )
        assert report.status is ApplyStatus.SUCCEEDED
        entry = next(e for e in report.categories[0].entries if e.identity == "mcp.explicit")
        assert entry.outcome is (EntryOutcome.UPDATED if attempt == 0 else EntryOutcome.UNCHANGED)
        with db.orm_session() as session:
            saved = session.query(BotMCPConfig).filter_by(server_code="mcp.explicit").one_or_none()
            assert (json.loads(saved.config) if saved else {}) == config


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_valid", [True, False])
async def test_bare_manifest_validates_the_saved_override_instead_of_inherited_configuration(saved_valid):
    world = McpWorld()
    with world.db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.explicit",
            config='{"endpoint_env":"PRE"}', env="dev",
        ))
    # Real Center data makes saved PRE and inherited PROD have opposite
    # validity. The real configuration service resolves and validates both.
    world.center.endpoint_envs["mcp.explicit"] = ("PRE",) if saved_valid else ("PROD",)
    inherited = world.config.validate_bot_override(
        user_id="owner", server_code="mcp.explicit", config=None, engine_type="moltis",
    )
    assert inherited["valid"] is not saved_valid
    report = await world.apply_manifest([{"server_code": "mcp.explicit"}])
    assert report.status is (ApplyStatus.SUCCEEDED if saved_valid else ApplyStatus.FAILED)
    assert world.saved() == {"mcp.explicit": {"endpoint_env": "PRE"}}


@pytest.mark.asyncio
async def test_preserve_does_not_restore_the_value_read_before_a_concurrent_scoped_edit():
    world = McpWorld()
    with world.db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.explicit",
            config='{"headers":{"X-Project":"old"}}', env="dev",
        ))

    def edit_while_center_is_read():
        # This external port callback runs after resolve read the old value;
        # configuration validation and persistence remain their real services.
        ScopedMCPConfigRepository(world.db).replace(
            user_id="entity-1", server_code="mcp.explicit", headers={},
            bot_headers={"bot-1": {"X-Project": "new"}}, owned_bot_ids={"bot-1"},
            endpoint_env="PROD", transport_protocol=None,
        )

    world.center.on_detail = edit_while_center_is_read
    report = await world.apply_manifest([{"server_code": "mcp.explicit"}])
    assert report.status is ApplyStatus.SUCCEEDED
    assert world.saved() == {"mcp.explicit": {"headers": {"X-Project": "new"}}}
    assert world.direct.list_installed_mcps(bot_id="bot-1", owner_id="owner", actor_id="owner") == {"mcp.explicit"}


@pytest.mark.asyncio
async def test_failed_explicit_configuration_write_rolls_back_the_direct_claim_and_reports_failure():
    db, _reader, _runtime, _uploads, engine, _parsed, bot, _skill = _rig(overlap=False)
    with db.transactional_orm_session() as session:
        session.query(BotMCPInstallation).filter_by(server_code="mcp.explicit").delete()

    def reject_config_insert(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO ac_bot_mcp_config"):
            raise RuntimeError("configuration storage unavailable")

    event.listen(db.engine, "before_cursor_execute", reject_config_insert)
    try:
        report = await engine.apply(
            _context(bot, apply_id="failed-config"),
            {"schema_version": 1, "manifest": {"mcp": [{"server_code": "mcp.explicit", "config": {"endpoint_env": "PRE"}}]}},
            apply_id="failed-config", trigger="explicit", started_at=datetime.now(),
        )
    finally:
        event.remove(db.engine, "before_cursor_execute", reject_config_insert)
    assert report.status is ApplyStatus.FAILED
    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).filter_by(server_code="mcp.explicit").count() == 0
        assert session.query(BotMCPConfig).filter_by(server_code="mcp.explicit").count() == 0
