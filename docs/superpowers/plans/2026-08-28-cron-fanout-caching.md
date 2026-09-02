# Cron Fan-out Caching (P0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the cron listing fan-out latency (measured 2.3–2.8 s on pre for `GET /openapi/v1/bots/routines/all`) with two process-level caches: a TTL cache for BaaS connection-info lookups (ws-info / http-info) and a negative cache that skips bindings whose adapter last failed with a "sandbox destroyed" verdict.

**Architecture:** `CronRelayService.list_all_crons` fans out one adapter call per runtime target, and every target resolves BaaS connection info over HTTP (130–160 ms each, no reuse across requests — AntLogs trace `0b446a1717878836076095311e05a8`). The two caches attach at the two chokepoints this repo owns: `BaasService` (a DI singleton — every resolver/transport caller benefits) and `CronRuntimeTargetMixin` (the relay fetch path used by both `list_all_crons` and `list_running_crons`). The corp-profile `HttpDeviceAdapterTransport` itself lives out of this repo and is deliberately not touched.

**Tech Stack:** Python 3.12, FastAPI backend (`src/backend`), pytest + LocalHttpClient/MockSeam stubs, `threading.Lock` + `time.monotonic` TTL pattern (precedent: `baas_invoke_transport._HTTP_INFO_TTL_SECONDS`).

**Evidence base (2026-08-28 AntLogs, pre):** one 2833 ms request = ~675 ms invisible sync DB (bot list N+1) + ~551 ms stall + ~950 ms device queries + BaaS HTTP resolution + ~520 ms adapter GETs, of which 6×404 to destroyed sandboxes of the caller's own bot `20260703_mem5n5qd`. Response body 834 bytes. This plan ships the two P0s; the DB N+1 (P1) is a separate plan.

**Design guardrails:**

- TTL values follow the validated in-repo precedent: tokens minted by BaaS `ws-info`/`http-info` comfortably outlive 30 s (`baas_invoke_transport.py` comment). 30 s staleness of a connection URL is the accepted worst case.
- The 401-refresh self-healing of `BaasInvokeTransport` must survive: the retry path gets a `force_refresh=True` bypass so a stale cached token is re-resolved, not replayed.
- The negative cache is binding-level with a 60 s TTL: a bot whose instances are recreated heals within one TTL window; a healthy binding is never recorded (only bindings that *already* failed with the destroyed-sandbox signature are skipped).
- Both caches are instance state on DI singletons, so they are process-local and lost on restart — no cross-process coherence problem.
- No public-contract change: no OpenAPI surface, gateway, or schema edits. `failed_targets` gains a new `reason` value (`sandbox_destroyed_cached`), which is informational-only.

---

### Task 1: Create the feature branch

**Files:** none

- [ ] **Step 1: Branch from REL20260828**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet
git fetch origin REL20260828
git switch -c feat/cron-fanout-caching-REL20260828 origin/REL20260828
git log -1 --oneline   # expect 70061fa2c (REL20260828 tip)
```

---

### Task 2: `BaasService.get_http_info` TTL cache

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/service_bot/services/baas_service.py`
- Test: `src/backend/tests/community/core/service_bot/services/test_baas_service_conn_info_cache.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/backend/tests/community/core/service_bot/services/test_baas_service_conn_info_cache.py`:

