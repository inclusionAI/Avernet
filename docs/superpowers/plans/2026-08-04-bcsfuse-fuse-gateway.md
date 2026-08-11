# bcsfuse fuse Gateway Exposure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose bcsfuse's 4 fusion/fusable-query endpoints through the Avernet gateway (`/openapi/v1/bcsfuse/...`) following the #712 BCN template, with lightest-touch auth (bcsfuse trusts the gateway).

**Architecture:** The gateway is a config-driven forwarding plane — adding a `bcsfuse` domain with a strip rewrite (`to: /`) covers both upstream prefixes (`/api/v1` fusion, `/v1` worker-config) with zero new gateway handlers and no bcsfuse route changes. bcsfuse gets three small non-route changes: a trust-gateway auth bypass, a tolerated `session_id` request field, and a `run_in_threadpool` wrap so gateway concurrency does not block the event loop. Spec: `docs/superpowers/specs/2026-08-04-bcsfuse-fuse-gateway-design.md`.

**Tech Stack:** Python 3, FastAPI, pydantic v2 (bcsfuse); YAML config + pytest (gateway); the gateway's hexagonal `core/forwarding/_domains.py` `PathRewrite` supports `to: /` strip (`tests/test_domain_map.py:324`).

## Global Constraints

- **Typing (AGENTS.md/CLAUDE.md):** required values stay non-optional; use `Optional[str]` (not `T | None`) only when `None` is an intentional state. Match the existing `from typing import Optional` style in `fusion_schemas.py`.
- **Gateway arch tests** (`src/gateway/tests/architecture/`) enforce hexagonal layer bans; pure config/test changes must keep them green — do not add `core`/`adapter` imports in tests beyond what existing tests use.
- **No bcsfuse route prefix changes:** the 4 endpoints stay where they are (`/api/v1` and `/v1`); the gateway strip-rewrite maps `/openapi/v1/bcsfuse/<rest>` → `/<rest>`.
- **Pre-push:** hook runs lint-only by default; `OCB_PRE_PUSH_RUN_CI=1` runs full module gates; merge target `origin/dev` (or `AVERNET_PRE_PUSH_MERGE_TARGET`).
- **PR conventions:** title `feat(gateway): <outcome>`, body `## Problem / ## Solution / ## Validation`.
- **External surface (4 endpoints), verbatim upstream paths after strip:**
  - `POST /openapi/v1/bcsfuse/api/v1/groups/{group_id}/fuse` → `/api/v1/groups/{group_id}/fuse`
  - `GET  /openapi/v1/bcsfuse/v1/workers/{worker_id}/config` → `/v1/workers/{worker_id}/config`
  - `PUT  /openapi/v1/bcsfuse/v1/workers/{worker_id}/config` → `/v1/workers/{worker_id}/config`
  - `POST /openapi/v1/bcsfuse/v1/workers/config/batch` → `/v1/workers/config/batch`

---

## File Structure

**Gateway (Avernet `src/gateway/`):**
- Modify `configs/application.yaml` — add `bcsfuse_server_url`, `bcsfuse` server, `bcsfuse` domain (strip rewrite), `route_security` rule.
- Create `configs/schemas/bcsfuse.openapi.json` — published doc, 4 paths in-namespace.
- Modify `configs/schemas/README.md` — document the new schema.
- Modify `tests/test_domain_map.py` — add `_VARS` entry + shipped-config strip-rewrite test.
- Create `tests/fixtures/bcsfuse.openapi.json` — small fixture for served-openapi test.
- Modify `tests/test_served_openapi.py` — assert the 4 paths serve with `x-avernet-security: {user: required}`.

**bcsfuse (Avernet `src/bcsfuse/`):**
- Modify `src/bootstrap/oss_business_routes.py` — add `_trust_gateway_enabled()` + early-return in `require_oss_auth`.
- Modify `src/interfaces/api/schemas/fusion_schemas.py` — add tolerated `session_id` field to `FusionRequest`.
- Modify `src/interfaces/api/fusion_parity_routes.py` — strip `session_id` before domain conversion; wrap `service.fuse()` in `_run_fuse` (threadpool).
- Create `tests/unit/bootstrap/test_trust_gateway_auth.py`.
- Create `tests/unit/interfaces/test_fusion_schemas_session_id.py`.
- Create `tests/unit/interfaces/test_run_fuse_threadpool.py`.

