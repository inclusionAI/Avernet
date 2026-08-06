# Tasks: Connection Endpoint — Serving Bots on the ARCA Provider

Spec: `spec.md` · Plan: `plan.md`

Every task below touches exactly two files:

- `src/backend/src/agentclaw/community/core/engine_runtime/connection.py`
- `src/backend/tests/community/core/engine_runtime/test_connection.py`

Reference values used throughout (from `plan.md`, "Worked trace"):

| name | value |
| --- | --- |
| target | `ARCA_ARCA-SANDBOX-abc123@0:20003` |
| credential | `eyJhbGciOiJIUzI1NiIs…` |
| socket path | `/api/openclaw/ws` |
| gateway base | `https://gateway.example.com` |
| expected URL | `wss://gateway.example.com/openapi/v1/bots/messages/ws/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws?x-proxypass-token=eyJhbGciOiJIUzI1NiIs…` |

---

## Task 1: Extract the shared gateway-URL builder  `[ ]`

Pure refactor. No behaviour change, no new branch yet.

- Add `_gateway_url(self, tail: str, query: str, token: str) -> str` holding the
  last five lines of `_readdress_onto_gateway` (`connection.py:415-424`): append
  the percent-encoded credential to `query`, prefix `_gateway_ws_base()` +
  `_ENGINE_PREFIX` + `/` onto `tail`, join with `?` only when there is a query.
- Replace those lines in `_readdress_onto_gateway` with
  `return self._gateway_url(parts.path[len(_PROXYPASS_PREFIX):], parts.query, token)`.
- Carry the existing comments with the code they explain — in particular why the
  credential is *appended* to a provider query rather than assigned, and why an
  absent credential publishes no empty parameter.

**Worked check.** With `tail="ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws"`,
`query=""`, `token="eyJhbGciOiJIUzI1NiIs…"` the builder returns the expected URL
above. With `query="mode=chat"` it returns
`…/api/openclaw/ws?mode=chat&x-proxypass-token=eyJ…`. With `token=""` it returns
the URL with no `?` at all.

**Verify.** `pytest tests/community/core/engine_runtime/test_connection.py` — the
whole existing file passes untouched. This is the guard that the extraction was
lossless.

---

## Task 2: Name the bare-target connection kinds  `[ ]`

- Add `_PROXY_TARGET_TYPES = frozenset({"proxy", "arca"})` beside
  `_PROXYPASS_PREFIX` (`connection.py:84`).
- Document, in the constant's comment: that the corp `ArcaDeviceService` answers
  `"proxy"`; that these providers return a bare routing target plus a signed
  credential and no URL; that `get_device_connection_v2` (`device_service.py:1776`)
  and `connectionStore.ts:177` already assemble `/proxypass/{target}{path}` from
  the same two values; and why both spellings are accepted.

**Verify.** Nothing to run — the constant is unused until Task 3. Lint clean.

---

## Task 3: Compose the gateway URL for a bare routing target  `[ ]`

- Add `_compose_onto_gateway(self, target: str, socket_path: str, token: str) -> str`:
  `_quote_or_reject(target, safe="@:[]", what="routing target")` +
  `_quote_or_reject(socket_path, safe="/", what="socket path")` as the tail, an
  empty query, then `_gateway_url`. State in the docstring that the result is
  byte-identical to what `_readdress_onto_gateway` produces for the same target
  and path, because the origin is discarded there either way.
- In `_socket_url` (`connection.py:341-358`): read the connection kind once into
  `conn_type`, keep the `local` branch exactly as it is, and add
  `if conn_type in _PROXY_TARGET_TYPES: return self._compose_onto_gateway(target, socket_path, token)`
  before the fall-through to `_readdress_onto_gateway`.
- Extend the method's docstring from two shapes to three, naming which provider
  produces each.

**Worked check.** `_Devices(type="proxy", target="ARCA_ARCA-SANDBOX-abc123@0:20003", token="eyJ…")`
on an `openclaw` bot yields the expected URL above. The same fake on a
`claude_code` bot yields the same URL with `/api/claude_code/ws`.

**Verify.** `pytest tests/community/core/engine_runtime/test_connection.py` still
green (no existing case uses `type="proxy"`).

---

## Task 4: Name the unrecognised connection kind  `[ ]`