```python
"""TTL cache for BaasService connection-info lookups (ws-info / http-info).

Evidence (2026-08-28 pre, trace 0b446a1717878836076095311e05a8): every cron
fan-out target resolves BaaS http/ws info over HTTP (130-160 ms each) with no
reuse across requests. BaasService is a DI singleton, so a 30 s process-level
cache — the TTL already validated by ``baas_invoke_transport._HTTP_INFO_TTL_SECONDS``
— serialises nothing and removes the per-request round trips.

Covered here: hit on repeated params, miss on param variation, TTL expiry,
errors not cached, ``force_refresh`` bypass, cap eviction, and the same set
for ``get_ws_info_by_bot_uuid``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services import baas_service as baas_module
from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient


class _FakeClock:
    """Stand-in for the ``time`` module, advanceable in tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _get_http_info_ok(response):
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "code": 0,
        "data": {"http_url": "http://container:20010", "token": "tok", "target": "TGT"},
    }
    return mock


def _make_service() -> tuple[BaasService, LocalHttpClient]:
    http = LocalHttpClient(base_url="http://baas.test")
    service = BaasService(
        deploy_composer=ManagedDeployConfigComposer(
            storage_path=MagicMock(),
            sandbox_registry=MagicMock(),
            bot_repo=MagicMock(),
        ),
        startup_script_reader=MagicMock(**{"get_body.return_value": ""}),
        baas_api_base="http://baas.test",
        tenant="tnt",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=http,
        general_http_client=LocalHttpClient(base_url=""),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )
    service._device_binding_repo.get_by_id.return_value = SimpleNamespace(
        device_id="BOT-1"
    )
    return service, http


def _stub_http_info_response(
    http: LocalHttpClient, *, token: str = "tok"
) -> None:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "code": 0,
        "data": {"http_url": "http://container:20010", "token": token, "target": "TGT"},
    }
    http.set_response("get", mock)


@pytest.mark.unit
def test_get_http_info_reuses_cached_result_between_calls():
    service, http = _make_service()
    _stub_http_info_response(http)

    first = service.get_http_info(bind_id=7, port=20010, path="/api/cron")
    second = service.get_http_info(bind_id=7, port=20010, path="/api/cron")

    assert first is second
    assert len(http.calls_to("get")) == 1


@pytest.mark.unit
def test_get_http_info_cache_key_distinguishes_params():
    service, http = _make_service()
    _stub_http_info_response(http)

    service.get_http_info(bind_id=7, port=20010, path="/api/cron")
    service.get_http_info(bind_id=7, port=20010, path="/api/cron", device_uuid="DEV-2")
    service.get_http_info(bind_id=8, port=20010, path="/api/cron")

    assert len(http.calls_to("get")) == 3


@pytest.mark.unit
def test_get_http_info_cached_entry_expires_after_ttl(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(baas_module, "time", clock)
    service, http = _make_service()
    _stub_http_info_response(http, token="tok-1")

    service.get_http_info(bind_id=7, port=20010, path="/api/cron")
    clock.now += baas_module.BAAS_CONN_INFO_TTL_SECONDS + 1
    _stub_http_info_response(http, token="tok-2")

    result = service.get_http_info(bind_id=7, port=20010, path="/api/cron")

    assert result.token == "tok-2"
    assert len(http.calls_to("get")) == 2


@pytest.mark.unit
def test_get_http_info_does_not_cache_failures():
    service, http = _make_service()
    boom = MagicMock()
    boom.raise_for_status.return_value = None
    boom.json.return_value = {"code": 1, "message": "BaaS exploded"}
    http.set_response("get", boom)

    with pytest.raises(BaasServiceError):
        service.get_http_info(bind_id=7, port=20010, path="/api/cron")

    _stub_http_info_response(http)
    result = service.get_http_info(bind_id=7, port=20010, path="/api/cron")

    assert result.token == "tok"
    assert len(http.calls_to("get")) == 2


@pytest.mark.unit
def test_get_http_info_force_refresh_bypasses_cache():
    service, http = _make_service()
    _stub_http_info_response(http)

    service.get_http_info(bind_id=7, port=20010, path="/api/cron")
    result = service.get_http_info(
        bind_id=7, port=20010, path="/api/cron", force_refresh=True
    )

    assert result.token == "tok"
    assert len(http.calls_to("get")) == 2


@pytest.mark.contract
def test_conn_info_cache_evicts_when_over_cap(monkeypatch):
    monkeypatch.setattr(baas_module, "BAAS_CONN_INFO_CACHE_MAX_ENTRIES", 2)
    service, http = _make_service()
    _stub_http_info_response(http)
    service._device_binding_repo.get_by_id.side_effect = lambda bid: SimpleNamespace(
        device_id=f"BOT-{bid}"
    )

    service.get_http_info(bind_id=1, port=20010, path="/api/cron")
    service.get_http_info(bind_id=2, port=20010, path="/api/cron")
    service.get_http_info(bind_id=3, port=20010, path="/api/cron")  # cap hit -> clear
    service.get_http_info(bind_id=1, port=20010, path="/api/cron")  # re-resolve

    assert len(http.calls_to("get")) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet/src/backend
uv run pytest tests/community/core/service_bot/services/test_baas_service_conn_info_cache.py -v
```

