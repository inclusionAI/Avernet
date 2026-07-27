# Admin Run Callback Test Server Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a dependency-free local server that captures, inspects, validates, and acknowledges organization admin-run callbacks.

**Architecture:** Implement the utility as a Python standard-library `ThreadingHTTPServer` under `src/bcs/scripts/`. Keep mutable in-memory records in a lock-protected state object, keep HTTP translation in a request handler, and expose all behavior through CLI flags so developers can test success, authentication failures, non-2xx acknowledgements, and delayed responses without editing code.

**Tech Stack:** Python 3.12 standard library, `unittest`, `http.server`, `urllib.request`

---

### Task 1: Callback capture state

**Files:**
- Create: `src/bcs/scripts/admin_run_callback_server.py`
- Create: `src/bcs/scripts/test_admin_run_callback_server.py`

**Step 1: Write the failing state tests**

Add tests that construct `CallbackStore`, record completed and failed payloads,
and assert:

```python
first = store.record(headers, {"run_id": "run-1", "status": "completed"})
second = store.record(headers, {"run_id": "run-1", "status": "completed"})

self.assertFalse(first["duplicate"])
self.assertTrue(second["duplicate"])
self.assertEqual(store.snapshot()["duplicate_counts"]["run-1"], 1)
self.assertEqual(len(store.for_run("run-1")), 2)
self.assertEqual(first["headers"]["Authorization"], "<redacted>")
```

Add a reset assertion that the records and duplicate counts become empty.

**Step 2: Run the test to verify it fails**

Run:

```bash
cd src/bcs
python3 scripts/test_admin_run_callback_server.py
```

Expected: FAIL because `admin_run_callback_server` does not exist.

**Step 3: Implement the minimal state object**

Implement:

```python
class CallbackStore:
    def __init__(self) -> None:
        self._records: list[JsonObject] = []
        self._seen_counts: dict[str, int] = {}
        self._lock = threading.RLock()

    def record(self, headers: Mapping[str, str], body: JsonObject) -> JsonObject:
        ...

    def snapshot(self) -> JsonObject:
        ...

    def for_run(self, run_id: str) -> list[JsonObject]:
        ...

    def reset(self) -> None:
        ...
```

Store `received_at`, method, path, redacted headers, parsed body, `run_id`, and
the duplicate marker. Calculate duplicate counts from repeated run IDs.

**Step 4: Run the tests to verify they pass**

Run the same command.

Expected: all state tests PASS.

**Step 5: Commit**

```bash
git add src/bcs/scripts/admin_run_callback_server.py \
  src/bcs/scripts/test_admin_run_callback_server.py
git commit -m "test(bcs): define admin callback capture state"
```

### Task 2: Callback and inspection HTTP endpoints

**Files:**
- Modify: `src/bcs/scripts/admin_run_callback_server.py`
- Modify: `src/bcs/scripts/test_admin_run_callback_server.py`

**Step 1: Write failing endpoint tests**

Start the server on `127.0.0.1:0` in a background thread. Add tests for:

- `GET /health` returning `{"ok": true, "callback_count": 0}`;
- `POST /callback` recording a JSON callback and returning `200`;
- `GET /callbacks` returning the recorded callback;
- `GET /callbacks/run-1` returning only matching records;
- `POST /reset` clearing the store;
- unknown paths returning `404`;
- malformed JSON returning `400` without adding a valid callback.

Use `urllib.request` and shut down the server in test cleanup.

**Step 2: Run the tests to verify they fail**

Run:

```bash
cd src/bcs
python3 scripts/test_admin_run_callback_server.py
```

Expected: FAIL because the HTTP server factory and handler are missing.

**Step 3: Implement the minimal HTTP layer**

Add:

```python
@dataclass(frozen=True)
class ServerConfig:
    response_status: int = 200
    response_delay_ms: int = 0
    expected_token: str | None = None
    expected_provider_id: str | None = None


def create_server(
    host: str,
    port: int,
    config: ServerConfig,
    store: CallbackStore | None = None,
) -> ThreadingHTTPServer:
    ...
```

