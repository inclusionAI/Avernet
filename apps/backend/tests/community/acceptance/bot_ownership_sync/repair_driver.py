"""Explicit maintenance Service API invocation against the live fixture DB.

No HTTP repair route, schema bootstrap, handoff steps, TaskWorker startup, or
sync_once calls. This is manual repair evidence, not business-trigger coverage.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time


def validate_database(database_url: str, root: Path) -> Path:
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    path = Path(url.database or "").resolve()
    if (url.drivername != "sqlite" or url.query or not path.is_file()
            or not path.is_relative_to(root.resolve())):
        raise ValueError("repair requires an existing isolated SQLite database")
    return path


class Events(logging.Handler):
    def __init__(self, bot_pk):
        super().__init__()
        self.bot_pk = bot_pk
        self.values = []

    def emit(self, record):
        # Production diagnostics are finite/payload-free. Retain only enum names.
        text = record.getMessage()
        if f" bot_pk={self.bot_pk} " not in text:
            return
        fields = dict(part.split("=", 1) for part in text.split() if "=" in part)
        if "event" in fields and "kind" in fields:
            self.values.append((fields["kind"], fields["event"]))


async def repair(path, bot_pk, bot_id, entity_id):
    from sqlalchemy import text
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.di.config_bootstrap import register_config_provider
    from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
    from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import BotManagerSyncLifecycle
    from agentclaw.community.core.bot_management.ownership_sync.repair import OwnershipSyncRepairService
    from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncKey
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
    from tests.community.acceptance.bot_ownership_sync.fixture_module import OwnerHandoffFixtureModule

    register_config_provider(DeployProfile.SINGLEBOX)
    injector = build_injector(profile=DeployProfile.SINGLEBOX, extra_modules=[OwnerHandoffFixtureModule(enable_fault_proxy=False)])
    # Resolve DB without calling bootstrap/create_all. It must be the same file
    # and stable identity observed through the live Backend SQL read boundary.
    with avernet_tenant_scope("teamclaw"):
        with injector.get(DatabasePlugin).orm_session() as session:
            actual = session.execute(text("PRAGMA database_list")).mappings().all()
            assert any(row["name"] == "main" and Path(row["file"]).resolve() == path for row in actual)
            row = session.execute(text(
                "SELECT bot_id, entity_id FROM ac_bots WHERE id=:pk AND env='dev' "
                "AND avernet_tenant='teamclaw' AND is_delete=0"
            ), {"pk": bot_pk}).one()
            assert tuple(row) == (bot_id, entity_id), "repair fixture identity mismatch"
        lifecycle = injector.get(BotManagerSyncLifecycle)
        dispatcher = injector.get(BotManagerSyncProtocol)
        events = Events(bot_pk)
        logger = logging.getLogger("agentclaw.community.manager_sync.diagnostics")
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(events)
        try:
            await lifecycle.startup()
            assert dispatcher.stats.attached and not dispatcher.stats.closed
            admission = injector.get(OwnershipSyncRepairService).request_bot(SyncKey("teamclaw", "dev", bot_pk))
            assert admission in {SubmissionResult.QUEUED, SubmissionResult.COALESCED}
            deadline = time.monotonic() + 30
            while dispatcher.stats.entries:
                assert time.monotonic() < deadline, "manual repair did not drain"
                await asyncio.sleep(0.05)
        finally:
            await lifecycle.shutdown()
            logger.removeHandler(events)
            logger.setLevel(old_level)
        assert not any(t.name == "manager-sync-loop" for t in threading.enumerate())
        owner_events = [event for kind, event in events.values if kind == "ownership" and event.startswith("send_")]
        assert owner_events, "manual repair produced no delivery evidence"
        return {
            "evidence": "manual_repair", "database_verified": True,
            "admission": admission.value, "owner_event": owner_events[-1],
            "manager_events": [event for kind, event in events.values if kind == "managers" and event.startswith("send_")],
            "drained": True, "shutdown_clean": True,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-pk", type=int, required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--entity-id", required=True)
    args = parser.parse_args()
    if not (os.environ.get("DEPLOY_PROFILE") == "singlebox"
            and os.environ.get("SINGLEBOX_COVERAGE") == "1"
            and os.environ.get("SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF") == "1"):
        raise ValueError("repair driver requires the explicit acceptance environment")
    root = Path(__file__).resolve().parents[6] / "singlebox/.dependencies"
    path = validate_database(os.environ.get("DATABASE_URL", ""), root)
    from tests.community.acceptance.bot_ownership_sync.fixture_passport import PREFIX
    if not args.bot_id.startswith(PREFIX) or not args.entity_id.startswith(PREFIX):
        raise ValueError("repair is restricted to fixture identities")
    # Read-only preflight: do not allow SQLAlchemy to create a fresh empty file.
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT bot_id, entity_id FROM ac_bots WHERE id=?", (args.bot_pk,)).fetchone()
        assert row == (args.bot_id, args.entity_id), "fixture is absent from the live database"
    print(json.dumps(asyncio.run(repair(path, args.bot_pk, args.bot_id, args.entity_id))))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No traceback containing an environment value or credential-bearing arg.
        raise SystemExit("Owner acceptance manual repair failed") from None
