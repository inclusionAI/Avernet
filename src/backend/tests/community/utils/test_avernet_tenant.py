"""Unit tests for the Avernet tenant context carrier."""
import threading

import pytest

from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    avernet_tenant_scope,
    bind_current_avernet_tenant,
    get_current_avernet_tenant,
)

pytestmark = pytest.mark.unit


def test_default_outside_any_scope():
    assert get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT
    assert DEFAULT_AVERNET_TENANT == "teamclaw"


def test_scope_sets_and_resets():
    assert get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT
    with avernet_tenant_scope("acme"):
        assert get_current_avernet_tenant() == "acme"
    assert get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT


def test_scope_nesting():
    with avernet_tenant_scope("outer"):
        assert get_current_avernet_tenant() == "outer"
        with avernet_tenant_scope("inner"):
            assert get_current_avernet_tenant() == "inner"
        assert get_current_avernet_tenant() == "outer"
    assert get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT


def test_scope_resets_even_when_body_raises():
    with pytest.raises(RuntimeError):
        with avernet_tenant_scope("acme"):
            assert get_current_avernet_tenant() == "acme"
            raise RuntimeError("boom")
    # The tenant must not survive a failed request.
    assert get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT


def test_bind_current_tenant_carries_into_thread():
    seen: dict[str, str] = {}

    def work() -> None:
        seen["tenant"] = get_current_avernet_tenant()

    with avernet_tenant_scope("acme"):
        # A bare thread would NOT copy the context var; the wrapper must.
        target = bind_current_avernet_tenant(work)
    # Bind captured "acme"; the thread runs after the scope has exited.
    t = threading.Thread(target=target)
    t.start()
    t.join()

    assert seen["tenant"] == "acme"


def test_bare_thread_does_not_inherit_tenant():
    # Guards the premise of bind_current_avernet_tenant: without it, a spawned
    # thread falls back to the default tenant.
    seen: dict[str, str] = {}

    def work() -> None:
        seen["tenant"] = get_current_avernet_tenant()

    with avernet_tenant_scope("acme"):
        t = threading.Thread(target=work)
        t.start()
        t.join()

    assert seen["tenant"] == DEFAULT_AVERNET_TENANT


def test_bind_preserves_args_and_return():
    def add(a: int, b: int) -> int:
        return a + b

    with avernet_tenant_scope("acme"):
        bound = bind_current_avernet_tenant(add)
    assert bound(2, 3) == 5
