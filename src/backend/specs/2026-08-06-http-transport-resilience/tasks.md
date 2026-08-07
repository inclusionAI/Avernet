# Tasks — Backend HTTP Transport Resilience

Implements `plan.md`. Five groups. Groups A–C are independent of D; E is the
final gate. Every task lists the file it touches and how it is verified.

Paths are relative to `src/backend/`.

---

## Group A — The reusable retry component

- [x] **A1. Create `src/agentclaw/community/utils/retry.py`.**
  Module docstring explains why classification is symptom-based (the
  `sofa_tracer` send-hook wrapper re-types transport errors and hides the cause
  on `__cause__`) and why retry is opt-in per call site rather than in the
  transport.

- [x] **A2. Implement `client_error_status(exc) -> int | None`.**
  Reads `exc.response.status_code`; returns it when it is an `int` in `[400, 500)`,
  else `None`. Document that `None` is a meaningful answer, not a missing one.

- [x] **A3. Implement `is_transport_failure(exc) -> bool`** as
  `client_error_status(exc) is None`.

- [x] **A4. Implement `describe_exception(exc) -> str`.**
  `"Type: msg"`, plus `" | caused by Type: msg"` when `__cause__` or
  `__context__` is set and is not `exc` itself, plus `" | request=<url>"` when
  `exc.request.url` exists.

- [x] **A5. Implement `retry_transport_call(call, *, operation, attempts=DEFAULT_ATTEMPTS, backoff_seconds=DEFAULT_BACKOFF_SECONDS)`.**
  Raise `ValueError` when `attempts < 1`. Loop: return `call()` on success; on
  exception, bare-`raise` when attempts are exhausted or the failure is not a
  transport failure (logging ERROR with elapsed ms + `describe_exception`);
  otherwise log WARNING and `time.sleep` a jittered backoff before retrying.
  Define `DEFAULT_ATTEMPTS = 2`, `DEFAULT_BACKOFF_SECONDS = 0.1`, and a module-
  private jitter fraction. Export the public names via `__all__`.

- [x] **A6. Write `tests/community/utils/test_retry.py`** covering every bullet
  in plan.md § _Test plan → New — test_retry.py_. Patch `time.sleep` so the
  suite does not actually wait.

- [x] **A7. Run** `tests/community/utils/test_retry.py` → green.

---

## Group B — Deduplicate the helpers in `llm.py`

- [x] **B1. Alias the private helpers.** In
  `src/agentclaw/community/core/harness/services/llm.py`, import
  `client_error_status` / `describe_exception` from `utils.retry` and replace the
  two function bodies with `_client_error_status = client_error_status` and
  `_exc_detail = describe_exception`. Leave a comment saying why the private
  names survive (module-attribute test access + LLM-local retry policy). Do not
  touch `_request_with_retry`, `_retry_delay`, or any LLM retry constant.

- [x] **B2. Run `test_llm_helpers.py` and `test_llm_request_retry.py`
  unmodified** → green. These passing without edits is the proof the extraction
  is behavior-preserving; if either needs a change, the extraction is wrong.

---

## Group C — Adopt the component in `get_http_info`

- [x] **C1. Wrap the transport call.** In
  `src/agentclaw/community/core/service_bot/services/baas_service.py`,
  `get_http_info`: replace the bare `self._http.get(...)` with
  `retry_transport_call(lambda: self._http.get(...), operation="BaasService.get_http_info")`.
  `raise_for_status()`, `.json()`, the `code != 0` check and the
  `HttpConnectionInfo` construction all stay **outside** the thunk and unchanged.
  Do not change the `timeout` parameter or its `5.0` default.

- [x] **C2. Make the failure log actionable.** In the trailing
  `except Exception as e` of `get_http_info`, format with `describe_exception(e)`
  and raise `BaasServiceError(...) from e`. Leave the `httpx.HTTPStatusError` and
  `BaasServiceError` handlers alone.

