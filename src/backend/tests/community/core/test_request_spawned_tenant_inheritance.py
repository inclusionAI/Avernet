"""Request-spawned threads inherit the request's avernet_tenant.

Task 8 wraps five in-request ``threading.Thread`` targets with
``bind_current_avernet_tenant`` (bot_service ×3, bot_publish_service,
collaborator_service). These tests exercise the exact spawn shapes used at
those sites — ``target`` only, ``target`` + ``kwargs``, and a run-and-join
helper — so the wrapping contract they depend on stays intact.
"""
import threading

import pytest

from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    avernet_tenant_scope,
    bind_current_avernet_tenant,
    get_current_avernet_tenant,
)

pytestmark = pytest.mark.unit


def test_target_only_thread_inherits_tenant():
    """Shape used by do_allocate / _update_cron_workflow / _do_restart."""
    seen = {}

    def work():
        seen["tenant"] = get_current_avernet_tenant()

    with avernet_tenant_scope("acme"):
        t = threading.Thread(target=bind_current_avernet_tenant(work), daemon=True)
    t.start()
    t.join()
    assert seen["tenant"] == "acme"


def test_thread_with_kwargs_inherits_tenant_and_passes_kwargs():
    """Shape used by _refresh_codefuse_token_on_device (target + kwargs)."""
    seen = {}

    def work(*, bot_id, user_id):
        seen["tenant"] = get_current_avernet_tenant()
        seen["bot_id"] = bot_id
        seen["user_id"] = user_id

    with avernet_tenant_scope("acme"):
        t = threading.Thread(
            target=bind_current_avernet_tenant(work),
            kwargs={"bot_id": "b1", "user_id": "u1"},
            daemon=True,
        )
    t.start()
    t.join()
    assert seen == {"tenant": "acme", "bot_id": "b1", "user_id": "u1"}


def test_run_and_join_helper_inherits_tenant():
    """Shape used by collaborator_service._run_coro_blocking (spawn + join)."""
    box = {}

    def runner():
        box["tenant"] = get_current_avernet_tenant()

    with avernet_tenant_scope("acme"):
        t = threading.Thread(target=bind_current_avernet_tenant(runner), daemon=True)
        t.start()
        t.join()
    assert box["tenant"] == "acme"


def test_default_tenant_when_spawned_outside_a_request():
    """Wrapping outside any scope captures the default — background-safe."""
    seen = {}

    def work():
        seen["tenant"] = get_current_avernet_tenant()

    t = threading.Thread(target=bind_current_avernet_tenant(work), daemon=True)
    t.start()
    t.join()
    assert seen["tenant"] == DEFAULT_AVERNET_TENANT