**ocb (sibling repo, optional coordination):**
- Modify `ocb/src/frontend/src/services/backend-api/BcsfuseController.ts` — retarget base `/bcnfuse` → `/openapi/v1/bcsfuse`.

Each task below is independently testable and ends with a commit.

---

## Task 1: Gateway config — add `bcsfuse` domain with strip rewrite

**Files:**
- Modify: `src/gateway/configs/application.yaml`
- Modify: `src/gateway/tests/test_domain_map.py` (add `_VARS` entry + new test)

**Interfaces:**
- Consumes: `DomainMap.from_config`, `RouteSecurity.from_table`, `PrincipalType`, `Presence` (already imported in the test file).
- Produces: shipped config resolves `/openapi/v1/bcsfuse/...` to the `bcsfuse` server with a strip rewrite and `user: required`.

- [ ] **Step 1: Write the failing test**

Append to `src/gateway/tests/test_domain_map.py`, and add the var to `_VARS` (lines 15-20):

```python
_VARS = {
    "backend_server_url": "http://backend:8080",
    "baas_server_url": "http://baas:9090",
    "bcs_server_url": "http://bcs:8081",
    "engineProxy_server_url": "https://engineproxy:20003",
    "bcsfuse_server_url": "http://bcsfuse:8765",
}
```

Append this test (after `test_shipped_config_routes_collaboration_verbatim_to_bcs`, ~line 100):

```python
def test_shipped_config_routes_bcsfuse_via_strip_rewrite() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    dm = DomainMap.from_config(raw["user_config"]["upstreams"], variables=_VARS)

    fusion = dm.domain_for("/openapi/v1/bcsfuse/api/v1/groups/group-1")
    assert fusion is not None
    assert fusion.server.name == "bcsfuse"
    assert fusion.server.base_url == "http://bcsfuse:8765"
    assert fusion.serves_http
    assert not fusion.serves_websocket
    assert fusion.rewrite is not None
    # Strip rewrite drops the domain prefix; the upstream's own /api/v1 and /v1 stay.
    assert fusion.upstream_path(
        "/openapi/v1/bcsfuse/api/v1/groups/group-1"
    ) == "/api/v1/groups/group-1"
    assert fusion.upstream_path(
        "/openapi/v1/bcsfuse/v1/workers/w-1/config"
    ) == "/v1/workers/w-1/config"
    assert fusion.upstream_path(
        "/openapi/v1/bcsfuse/v1/workers/config/batch"
    ) == "/v1/workers/config/batch"
    assert fusion.schema.location == "schemas/bcsfuse.openapi.json"

    security = RouteSecurity.from_table(raw["user_config"]["route_security"])
    requirement = security.resolve("POST", "/openapi/v1/bcsfuse/api/v1/groups/group-1")
    assert requirement is not None
    assert requirement[PrincipalType.USER] is Presence.REQUIRED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/gateway && uv run pytest tests/test_domain_map.py::test_shipped_config_routes_bcsfuse_via_strip_rewrite -v`
Expected: FAIL — `domain_for(...)` returns `None` (no `bcsfuse` domain in shipped config) → `assert fusion is not None` fails.

- [ ] **Step 3: Add the config**

Edit `src/gateway/configs/application.yaml`:

Under `upstream_vars:` (after line 79 `engineProxy_server_url: https://engineproxy.sample.com`), add:

```yaml
    bcsfuse_server_url: https://bcsfuse.sample.com
```

Under `route_security:` (after the `/openapi/v1/collaboration/**` block, ~line 108), add:

```yaml
    "/openapi/v1/bcsfuse/**":
      user: required
```

Under `upstreams.domains:` (after the `collaboration` block, ~line 181), add:

