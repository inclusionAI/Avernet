# Gateway Streaming Request Forwarding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Forward Gateway request bodies to upstream services without materializing the complete payload in memory.

**Architecture:** Evolve the framework-neutral `Forwarder` Plugin API so an empty body is represented by `None` and a non-empty body is a closeable, one-shot asynchronous byte stream. The Starlette adapter authenticates and signs before reading the body, prefetches at most one ASGI chunk to distinguish an empty body from a stream, and hands ownership to the selected forwarder. `HttpxForwarder` preserves a declared `Content-Length`, lets HTTPX generate chunked framing when the length is unknown, and closes the request stream on success, failure, or cancellation.

**Tech Stack:** Python 3.12, FastAPI/Starlette ASGI, HTTPX, pytest, pytest-asyncio.

---

### Task 1: Specify the streaming request-body Plugin API

**Files:**
- Modify: `src/gateway/src/gateway/community/spi/forwarder/_models.py`
- Modify: `src/gateway/src/gateway/community/spi/forwarder/_protocols.py`
- Modify: `src/gateway/src/gateway/community/spi/forwarder/__init__.py`
- Test: `src/gateway/tests/contracts/spi/test_forwarder.py`

**Step 1: Write the failing contract tests**

Add tests that construct `ForwardRequest` with a closeable async body and establish these framing rules through the real HTTPX implementation:

```python
request = ForwardRequest(
    method="PUT",
    url="http://up/upload",
    headers={"content-length": "6"},
    body=_Body([b"abc", b"def"]),
)
```

- a declared length reaches the upstream unchanged and does not add `Transfer-Encoding`;
- an unknown-length stream reaches the upstream with chunked framing;
- `body=None` sends a compatible empty request;
- request-body ownership is one-shot and its `aclose()` method is called.

**Step 2: Run tests to verify RED**

Run:

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
  -u HTTPS_PROXY -u https_proxy uv run pytest -q \
  tests/contracts/spi/test_forwarder.py
```

Expected: FAIL because `ForwardRequest` has only buffered `content: bytes`.

**Step 3: Define the minimal contract**

Introduce a framework-neutral structural protocol:

```python
class ForwardRequestBody(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...
    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class ForwardRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: ForwardRequestBody | None = None
```

Document that the stream is one-shot, `aclose()` is idempotent, and the `Forwarder` owns it after `forward()` is entered. Remove the speculative documentation of a nonexistent enterprise SOFA forwarder.

**Step 4: Run tests to verify GREEN**

Run the Task 1 command and confirm the contract tests pass.

### Task 2: Make the HTTPX plugin stream and close request bodies

**Files:**
- Modify: `src/gateway/src/gateway/community/plugins/forwarder/httpx/_plugin.py`
- Test: `src/gateway/tests/contracts/spi/test_forwarder.py`

**Step 1: Write failing lifecycle tests**

Use custom HTTPX transports to prove:

- a transport failure before consuming the body still closes it;
- cancelling `forward().__aenter__()` closes it;
- response-body streaming and duplicate response headers remain unchanged.

**Step 2: Run tests to verify RED**

Run the Task 1 command. Expected: lifecycle assertions fail because the current plugin only accepts bytes and does not close an inbound stream.

**Step 3: Implement minimal streaming and cleanup**

Pass `request.body` directly to `build_request()`, or `None` for an empty body. Put both request construction and `client.send()` inside a `try/finally` which closes the request body. Explicitly disable HTTPX client-level authentication and redirect following for the send, even if the injected client enables them, because challenge-response auth and 307/308 redirects can replay the one-shot body. Gateway-provided authorization remains an ordinary forwarded header. Keep response cleanup in the existing context-manager `finally`.

Replace buffered-length logging with:

```python
declared_content_length = next(
    (value for name, value in request.headers.items()
     if name.lower() == "content-length"),
    "unknown",
)
```

Do not add retries, challenge-response authentication, or automatic redirects: request bodies are deliberately not replayable.

**Step 4: Run tests to verify GREEN**

Run the Task 1 command and confirm all lifecycle and compatibility assertions pass.

### Task 3: Stream the Starlette request into the Plugin API

**Files:**
- Modify: `src/gateway/src/gateway/community/adapters/web/_forward.py`
- Test: `src/gateway/tests/integration/test_forward_route.py`
- Test: `src/gateway/tests/integration/test_forward_signs_principal.py`
- Test: `src/gateway/tests/test_forward_access_log.py`
- Test: `src/gateway/tests/unit/core/forwarding/test_forward_seam.py`

**Step 1: Write the failing backpressure test**

Drive the Gateway ASGI app with an async client body whose second chunk is blocked. Use a test forwarder that consumes the first forwarded chunk and immediately returns a response. Assert that the forwarder observes the first chunk before the client releases the second one.

This must fail against `await request.body()`, which waits for every client chunk before calling the forwarder.

**Step 2: Run the test to verify RED**

Run:

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
  -u HTTPS_PROXY -u https_proxy uv run pytest -q \
  tests/integration/test_forward_route.py
```

Expected: FAIL or time out at the bounded assertion because the forwarder is not entered until the complete body is buffered.

**Step 3: Implement the Starlette adapter**

- Keep domain resolution, authentication, and principal signing before body consumption.
- Replace `await request.body()` with `request.stream()`.
- Prefetch only the first non-empty ASGI chunk. Return `None` when the stream is empty; otherwise wrap the first chunk and remaining iterator in a closeable one-shot body.
- Continue forwarding the incoming `Content-Length` header unchanged.
- Continue stripping inbound `Host`, forged principal headers, and hop-by-hop headers at their current layers.

**Step 4: Update test forwarders**

Change in-repository capturing and stub forwarders to use `request.body`; consumers that read it must close it according to the new Plugin API contract.

**Step 5: Verify request method compatibility**

Exercise JSON/raw request bodies through POST, PUT, and PATCH plus empty GET/DELETE requests. Confirm response streaming and duplicate response headers remain covered by the existing tests.

### Task 4: Verify cancellation, compatibility, and architecture

**Files:**
- Modify: `src/gateway/tests/contracts/spi/test_forwarder.py`
- Modify: `src/gateway/tests/integration/test_forward_route.py`

**Step 1: Run focused tests**

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
  -u HTTPS_PROXY -u https_proxy uv run pytest -q \
  tests/contracts/spi/test_forwarder.py \
  tests/integration/test_forward_route.py \
  tests/integration/test_forward_signs_principal.py \
  tests/unit/core/forwarding/test_forward_seam.py \
  tests/test_forward_access_log.py
```

Expected: all pass.

**Step 2: Run Gateway lint and type checks**

Run the module's standard Ruff, mypy, and basedpyright commands from its `justfiles`/CI script. Expected: zero new diagnostics.

**Step 3: Run the complete Gateway test suite**

Run the Gateway unit, contract, integration, and architecture suites from `src/gateway`. Expected: all locally runnable tests pass; report any environment-only E2E exclusions explicitly.

**Step 4: Inspect the final diff**

Confirm the diff changes only the Forwarder Plugin API, Starlette delivery adapter, HTTPX implementation, tests, and this plan. Verify that BCS contracts, identity policy, URL projection, and retry behavior are untouched.