- At the top of `_readdress_onto_gateway`, after reading `url` and before parsing
  it, refuse an empty `url` with a message naming the kind:
  `f"device connection of kind {conn_type!r} carries no relay url and is not a kind this endpoint can compose one for"`.
  Pass the kind in, or re-read it from `info` — whichever keeps the method's
  signature honest about what it needs.
- Leave the wrong-shape message (`"…no relay url this endpoint can re-address…"`)
  for a URL that is present but not `/proxypass/`-shaped, e.g. BaaS LOCAL's
  `wss://host/wsrelay/6f2a…`. The two situations now read differently in a log.
- Do not touch the `responses.py` mapping: the published body stays
  `502 "Engine service error"`, and the kind reaches logs only.

**Verify.** Existing wrong-shape cases (`/wsrelay/`, unparseable URL, fragment)
still raise their original messages.

---

## Task 5: Correct the stale comment on the provider call  `[ ]`

`_get_connection` (`connection.py:285-308`) claims that leaving `ws_conn_mode`
unset is what makes a provider hand back a bare routing target, and that `path` is
baked into the URL by the provider. Both are BaaS-only facts. Amend to record:
ARCA returns a bare target regardless of the mode, and never sees `path` at all —
the base `get_device_connection` does not forward it to `_compose_device_conn_info`
(`device_service.py:1031`) — which is why the composed branch appends the engine
path itself.

Keep both arguments on the call: they are correct for BaaS and removing either
would break it.

**Verify.** Comment-only. Full test file green.

---

## Task 6: Tests  `[ ]`

Add to `tests/community/core/engine_runtime/test_connection.py`, beside the
existing local-branch cases, using the reference values above.

- [ ] A `proxy` device publishes the expected gateway URL, in full.
- [ ] The published URL contains neither the engine proxy's host nor `/proxypass/`.
- [ ] The target segment survives verbatim: `@` and `:` are not percent-encoded.
- [ ] A bracketed target keeps its brackets.
- [ ] The engine's own path is used — `claude_code` → `/api/claude_code/ws`.
- [ ] A `proxy` device with an empty token publishes no query string.
- [ ] A credential containing reserved characters is percent-encoded.
- [ ] Composed and re-addressed branches agree byte-for-byte: a `proxy` device and
      a `baas` device whose `url` is
      `wss://agentclawproxy-prod.example.com/proxypass/{same target}{same path}`
      publish the identical string.
- [ ] An unrecognised kind with `url=""` raises the new named error; the message
      carries the kind and not the credential.
- [ ] `expires_at` on a `proxy` device (which reports none) is `now + 7200s`.
- [ ] A `proxy` device with an empty target still raises
      `"device connection carries no routing target"`.
- [ ] An unencodable (lone-surrogate) target on a `proxy` device is named, not a
      500 — extend the existing parametrised case rather than writing a new one.

**Verify.** `pytest tests/community/core/engine_runtime/test_connection.py` and
`pytest tests/community/adapters/http/openapi_v1/engine_runtime/test_connection.py`
both green.

---

## Task 7: Verification against the spec  `[ ]`

- Walk each acceptance criterion in `spec.md` and tick it, or record why not.
- Run the module gates the pre-push contract requires
  (`AGENTS.md` → `Pre-push Module Selection`; `OCB_PRE_PUSH_RUN_CI=1` for the full
  set), with `REL20260806` as the merge target.
- Confirm nothing outside `connection.py` and its test file changed:
  `git diff --stat origin/REL20260806`.

---

## Groups

| group | tasks | why together |
| --- | --- | --- |
| A — refactor | 1 | Lossless extraction, proved by the untouched suite. Lands green on its own. |
| B — the fix | 2, 3, 4, 5 | The new branch, its constant, its error, and the comment that described the old two-shape world. Meaningless apart. |
| C — proof | 6, 7 | Tests and spec verification. |

## Notes for the implementer

- **Do not** inject `SandboxRuntimeClient`. The proxy base URL would be fetched
  only to be discarded — see `plan.md`, "Why no ARCA-side or configuration change
  is needed".
- **Do not** re-encode the BaaS path. Only the composed branch encodes, because
  only it builds from raw values.
- **Do not** parse the target. `ARCA_{id}@{alt}:{port}` and `ARCA_{id}:{port}` are
  both valid and both opaque to this service; the proxy checks the credential's
  claim against the whole string.