Expected: FAIL — `TypeError: get_http_info() got an unexpected keyword argument 'force_refresh'` (and `AttributeError: ... BAAS_CONN_INFO_TTL_SECONDS` for the eviction test).

- [ ] **Step 3: Implement the cache**

In `src/backend/src/agentclaw/community/core/service_bot/services/baas_service.py`:

3a. Add module constants after the existing `STARTUP_SCRIPT_LOG` constant block (near line 96):

```python
#: Process-level TTL for BaaS connection-info lookups (ws-info / http-info).
#: The BaaS endpoints mint (url, token) pairs whose lifetime comfortably
#: exceeds this window — the same 30 s already validated by
#: ``baas_invoke_transport._HTTP_INFO_TTL_SECONDS`` — and a cron-relay fan-out
#: resolves them per binding per request (measured 130-160 ms per round trip,
#: pre 2026-08-28). Tokens that go stale anyway are recovered by the
#: ``force_refresh`` bypass used on retry paths.
BAAS_CONN_INFO_TTL_SECONDS = 30.0

#: Hard cap on distinct cached lookups. Treats the cache as a small working
#: set (active bindings × param variants), not an unbounded key space.
BAAS_CONN_INFO_CACHE_MAX_ENTRIES = 512
```

3b. In `BaasService.__init__`, after `self._startup_script_reader = startup_script_reader` (near line 475):

```python
        # Connection-info caches (ws-info / http-info). Instance state on a
        # DI singleton, so this is process-local and lost on restart. The lock
        # guards only the dict read/update — NOT the HTTP call — so
        # resolutions for different keys stay parallel and same-key misses at
        # worst duplicate one call whose result the later writer overwrites.
        # Errors are never cached, so a transient BaaS failure self-heals on
        # the next call. Cached dataclass instances are shared: callers must
        # treat them as read-only.
        self._http_info_lock = threading.Lock()
        self._http_info_cache: dict[tuple, tuple[float, HttpConnectionInfo]] = {}
        self._ws_info_lock = threading.Lock()
        self._ws_info_cache: dict[
            tuple, tuple[float, BotWsConnectionInfoResponse]
        ] = {}
```

3c. Add `import threading` to the import block (after `import time`, line 26).

3d. Add the put helper as a private method (place it right before `get_http_info`, near line 3309):

```python
    def _conn_cache_put(
        self,
        lock: threading.Lock,
        cache: dict,
        key: tuple,
        value: Any,
    ) -> None:
        """Store a successful connection-info result with a TTL, bounded."""
        now = time.monotonic()
        with lock:
            if len(cache) >= BAAS_CONN_INFO_CACHE_MAX_ENTRIES:
                for stale_key in [
                    k for k, (deadline, _) in cache.items() if deadline <= now
                ]:
                    del cache[stale_key]
                if len(cache) >= BAAS_CONN_INFO_CACHE_MAX_ENTRIES:
                    # All live (recently written) but the key space still grew
                    # past the cap — the working-set assumption is wrong, so
                    # reset rather than silently grow unbounded.
                    cache.clear()
            cache[key] = (now + BAAS_CONN_INFO_TTL_SECONDS, value)
```

3e. Modify `get_http_info` signature (line 3309) to add the kwarg and the cache:

```python
    def get_http_info(
        self,
        *,
        bind_id: int,
        port: int,
        path: str = "",
        tenant: Optional[str] = None,
        device_affinity: Optional[str] = None,
        device_uuid: Optional[str] = None,
        ws_conn_mode: Optional[str] = None,
        timeout: float = 5.0,
        force_refresh: bool = False,
    ) -> HttpConnectionInfo:
```

Inside the body, insert after the docstring (before the `device_binding_repo` lookup, so a hit also skips the binding DB query):

```python
        cache_key = (
            bind_id,
            port,
            path,
            tenant or self._tenant,
            device_affinity,
            device_uuid,
            ws_conn_mode,
        )
        if not force_refresh:
            with self._http_info_lock:
                cached = self._http_info_cache.get(cache_key)
            if cached is not None and cached[0] > time.monotonic():
                logger.info(
                    "[BaasService.get_http_info] cache hit: bind_id=%s, "
                    "expires_in=%.1fs",
                    bind_id,
                    cached[0] - time.monotonic(),
                )
                return cached[1]
```