```yaml
      bcsfuse:
        server: bcsfuse
        protocols: [http]
        # bcsfuse serves fusion under /api/v1 and worker-config under /v1.
        # Strip the gateway prefix so both upstream prefixes pass through unchanged.
        rewrite:
          from: /openapi/v1/bcsfuse
          to: /
        schema:
          source: file
          path: schemas/bcsfuse.openapi.json
          refresh_seconds: 300
```

Under `upstreams.servers:` (after the `bcs` block, ~line 233), add:

```yaml
      bcsfuse:
        base_url: "${bcsfuse_server_url}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/gateway && uv run pytest tests/test_domain_map.py::test_shipped_config_routes_bcsfuse_via_strip_rewrite -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/configs/application.yaml src/gateway/tests/test_domain_map.py
git commit -m "feat(gateway): add bcsfuse domain with strip rewrite"
```

---

## Task 2: bcsfuse OpenAPI artifact + served-openapi test

**Files:**
- Create: `src/gateway/configs/schemas/bcsfuse.openapi.json`
- Create: `src/gateway/tests/fixtures/bcsfuse.openapi.json`
- Modify: `src/gateway/tests/test_served_openapi.py`
- Modify: `src/gateway/configs/schemas/README.md`

**Interfaces:**
- Consumes: `build_served_openapi`, `_SHIPPED_RULES`, `_METHODS` (already in `test_served_openapi.py`).
- Produces: a committed in-namespace artifact the gateway serves for the `bcsfuse` domain.

- [ ] **Step 1: Write the failing test**

Append to `src/gateway/tests/test_served_openapi.py` (the file already defines `_METHODS`, `_SHIPPED_RULES`, `build_served_openapi`, `json`, `Path`):

```python
_BCSFUSE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bcsfuse.openapi.json"


def _bcsfuse_served() -> dict[str, Any]:
    description = json.loads(_BCSFUSE_FIXTURE.read_text())
    return build_served_openapi(
        ["bcsfuse"],
        lambda _domain: description,
        _SHIPPED_RULES,
        title="gateway",
        version="0.1.0",
        description="test",
    )


def test_bcsfuse_paths_served_with_user_security() -> None:
    paths = _bcsfuse_served()["paths"]
    assert set(paths) == {
        "/openapi/v1/bcsfuse/api/v1/groups/{group_id}/fuse",
        "/openapi/v1/bcsfuse/v1/workers/{worker_id}/config",
        "/openapi/v1/bcsfuse/v1/workers/config/batch",
    }
    for path, item in paths.items():
        for method, operation in item.items():
            if method in _METHODS:
                assert operation["x-avernet-security"] == {"user": "required"}, (
                    f"{method} {path}"
                )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/gateway && uv run pytest tests/test_served_openapi.py::test_bcsfuse_paths_served_with_user_security -v`
Expected: FAIL — `fixtures/bcsfuse.openapi.json` does not exist (`FileNotFoundError`).

- [ ] **Step 3: Create the fixture**

`src/gateway/tests/fixtures/bcsfuse.openapi.json`:

```json
{
  "openapi": "3.1.0",
  "info": {"title": "bcsfuse", "version": "0.1.0"},
  "paths": {
    "/openapi/v1/bcsfuse/api/v1/groups/{group_id}/fuse": {
      "post": {"summary": "Fuse group participants", "responses": {"200": {"description": "Fusion result"}}}
    },
    "/openapi/v1/bcsfuse/v1/workers/{worker_id}/config": {
      "get": {"summary": "Get worker fusion config", "responses": {"200": {"description": "Worker config"}}},
      "put": {"summary": "Update worker fusion config", "responses": {"200": {"description": "Worker config"}}}
    },
    "/openapi/v1/bcsfuse/v1/workers/config/batch": {
      "post": {"summary": "Batch query fusion_enable (fusable bots)", "responses": {"200": {"description": "Batch config"}}}
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/gateway && uv run pytest tests/test_served_openapi.py::test_bcsfuse_paths_served_with_user_security -v`
Expected: PASS.

- [ ] **Step 5: Create the published artifact**