- [x] **C3. Write `tests/community/core/service_bot/services/test_baas_service_get_http_info_retry.py`**
  covering the four cases in plan.md § _Test plan_. Stub `HttpClient` and
  `DeviceBindingRepository`; assert call counts, not just outcomes.

- [x] **C4. Run the new test plus the existing `baas_service` suite** → green.

---

## Group D — Connection reuse in `HttpxClient`

- [x] **D1. Hold one client.** In `src/agentclaw/community/plugins/http_client.py`,
  build `httpx.Client(base_url=base_url)` eagerly in `__init__` and store it.
  Update the module docstring: it currently states each call opens a short-lived
  client, which will no longer be true.

- [x] **D2. Reuse it per request.** `_request` drops the `with httpx.Client(...)`
  block and calls `self._client.request(method, path, timeout=timeout, **kwargs)`.
  Keep the `None`-omitting kwargs assembly exactly as-is. No `close()`, no
  `limits=`.

- [x] **D3. Update `tests/community/plugins/test_http_client.py`.** The five
  existing tests assert `httpx.Client(base_url=..., timeout=<per-call>)`; move
  the timeout assertion to `request(...)` and construct `HttpxClient` inside the
  patch context. Update the module docstring, which describes the old per-call
  construction.

- [x] **D4. Add a reuse test:** two calls on one `HttpxClient` construct
  `httpx.Client` exactly once and issue two requests. This is the assertion that
  pins the actual fix.

- [x] **D5. Run** `tests/community/plugins/test_http_client.py` and the
  `tests/community/di/` HTTP-client module tests → green.

---

## Group E — Boundaries, full verification, delivery

- [x] **E1. Declare the new import edge.** Add
  `agentclaw.community.utils.retry` to `internal_dependencies` in
  `src/agentclaw/community/core/harness/README.md`, with a short trailing
  comment — it declares only `...utils.env_utils`, which does not cover the new
  module. `src/agentclaw/community/core/service_bot/README.md` needs **no
  change**: it already declares the bare `agentclaw.community.utils`, which the
  boundary test's prefix rule (`actual == d or actual.startswith(d + ".")`)
  already matches against `...utils.retry`.

- [x] **E2. Run the architecture suite** — at minimum
  `tests/community/architecture/test_module_boundaries.py` (baseline: 3 passed)
  plus `test_api_layer_is_protocols_only.py` and
  `test_core_no_concrete_plugin_imports.py`.

- [ ] **E3. Run the backend SAST/lint gate** (`scripts/ci/python_sast_local.sh`
  or the pre-push lint-only path) for the changed modules.

- [ ] **E4. Run the broader affected suites:** `tests/community/utils/`,
  `tests/community/plugins/`, `tests/community/core/service_bot/`,
  `tests/community/core/harness/`, `tests/community/di/`. Record what passed and
  state explicitly anything that could not run and why.

- [ ] **E5. Commit** to `claude/skill-upload-http-logs-ee0m9h` with a message
  following the AGENTS.md convention, and push with
  `git push -u origin claude/skill-upload-http-logs-ee0m9h`.

- [ ] **E6. Open a draft PR against `REL20260806`** titled
  `fix(backend): retry transport failures and reuse HTTP connections`, body
  filled from `.github/pull_request_template.md` (`Problem` / `Solution` /
  `Validation`, plus `Compatibility and risk`). Then subscribe to its activity.

---

## Definition of done

- One retry component under `utils/`, importable by any backend module, with no
  coupling to BaaS or skill upload.
- `get_http_info` survives a single transport blip; status handling unchanged.
- `HttpxClient` opens one connection pool per client instead of one per request.
- `llm.py` has no duplicate copy of the classifier or the cause formatter, and
  its tests pass unmodified.
- Architecture boundary tests green; new import edges declared.
- Draft PR open against `REL20260806`.