And just before the final `return result` (after the `HttpConnectionInfo` is built, line ~3398):

```python
            self._conn_cache_put(
                self._http_info_lock, self._http_info_cache, cache_key, result
            )
```

Document the param in the docstring `Args:` block:

```python
            force_refresh: Bypass a fresh cache entry and re-resolve, overwriting
                it. The 401-retry path of ``BaasInvokeTransport`` sets this so a
                token that went stale inside the TTL is replaced instead of
                replayed.
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/community/core/service_bot/services/test_baas_service_conn_info_cache.py -v
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/service_bot/services/baas_service.py ../../tests/community/core/service_bot/services/test_baas_service_conn_info_cache.py
git commit -m "perf(backend): cache BaaS http-info lookups per (binding, params) with 30s TTL"
```

(Run the commit from `src/backend`; paths above are repo-relative — use `git add` from the repo root with the full paths shown in `git status`.)

---

### Task 3: Preserve the 401-refresh self-heal in `BaasInvokeTransport`

**Why now, before ws-info caching:** without this, a token that expires inside the 30 s TTL is replayed instead of refreshed — the wait between the two cache tasks would ship a degraded intermediate state.

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/devices/services/baas_invoke_transport.py:160`
- Test: `src/backend/tests/community/plugins/prod/test_baas_invoke_transport.py` (update 2 existing tests)

- [ ] **Step 1: Update the two existing tests**

In `test_http_info_resolution_carries_the_instance_identity` (line ~360), the assertion at the end becomes:

```python
    svc.get_http_info.assert_called_once_with(
        bind_id=7,
        port=20003,
        path="/api/file/read",
        tenant="team_claw",
        device_uuid="DEV-xyz",
        force_refresh=False,
    )
```

In `test_rejected_token_refreshes_http_info_and_retries_once` (line ~384), add after the existing `get_http_info.call_count == 2` assertion:

```python
    assert [
        c.kwargs.get("force_refresh") for c in svc.get_http_info.call_args_list
    ] == [False, True]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/community/plugins/prod/test_baas_invoke_transport.py -v