`src/gateway/configs/schemas/bcsfuse.openapi.json` — same content as the fixture above (this is the file the gateway's schema-catalog reads at runtime to serve `/openapi.json`). Copy the fixture verbatim.

- [ ] **Step 6: Document the schema**

In `src/gateway/configs/schemas/README.md`, add a line for `bcsfuse.openapi.json` mirroring the existing entries for `bots`/`bcn`/`baas` (e.g. `- \`bcsfuse.openapi.json\` — bcsfuse fusion + fusable-query endpoints exposed under \`/openapi/v1/bcsfuse\`.`).

- [ ] **Step 7: Commit**

```bash
git add src/gateway/configs/schemas/bcsfuse.openapi.json src/gateway/configs/schemas/README.md src/gateway/tests/fixtures/bcsfuse.openapi.json src/gateway/tests/test_served_openapi.py
git commit -m "feat(gateway): publish bcsfuse openapi artifact + served test"
```

---

## Task 3: bcsfuse trust-gateway auth bypass (D2, lightest)

**Files:**
- Modify: `src/bcsfuse/src/bootstrap/oss_business_routes.py`
- Create: `src/bcsfuse/tests/unit/bootstrap/test_trust_gateway_auth.py`

**Interfaces:**
- Consumes: `require_oss_auth(request)` (called by `_require_auth` in `fusion_parity_routes.py:65-68`, which guards the fusion route).
- Produces: `_trust_gateway_enabled() -> bool`; when `BCSFUSE_TRUST_GATEWAY=true`, `require_oss_auth` returns immediately (gateway `user:required` is the trust boundary; bcsfuse does not verify `X-Avernet-Principal`).

- [ ] **Step 1: Write the failing test**

`src/bcsfuse/tests/unit/bootstrap/test_trust_gateway_auth.py`:

```python
import pytest

from src.bootstrap.oss_business_routes import _trust_gateway_enabled


def test_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BCSFUSE_TRUST_GATEWAY", raising=False)
    assert _trust_gateway_enabled() is False


def test_enabled_when_env_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "true")
    assert _trust_gateway_enabled() is True


def test_disabled_for_other_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "0")
    assert _trust_gateway_enabled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/bcsfuse && uv run pytest tests/unit/bootstrap/test_trust_gateway_auth.py -v`
Expected: FAIL — `ImportError: cannot import name '_trust_gateway_enabled'`.

- [ ] **Step 3: Implement**

In `src/bcsfuse/src/bootstrap/oss_business_routes.py`, ensure `import os` is present at the top (add it if missing). Add the helper just above `require_oss_auth` (line ~340):

```python
def _trust_gateway_enabled() -> bool:
    """True when bcsfuse sits behind the Avernet gateway and trusts its auth.

    The gateway authenticates the caller (route_security user:required) and
    forwards the request; bcsfuse then skips its own shared-Bearer check.
    D2 lightest: bcsfuse does NOT verify X-Avernet-Principal.
    """
    return os.environ.get("BCSFUSE_TRUST_GATEWAY", "").lower() == "true"
```

Add the early return as the first line inside `require_oss_auth` (before `registry = _get_provider_registry(request)`):

```python
    if _trust_gateway_enabled():
        return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/bcsfuse && uv run pytest tests/unit/bootstrap/test_trust_gateway_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Run existing auth-regression smoke to confirm no breakage**

Run: `cd src/bcsfuse && uv run pytest tests/smoke/auth_regression.py -v` (if the path exists; otherwise `cd src/bcsfuse && uv run pytest tests/smoke -v`)
Expected: PASS (env unset in tests → existing behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/bcsfuse/src/bootstrap/oss_business_routes.py src/bcsfuse/tests/unit/bootstrap/test_trust_gateway_auth.py
git commit -m "feat(bcsfuse): add trust-gateway auth bypass"
```

---

## Task 4: Tolerate caller-supplied `session_id` (Q1=A)

**Files:**
- Modify: `src/bcsfuse/src/interfaces/api/schemas/fusion_schemas.py` (add field to `FusionRequest`)
- Modify: `src/bcsfuse/src/interfaces/api/fusion_parity_routes.py` (strip before domain conversion)
- Create: `src/bcsfuse/tests/unit/interfaces/test_fusion_schemas_session_id.py`

**Interfaces:**
- Consumes: `FusionRequest` (API schema, `fusion_schemas.py:151`); domain `FusionRequest` (`src.domain.models.fusion_request`, `extra:"forbid"`, no `session_id`).
- Produces: `FusionRequest.session_id: Optional[str]` (accepted, not used — G9 scopes context by path `group_id`).

- [ ] **Step 1: Write the failing test**

`src/bcsfuse/tests/unit/interfaces/test_fusion_schemas_session_id.py`:

```python
import pytest

from src.interfaces.api.schemas.fusion_schemas import FusionRequest


def test_accepts_session_id() -> None:
    req = FusionRequest(question="q", participants=["a"], session_id="sess-1")
    assert req.session_id == "sess-1"


def test_session_id_defaults_none() -> None:
    req = FusionRequest(question="q", participants=["a"])
    assert req.session_id is None


def test_unknown_fields_still_forbidden() -> None:
    with pytest.raises(Exception):
        FusionRequest(question="q", participants=["a"], surprise="x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/bcsfuse && uv run pytest tests/unit/interfaces/test_fusion_schemas_session_id.py -v`
Expected: FAIL — `test_accepts_session_id` raises `ValidationError` (unexpected field `session_id`, `extra="forbid"`).

- [ ] **Step 3: Add the field**

In `src/bcsfuse/src/interfaces/api/schemas/fusion_schemas.py`, inside `class FusionRequest` (after the `fusion_mode` field, before `options`, ~line 194):

```python
    session_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Session identifier (accepted for caller compatibility; not "
        "used — Avernet G9 scopes context by the path group_id).",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/bcsfuse && uv run pytest tests/unit/interfaces/test_fusion_schemas_session_id.py -v`
Expected: PASS.

- [ ] **Step 5: Strip `session_id` before domain conversion**

In `src/bcsfuse/src/interfaces/api/fusion_parity_routes.py`, the conversion block (~lines 319-323) currently:

```python
                req_dict = req.model_dump()
                logger.info(f"[Fusion][R5] req_dict fusion_mode: {req_dict.get('fusion_mode')}")
                logger.info(f"[Fusion][R5] req_dict options: {req_dict.get('options', {})}")

                real_request = RealFusionRequest(**req_dict)
```

Insert a pop before `real_request = RealFusionRequest(**req_dict)` so the domain model (which is `extra="forbid"` and has no `session_id`) does not reject it:

```python
                req_dict = req.model_dump()
                logger.info(f"[Fusion][R5] req_dict fusion_mode: {req_dict.get('fusion_mode')}")
                logger.info(f"[Fusion][R5] req_dict options: {req_dict.get('options', {})}")

                # Q1: tolerate caller-supplied session_id (not used by Avernet G9,
                # which scopes context by group_id). Strip before domain conversion
                # so the domain FusionRequest (extra=forbid) does not reject it.
                req_dict.pop("session_id", None)

                real_request = RealFusionRequest(**req_dict)
```

- [ ] **Step 6: Run bcsfuse fusion tests to confirm no regression**

Run: `cd src/bcsfuse && uv run pytest tests/unit/application/test_group_fusion_service.py tests/integration/test_group_fusion_flow.py -v` (skip integration if it requires LLM env; at minimum the unit test must pass)
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bcsfuse/src/interfaces/api/schemas/fusion_schemas.py src/bcsfuse/src/interfaces/api/fusion_parity_routes.py src/bcsfuse/tests/unit/interfaces/test_fusion_schemas_session_id.py
git commit -m "feat(bcsfuse): tolerate caller-supplied session_id"
```

---

## Task 5: Offload `service.fuse()` to a threadpool (D3 hardening)

**Files:**
- Modify: `src/bcsfuse/src/interfaces/api/fusion_parity_routes.py` (add `_run_fuse`, replace call site)
- Create: `src/bcsfuse/tests/unit/interfaces/test_run_fuse_threadpool.py`

**Interfaces:**
- Consumes: `GroupFusionService.fuse(request, group_id)` (synchronous; blocks the event loop when called from the async R5 handler).
- Produces: `async def _run_fuse(service, request, group_id)` — awaits `run_in_threadpool(service.fuse, request, group_id=group_id)`.

- [ ] **Step 1: Write the failing test**

`src/bcsfuse/tests/unit/interfaces/test_run_fuse_threadpool.py`:

```python
import asyncio
import threading

from src.interfaces.api.fusion_parity_routes import _run_fuse


class _StubService:
    def __init__(self) -> None:
        self.called_thread = None

    def fuse(self, request, group_id=None):
        self.called_thread = threading.get_ident()
        return "fused"


def test_run_fuse_offloads_to_a_worker_thread() -> None:
    async def main() -> None:
        loop_thread = threading.get_ident()
        service = _StubService()
        result = await _run_fuse(service, None, group_id="g-1")
        assert result == "fused"
        assert service.called_thread is not None
        assert service.called_thread != loop_thread

    asyncio.run(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/bcsfuse && uv run pytest tests/unit/interfaces/test_run_fuse_threadpool.py -v`
Expected: FAIL — `ImportError: cannot import name '_run_fuse'`.

- [ ] **Step 3: Add `_run_fuse` and use it**

In `src/bcsfuse/src/interfaces/api/fusion_parity_routes.py`, add the helper near the top of the module (after `logger = logging.getLogger(__name__)`, ~line 45):

```python
async def _run_fuse(service, request, group_id: str):
    """Run the synchronous GroupFusionService.fuse() off the event loop.

    The R5 fusion handler is async; calling service.fuse() inline blocks the
    loop for the full fusion duration (up to 600s). run_in_threadpool keeps the
    gateway's concurrent requests from serializing on it.
    """
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(service.fuse, request, group_id=group_id)
```

Replace the call site (~line 334):

```python
            result = service.fuse(real_request, group_id=group_id)
```

with:

```python
            result = await _run_fuse(service, real_request, group_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/bcsfuse && uv run pytest tests/unit/interfaces/test_run_fuse_threadpool.py -v`
Expected: PASS.

- [ ] **Step 5: Run fusion parity smoke/route inventory to confirm import integrity**

Run: `cd src/bcsfuse && uv run pytest tests/smoke/route_import_inventory.py -v` (if present; else `cd src/bcsfuse && python -c "from src.interfaces.api.fusion_parity_routes import fuse_group, _run_fuse"`)
Expected: PASS / no import error.

- [ ] **Step 6: Commit**

```bash
git add src/bcsfuse/src/interfaces/api/fusion_parity_routes.py src/bcsfuse/tests/unit/interfaces/test_run_fuse_threadpool.py
git commit -m "perf(bcsfuse): run service.fuse() in threadpool"
```

---

## Task 6: Final verification (gateway + bcsfuse green)

**Files:** none (verification only)

- [ ] **Step 1: Run the gateway module tests touched by this change**

Run: `cd src/gateway && uv run pytest tests/test_domain_map.py tests/test_served_openapi.py tests/test_route_security.py -v`
Expected: PASS (including the two new bcsfuse tests; `test_route_security` should still pass since the bcsfuse rule is `user: required`, consistent with `/**`).

- [ ] **Step 2: Run bcsfuse unit tests touched by this change**

Run: `cd src/bcsfuse && uv run pytest tests/unit/bootstrap/test_trust_gateway_auth.py tests/unit/interfaces/test_fusion_schemas_session_id.py tests/unit/interfaces/test_run_fuse_threadpool.py -v`
Expected: PASS.

- [ ] **Step 3: Run gateway architecture tests (layer rules) to confirm no violation**

Run: `cd src/gateway && uv run pytest tests/architecture -v` (or `just test-arch` from the gateway dir)
Expected: PASS (pure config + test changes; no new `core`/`adapter` imports).

- [ ] **Step 4 (optional): Sanity-check the served doc end-to-end (single-box)**

Boot the gateway locally (e.g. `cd src/gateway && uv run uvicorn gateway.community.adapters.web.app:app --port 8888`), then:

Run: `curl -s localhost:8888/openapi.json | python -c "import sys,json; d=json.load(sys.stdin); print(sorted(p for p in d['paths'] if '/bcsfuse/' in p))"`
Expected: the 4 `/openapi/v1/bcsfuse/...` paths.

Step 1 already covers the served paths via `test_served_openapi.py`, so this step is optional end-to-end confirmation.

- [ ] **Step 5: No commit (verification only) — or commit if any formatting/README cleanup was needed**

If all green and nothing else changed, no commit. If the README/artifact needed a touch-up, commit with `docs(gateway): finalize bcsfuse gateway exposure`.

---

## Task 7 (cross-repo, optional coordination): ocb frontend base retarget

> This task is in the **sibling `ocb` repo** (`/Users/wenyang/proj/alpharisk/ocb`), not Avernet. It is coordination work for the actual caller (ocb frontend). Do it only once the gateway is deployed at a reachable URL. It is optional with respect to the Avernet plan.

**Files:**
- Modify: `ocb/src/frontend/src/services/backend-api/BcsfuseController.ts`

**Interfaces:**
- Consumes: the 4 gateway paths from Global Constraints. The strip rewrite means the frontend keeps the exact `/api/v1/...` and `/v1/...` suffixes it already sends — only the base prefix changes.
- Produces: frontend calls the gateway instead of the direct `/bcnfuse` proxy.

- [ ] **Step 1: Retarget the base prefix**

In `BcsfuseController.ts`, the three functions (`postFuse`, `getWorkerConfig`, `updateWorkerConfig`) hard-code `/bcnfuse/...`. Replace the `/bcnfuse` prefix with `/openapi/v1/bcsfuse`, leaving the suffix unchanged. For example:

`/bcnfuse/api/v1/groups/${group_id}/fuse` → `/openapi/v1/bcsfuse/api/v1/groups/${group_id}/fuse`
`/bcnfuse/v1/workers/${worker_id}/config` → `/openapi/v1/bcsfuse/v1/workers/${worker_id}/config`

(Define a `BCSFUSE_BASE = '/openapi/v1/bcsfuse'` constant once and interpolate, to keep it DRY.)

- [ ] **Step 2: Verify cookies are sent to the gateway origin**

Ensure the request client sends credentials (`withCredentials` / Bigfish `request` credentials) so the user's BCN cookie reaches the gateway (which forwards it to bcsfuse for G9 context). Confirm the BCN cookie domain covers the gateway domain (deployment checklist, spec §9-Q5).

- [ ] **Step 3: Commit (in ocb repo)**

```bash
git add src/frontend/src/services/backend-api/BcsfuseController.ts
git commit -m "feat(frontend): route bcsfuse calls through the gateway"
```

---

## Notes / Out of scope (per spec §10, future work)

- **HTTP timeout (D3 operational prerequisite):** the gateway's HTTP forwarder and bcsfuse must allow fusion requests up to the max fusion timeout (600s; the ocb frontend sends `timeout_ms=180000`). Before going live, verify the gateway httpx forwarder's read/connect timeout is ≥600s (or effectively unlimited for streamed responses). If there is no config knob, add a follow-up task to expose one. This is an operational checklist item, not a code task in this plan.
- **OpenAPI dump automation** (`src/bcsfuse/scripts/dump_openapi.py` + `dump_and_publish.sh` registration): this plan uses a **static committed artifact** (`configs/schemas/bcsfuse.openapi.json`), the lighter alternative the spec explicitly allowed. The dump/gate pipeline can be added later when the contract stabilizes; the backward-compat gate is not active until then.
- **WS streaming fusion** (per-perspective events then final fusion), **G9 service-identity to BCN** (replacing cookie forwarding), and **response-envelope alignment** are explicit future items — not in this plan.
- **ocb frontend `fetchFusionBots`** switching from N×`getWorkerConfig` to the batch endpoint (`POST /v1/workers/config/batch`) is a separate frontend optimization.
