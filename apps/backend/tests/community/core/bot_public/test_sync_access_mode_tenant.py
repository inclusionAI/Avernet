"""The access-mode sync coalescing key is tenant-scoped.

`_sync_access_mode_and_relations` coalesces concurrent syncs for a bot via
process-wide `_syncing_bots` / `_pending_syncs` dicts keyed by `sync_key`. Since
`(owner_id, bot_id)` is unique only *within* a tenant, the key must include the
tenant — otherwise a second tenant's sync for a colliding `(owner_id, bot_id)`
is queued under the first tenant's key and applied to the first tenant's bot by
its (tenant-bound) thread, while its own bot is never synced.

Regression for the P1 review finding on PR #478.
"""
import threading
import time

import pytest

from agentclaw.community.core.bot_public.services.bot_public_service import (
    BotPublicService,
)
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)

pytestmark = pytest.mark.unit


def _wait_until(pred, timeout: float = 5.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


def _bare_service() -> BotPublicService:
    """Only the slice `_sync_access_mode_and_relations` touches."""
    svc = BotPublicService.__new__(BotPublicService)
    svc._sync_lock = threading.Lock()
    svc._syncing_bots = set()
    svc._pending_syncs = {}
    return svc


def test_sync_key_includes_the_current_tenant(monkeypatch):
    svc = _bare_service()
    snap: dict = {}

    def _or_raise(*, bot_id, owner_id, access_mode, public):
        # Snapshot the in-flight key set from inside the running sync thread.
        snap["syncing"] = set(svc._syncing_bots)

    svc._sync_access_mode_and_relations_or_raise = _or_raise

    threads = []
    real_thread = threading.Thread
    monkeypatch.setattr(
        threading,
        "Thread",
        lambda *a, **k: threads.append(real_thread(*a, **k)) or threads[-1],
    )

    with avernet_tenant_scope("tenant-x"):
        svc._sync_access_mode_and_relations("b1", "u1", "OPEN", "1")
    for t in threads:
        t.join(timeout=5)

    assert snap["syncing"] == {"tenant-x:u1:b1"}


def test_colliding_ids_across_tenants_do_not_coalesce(monkeypatch):
    """Tenant B's sync for the same (owner_id, bot_id) must run on its own bot,
    not be swallowed into tenant A's in-flight bucket."""
    svc = _bare_service()

    gate = threading.Event()
    calls = []

    def _or_raise(*, bot_id, owner_id, access_mode, public):
        calls.append((get_current_avernet_tenant(), access_mode, public))
        gate.wait(timeout=5)  # hold the sync so it stays 'in-flight'

    svc._sync_access_mode_and_relations_or_raise = _or_raise

    threads = []
    real_thread = threading.Thread
    monkeypatch.setattr(
        threading,
        "Thread",
        lambda *a, **k: threads.append(real_thread(*a, **k)) or threads[-1],
    )

    # Tenant A starts a sync and is held in _or_raise (in-flight).
    with avernet_tenant_scope("tenant-a"):
        svc._sync_access_mode_and_relations("b1", "u1", "OPEN", "1")
    _wait_until(lambda: len(calls) == 1)

    # Tenant B syncs the SAME (owner_id, bot_id) — its own, different bot.
    with avernet_tenant_scope("tenant-b"):
        svc._sync_access_mode_and_relations("b1", "u1", "RESTRICTED", "0")

    # B must NOT have been queued into A's bucket — it starts its own sync.
    assert svc._pending_syncs == {}
    _wait_until(lambda: len(calls) == 2)

    gate.set()
    for t in threads:
        t.join(timeout=5)

    # Each tenant applied its own values under its own tenant — no crossover.
    by_tenant = {tenant: (am, pub) for tenant, am, pub in calls}
    assert by_tenant == {
        "tenant-a": ("OPEN", "1"),
        "tenant-b": ("RESTRICTED", "0"),
    }