```

Expected: the two updated tests FAIL (call kwargs missing `force_refresh`).

- [ ] **Step 3: Pass the bypass flag on the retry resolution**

In `BaasInvokeTransport._http_info` (line ~160), change the `get_http_info` call:

```python
            info = self._baas_service.get_http_info(
                bind_id=self._bind_id,
                port=self._engine_port,
                path=path,
                tenant=self._tenant,
                device_uuid=self._device_uuid,
                # A retry means the caller was just rejected on this entry; the
                # service-level TTL cache must hand back a fresh resolution,
                # not the same stale token it still considers fresh.
                force_refresh=stale is not None,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/community/plugins/prod/test_baas_invoke_transport.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "fix(backend): force-refresh BaaS http-info on 401 retry so the TTL cache never replays a stale token"
```

---

### Task 4: `BaasService.get_ws_info_by_bot_uuid` TTL cache

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/service_bot/services/baas_service.py:1891`
- Test: `src/backend/tests/community/core/service_bot/services/test_baas_service_conn_info_cache.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test_baas_service_conn_info_cache.py`:

```python
def _stub_ws_info_response(http: LocalHttpClient, *, token: str = "wtok") -> None:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "code": 0,
        "data": {
            "ws_url": "ws://container:20003/api/openclaw/ws",
            "token": token,
            "target": "TGT",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }
    http.set_response("get", mock)


@pytest.mark.unit
def test_get_ws_info_by_bot_uuid_reuses_cached_result_between_calls():
    service, http = _make_service()
    _stub_ws_info_response(http)

    first = service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")
    second = service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")

    assert first is second
    assert len(http.calls_to("get")) == 1


@pytest.mark.unit
def test_get_ws_info_cache_key_distinguishes_params():
    service, http = _make_service()
    _stub_ws_info_response(http)

    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")
    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-2")
    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1", device_affinity="240841")

    assert len(http.calls_to("get")) == 3


@pytest.mark.unit
def test_get_ws_info_cached_entry_expires_after_ttl(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(baas_module, "time", clock)
    service, http = _make_service()
    _stub_ws_info_response(http, token="wtok-1")

    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")
    clock.now += baas_module.BAAS_CONN_INFO_TTL_SECONDS + 1
    _stub_ws_info_response(http, token="wtok-2")

    result = service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")

    assert result.token == "wtok-2"
    assert len(http.calls_to("get")) == 2


@pytest.mark.unit
def test_get_ws_info_by_bot_uuid_force_refresh_bypasses_cache():
    service, http = _make_service()
    _stub_ws_info_response(http)

    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")
    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1", force_refresh=True)

    assert len(http.calls_to("get")) == 2


@pytest.mark.contract
def test_get_ws_info_error_is_not_cached():
    service, http = _make_service()
    boom = MagicMock()
    boom.raise_for_status.return_value = None
    boom.json.return_value = {"code": 1, "message": "ws-info exploded"}
    http.set_response("get", boom)

    with pytest.raises(BaasServiceError):
        service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")

    _stub_ws_info_response(http)
    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")

    assert len(http.calls_to("get")) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/community/core/service_bot/services/test_baas_service_conn_info_cache.py -v
```

Expected: the 5 new tests FAIL with `TypeError: ... unexpected keyword argument 'force_refresh'`.

- [ ] **Step 3: Implement**

Modify `get_ws_info_by_bot_uuid` (line 1891) — signature gains `force_refresh: bool = False`; the cache check goes after `effective_tenant = tenant or self._tenant` and before the `logger.info(... "Getting ws info" ...)` line; the put goes right before the final `return result`:

```python
    def get_ws_info_by_bot_uuid(
        self,
        bot_uuid: str,
        port: int = 20003,
        path: str = "/api/openclaw/ws",
        tenant: str = "",
        device_affinity: Optional[str] = None,
        device_uuid: Optional[str] = None,
        ws_conn_mode: Optional[str] = None,
        force_refresh: bool = False,
    ) -> BotWsConnectionInfoResponse:
```

```python
        effective_tenant = tenant or self._tenant
        ws_cache_key = (
            bot_uuid,
            port,
            path,
            effective_tenant,
            device_affinity,
            device_uuid,
            ws_conn_mode,
        )
        if not force_refresh:
            with self._ws_info_lock:
                cached = self._ws_info_cache.get(ws_cache_key)
            if cached is not None and cached[0] > time.monotonic():
                logger.info(
                    "[BaasService.get_ws_info_by_bot_uuid] cache hit: "
                    "bot_uuid=%s, expires_in=%.1fs",
                    bot_uuid,
                    cached[0] - time.monotonic(),
                )
                return cached[1]
```

```python
            self._conn_cache_put(
                self._ws_info_lock, self._ws_info_cache, ws_cache_key, result
            )
            return result
```

Also document `force_refresh` in the `Args:` docstring block (same wording as `get_http_info`).

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/community/core/service_bot/services/test_baas_service_conn_info_cache.py tests/community/core/devices/services/../../plugins/prod/test_baas_invoke_transport.py -v
```

Expected: all PASS. Also run the ws-info consumer suites for regressions:

```bash
uv run pytest tests/community/core/cron -q && uv run pytest tests/community/core/grt_chat -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "perf(backend): cache BaaS ws-info lookups with the shared 30s connection-info TTL"
```

---

### Task 5: Destroyed-sandbox negative cache in the cron fan-out

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/cron/services/cron_runtime_targets.py`
- Modify: `src/backend/src/agentclaw/community/core/cron/services/cron_relay.py:90-93`
- Test: `src/backend/tests/community/core/cron/services/test_cron_relay_sandbox_skip.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/backend/tests/community/core/cron/services/test_cron_relay_sandbox_skip.py`:

```python
"""Binding-level negative cache for destroyed sandboxes in the cron fan-out.

Pre 2026-08-28 evidence: a bot's instances get reclaimed while the binding row
stays ACTIVE, and every listing/running fan-out then pays a 404 precheck round
trip per destroyed instance, forever (trace 0b446a1717878836076095311e05a8:
6 destroyed sandboxes of the caller's own bot per request). The relay is a
singleton, so a binding-keyed "sandbox destroyed" verdict with a short TTL
skips the prepare+invoke chain until it expires.

Covered: verdict recorded from both failure shapes (error dict / raised
exception), skip skips the transport, TTL expiry re-invokes, non-destroyed
failures are never recorded, and ``list_all_crons`` surfaces the skip as a
failed target with an explicit reason.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.services import cron_runtime_targets
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget,
)


_DESTROYED_MSG = (
    'Adapter returned HTTP 404: {"message":"报错类型: 沙箱已销毁, err=sandbox destroyed"}'
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 2000.0

    def monotonic(self) -> float:
        return self.now


def _make_service(*, transport_invoke: AsyncMock) -> CronRelayService:
    svc = CronRelayService(
        bot_provider=MagicMock(),
        device_provider=MagicMock(),
        transport=MagicMock(),
        resolver=MagicMock(),
        template_repo=MagicMock(),
        publish_repo=MagicMock(),
    )
    ctx = MagicMock()
    ctx.conn_info = {"url": "http://adapter"}
    svc._prepare_runtime_query_async = AsyncMock(return_value=(ctx, None))
    svc._invoke_transport = transport_invoke
    return svc


def _target(binding_id: int = 1) -> CronRuntimeTarget:
    return CronRuntimeTarget(
        bot_id="bot-1",
        bot_name="bot-1",
        owner_id="user-1",
        bot_type="service",
        runtime_stage="draft",
        binding_id=binding_id,
    )


@pytest.mark.asyncio
async def test_destroyed_sandbox_failure_marks_binding():
    invoke = AsyncMock(
        return_value={"success": False, "error": _DESTROYED_MSG}
    )
    svc = _make_service(transport_invoke=invoke)

    result = await svc._fetch_runtime_target_crons(_target(1), "user-1")

    assert result["reason"] == "cron_api_failed"
    assert 1 in svc._sandbox_down_until


@pytest.mark.asyncio
async def test_destroyed_sandbox_exception_marks_binding():
    invoke = AsyncMock(side_effect=ValueError(_DESTROYED_MSG))
    svc = _make_service(transport_invoke=invoke)

    result = await svc._fetch_runtime_target_crons(_target(2), "user-1")

    assert result["reason"] == "cron_api_failed"
    assert 2 in svc._sandbox_down_until


@pytest.mark.asyncio
async def test_second_fetch_within_ttl_skips_transport():
    invoke = AsyncMock(
        return_value={"success": True, "data": []}
    )
    svc = _make_service(transport_invoke=invoke)
    svc._sandbox_down_until[7] = (
        cron_runtime_targets.time.monotonic()
        + cron_runtime_targets.SANDBOX_DESTROYED_TTL_SECONDS
    )

    result = await svc._fetch_runtime_target_crons(_target(7), "user-1")

    assert result["success"] is False
    assert result["reason"] == "sandbox_destroyed_cached"
    invoke.assert_not_awaited()
    # prepare (device query + resolution) is skipped too — that is the win.
    svc._prepare_runtime_query_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_verdict_expires_after_ttl(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(cron_runtime_targets, "time", clock)
    invoke = AsyncMock(
        return_value={"success": False, "error": _DESTROYED_MSG}
    )
    svc = _make_service(transport_invoke=invoke)

    await svc._fetch_runtime_target_crons(_target(3), "user-1")
    clock.now += cron_runtime_targets.SANDBOX_DESTROYED_TTL_SECONDS + 1

    await svc._fetch_runtime_target_crons(_target(3), "user-1")

    assert invoke.await_count == 2


@pytest.mark.asyncio
async def test_plain_failures_are_never_recorded():
    invoke = AsyncMock(
        return_value={"success": False, "error": "Adapter returned HTTP 500"}
    )
    svc = _make_service(transport_invoke=invoke)

    await svc._fetch_runtime_target_crons(_target(4), "user-1")
    await svc._fetch_runtime_target_crons(_target(4), "user-1")

    assert invoke.await_count == 2
    assert not svc._sandbox_down_until


@pytest.mark.asyncio
async def test_list_all_crons_surfaces_skip_as_failed_target():
    invoke = AsyncMock(
        return_value={"success": False, "error": _DESTROYED_MSG}
    )
    svc = _make_service(transport_invoke=invoke)
    bot_provider = svc._bot_provider
    bot_provider.list_bots_by_owner_or_collaborator.return_value = {
        "total": 1,
        "items": [
            {
                "bot_id": "bot-1",
                "bot_name": "bot-1",
                "owner_id": "user-1",
                "bot_type": "service",
                "binding_id": 1,
            }
        ],
    }
    svc._build_runtime_targets = MagicMock(return_value=([_target(1)], []))

    first = await svc.list_all_crons(user_id="user-1", nick_name="user-1")
    second = await svc.list_all_crons(user_id="user-1", nick_name="user-1")

    assert invoke.await_count == 1
    reasons_first = [f["reason"] for f in first["failed_targets"]]
    reasons_second = [f["reason"] for f in second["failed_targets"]]
    assert reasons_first == ["cron_api_failed"]
    assert reasons_second == ["sandbox_destroyed_cached"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/community/core/cron/services/test_cron_relay_sandbox_skip.py -v
```

Expected: FAIL — `AttributeError: 'CronRelayService' object has no attribute '_sandbox_down_until'` and `No Attribute SANDBOX_DESTROYED_TTL_SECONDS` on the module.

- [ ] **Step 3: Implement the negative cache**

3a. In `cron_runtime_targets.py`:

Add `import time` to the imports (after `import asyncio`).

Add after `RUNTIME_QUERY_PREPARE_CONCURRENCY = 8`:

```python
#: How long a binding's "sandbox destroyed" verdict suppresses its queries.
#: ARCA/BaaS instances are reclaimed while the binding row stays ACTIVE, and a
#: listing fan-out would otherwise replay a guaranteed 404 per destroyed
#: instance on every request (pre 2026-08-28: 6 dead sandboxes of one bot per
#: request). The window is short so a recreated sandbox becomes visible again
#: within roughly one console polling cycle.
SANDBOX_DESTROYED_TTL_SECONDS = 60.0

#: Substrings that identify the destroyed-sandbox verdict in adapter error
#: payloads (both spellings observed in pre logs, message is matched
#: case-insensitively).
_SANDBOX_DESTROYED_MARKERS = ("沙箱已销毁", "sandbox destroyed")


def _mentions_destroyed_sandbox(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _SANDBOX_DESTROYED_MARKERS)
```

Add two mixin methods (place next to `_prepare_runtime_query_async`, before `_fetch_runtime_target_crons`):

```python
    # ── 沙箱已销毁负缓存 ─────────────────────────────────────────

    def _sandbox_down_deadline(self, binding_id: int) -> float | None:
        """Return the live skip-deadline for a binding, else ``None``.

        Expired entries are dropped here (lazy TTL) — the map only ever
        holds bindings that failed recently, so it stays tiny.
        """
        deadline = self._sandbox_down_until.get(binding_id)
        if deadline is None:
            return None
        if deadline <= time.monotonic():
            del self._sandbox_down_until[binding_id]
            return None
        return deadline

    def _mark_sandbox_down(self, binding_id: int) -> None:
        self._sandbox_down_until[binding_id] = (
            time.monotonic() + SANDBOX_DESTROYED_TTL_SECONDS
        )
```

Extend `_fetch_runtime_target_crons` (line ~310). Insert at the top of the body, before `ctx, failure = await self._prepare_runtime_query_async(target)`:

```python
        deadline = self._sandbox_down_deadline(target.binding_id)
        if deadline is not None:
            logger.info(
                "[_fetch_runtime_target_crons] Skip bot=%s stage=%s "
                "binding=%s: sandbox-destroyed verdict cached (%.0fs left)",
                target.bot_id,
                target.runtime_stage,
                target.binding_id,
                deadline - time.monotonic(),
            )
            return {
                "success": False,
                "reason": "sandbox_destroyed_cached",
                "error": (
                    f"skipped: binding {target.binding_id} failed with a "
                    f"sandbox-destroyed verdict within the last "
                    f"{SANDBOX_DESTROYED_TTL_SECONDS:g}s"
                ),
            }
```

In the same method's failure paths, record the verdict. The exception branch becomes:

```python
        except Exception as e:
            logger.error(
                "[_fetch_runtime_target_crons] Adapter request failed for "
                "bot=%s stage=%s: %s",
                target.bot_id,
                target.runtime_stage,
                e,
            )
            if _mentions_destroyed_sandbox(str(e)):
                self._mark_sandbox_down(target.binding_id)
            return {"success": False, "reason": "cron_api_failed", "error": str(e)}
```

And the non-success branch after `_invoke_transport`:

```python
        if not result.get("success", True):
            error_text = str(
                result.get("message") or result.get("error") or "cron api failed"
            )
            if _mentions_destroyed_sandbox(error_text):
                self._mark_sandbox_down(target.binding_id)
            return {
                "success": False,
                "reason": "cron_api_failed",
                "error": error_text,
            }
```

3b. In `cron_relay.py` `__init__`, after the `_runtime_query_prepare_semaphore` assignment (line ~93):

```python
        # 沙箱已销毁负缓存：binding 级"实例已被回收"判决，TTL 内跳过该
        # binding 的查询与转发（方法定义在 CronRuntimeTargetMixin）。
        # 实例被重建后最多一个 TTL 窗口内列表仍报该 binding 失败。
        self._sandbox_down_until: dict[int, float] = {}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/community/core/cron -v
```

Expected: all cron suites PASS (including the pre-existing files: `test_cron_relay_list_stage_filter.py`, `test_cron_relay_uses_resolver.py`, `test_cron_relay_find_auto_initiate.py`).

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/cron/services/cron_runtime_targets.py src/agentclaw/community/core/cron/services/cron_relay.py ../../../tests/community/core/cron/services/test_cron_relay_sandbox_skip.py
git commit -m "perf(backend): skip bindings with a cached sandbox-destroyed verdict in cron fan-out"
```

(From the repo root: `git add src/backend/...` with full paths.)

---

### Task 6: Full suite + changed-line coverage gate

**Files:** none (verification only)

- [ ] **Step 1: Run the backend CI gate locally**

Per repo convention the changed-line coverage gate only reproduces locally via `ci_test.sh`:

```bash
cd /Users/rongzhi/PycharmProjects/Avernet/src/backend
BACKEND_CI_PYTEST_WORKERS=4 scripts/ci_test.sh --base origin/REL20260828
```

Expected: pytest passes, coverage gate passes (≥75 line / changed-line thresholds from `report_check.py`).

- [ ] **Step 2: Fix any fallout**

If unrelated pre-existing tests fail, verify they also fail on clean `origin/REL20260828` before touching anything (`git stash && scripts/ci_test.sh ... && git stash pop`). Only fix fallout caused by these changes:

- Uncached-behaviour assumptions in `tests/community/core/grt_chat`, `tests/community/core/devices`, or singlebox flows → the cache is TTL-30-s; tests that call ws/http-info repeatedly with the same params and expect per-call HTTP counts need either distinct params, `force_refresh=True`, or a fresh `BaasService` per case (they already construct their own).

- [ ] **Step 3: Commit any fallout fixes**

```bash
git commit -am "test(backend): adapt conn-info caching consumers to the 30s TTL"
```

---

### Task 7: Validation summary (no push)

- [ ] **Step 1: Collect the evidence**

```bash
git log --oneline origin/REL20260828..HEAD
git diff origin/REL20260828..HEAD --stat
```

- [ ] **Step 2: Do NOT push or open a PR** — report back with the locally-built summary including the measured expected effect (per the 2026-08-28 trace: ~5-6 secbaas round trips + 6 destroyed-sandbox 404 chains per listing request are the dominant removable latency; target is bringing the 2.8 s pre worst case under ~1 s) and propose deploying to pre before measuring again via AntLogs (`list_all_crons` + access RT for `/openapi/v1/bots/routines/all`, the same queries this plan was diagnosed with).

---

## Self-Review notes

- **Spec coverage:** P0-1 (Tasks 2–4: http-info cache, retry self-heal, ws-info cache), P0-2 (Task 5). P1 lean-bot-list / batched publish queries are explicitly deferred to a separate plan.
- **Type consistency:** cache tuple type is `(float, dataclass)` in both caches; `_conn_cache_put` is generic over it; `force_refresh` keyword is identical in `get_http_info` / `get_ws_info_by_bot_uuid`.
- **No placeholders:** every step carries the full code or exact command.
- **Known accepted risk (documented in-code):** a corp-profile `HttpDeviceAdapterTransport` live out of this repo also consumes `get_http_info`; if it has its own 401-retry it will now read the 30 s cache. Token lifetimes exceed the TTL (in-repo validated precedent), bounding the risk window; the guerrilla fix for that consumer would be `force_refresh` plumbing on its side.