Implement `BaseHTTPRequestHandler` routing, JSON parsing, JSON responses, and
quiet default request logging. Print each accepted callback as indented JSON
with Authorization already redacted by the store.

**Step 4: Run the tests to verify they pass**

Run the same command.

Expected: endpoint tests PASS.

**Step 5: Commit**

```bash
git add src/bcs/scripts/admin_run_callback_server.py \
  src/bcs/scripts/test_admin_run_callback_server.py
git commit -m "feat(bcs): add local admin callback capture server"
```

### Task 3: Authentication and response simulation

**Files:**
- Modify: `src/bcs/scripts/admin_run_callback_server.py`
- Modify: `src/bcs/scripts/test_admin_run_callback_server.py`

**Step 1: Write failing behavior tests**

Add tests proving:

- wrong or missing expected Bearer token returns `401`;
- wrong or missing expected `X-BCN-Provider-Id` returns `403`;
- valid credentials permit callback recording;
- configured callback status such as `500` is returned after the request is
  recorded;
- configured delay applies to `/callback` but not `/health`.

Use a small delay such as 50 ms and assert a broad lower bound to avoid flaky
timing checks.

**Step 2: Run the tests to verify they fail**

Run the unit-test command.

Expected: FAIL on missing validation and response simulation behavior.

**Step 3: Implement validation and simulation**

Validate the exact header values before recording:

```python
authorization == f"Bearer {config.expected_token}"
provider_id == config.expected_provider_id
```

Return stable JSON error values:

```json
{"ok": false, "error": "invalid_token"}
{"ok": false, "error": "provider_id_mismatch"}
```

After a valid callback is recorded, sleep for `response_delay_ms / 1000` and
return the configured status with:

```json
{"ok": true, "recorded": true}
```

**Step 4: Run the tests to verify they pass**

Run the unit-test command.

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add src/bcs/scripts/admin_run_callback_server.py \
  src/bcs/scripts/test_admin_run_callback_server.py
git commit -m "feat(bcs): simulate admin callback acknowledgements"
```

### Task 4: CLI and developer usage

**Files:**
- Modify: `src/bcs/scripts/admin_run_callback_server.py`
- Modify: `src/bcs/README.md`
- Modify: `src/bcs/scripts/test_admin_run_callback_server.py`

**Step 1: Write failing CLI parser tests**

Test defaults and explicit arguments:

```text
--host
--port
--expected-token
--expected-provider-id
--response-status
--response-delay-ms
```

Reject response statuses outside `100..599`, negative delays, and ports outside
`0..65535`.

**Step 2: Run the tests to verify they fail**

Run the unit-test command.

Expected: FAIL because the parser does not expose the required arguments.

**Step 3: Implement CLI startup and documentation**

Build the parser in a testable `parse_args()` function. On startup print:

```text
Admin run callback test server listening on http://127.0.0.1:28081
Configure admin_callback_url as http://127.0.0.1:28081/callback
Inspect callbacks at http://127.0.0.1:28081/callbacks
```

Document the normal command, Provider callback URL, inspection commands,
credential-validation flags, and failure simulation in `src/bcs/README.md`.

**Step 4: Run focused verification**

Run:

```bash
cd src/bcs
python3 scripts/test_admin_run_callback_server.py
python3 scripts/admin_run_callback_server.py --help
```

Expected: tests PASS and help lists every supported option.

**Step 5: Run repository hygiene checks**

Run:

```bash
git diff --check
python3 -m py_compile \
  src/bcs/scripts/admin_run_callback_server.py \
  src/bcs/scripts/test_admin_run_callback_server.py
```

Expected: both commands succeed.

**Step 6: Commit**

```bash
git add src/bcs/scripts/admin_run_callback_server.py \
  src/bcs/scripts/test_admin_run_callback_server.py \
  src/bcs/README.md \
  docs/plans/2026-07-27-admin-run-callback-test-server.md
git commit -m "docs(bcs): explain admin callback test server"
```
