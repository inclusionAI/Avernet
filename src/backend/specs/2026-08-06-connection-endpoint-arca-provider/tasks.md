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

## Task 1: Extract the shared gateway-URL builder  `[x]`

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

## Task 2: Name the bare-target connection kinds  `[x]`

- Add `_PROXY_TARGET_TYPES = frozenset({"proxy", "arca"})` beside
  `_PROXYPASS_PREFIX` (`connection.py:84`).
- Document, in the constant's comment: that the corp `ArcaDeviceService` answers
  `"proxy"`; that these providers return a bare routing target plus a signed
  credential and no URL; that `get_device_connection_v2` (`device_service.py:1776`)
  and `connectionStore.ts:177` already assemble `/proxypass/{target}{path}` from
  the same two values; and why both spellings are accepted.

**Verify.** Nothing to run — the constant is unused until Task 3. Lint clean.

---

## Task 3: Compose the gateway URL for a bare routing target  `[x]`

- Add `_compose_onto_gateway(self, target: str, socket_path: str, token: str) -> str`:
  `_quote_or_reject(target, safe="@:[]", what="routing target")` +
  `_quote_or_reject(socket_path, safe="/", what="socket path")` as the tail, an
  empty query, then `_gateway_url`. State in the docstring that the result is
  byte-identical to what `_readdress_onto_gateway` produces for the same target
  and path, because the origin is discarded there either way.
- In `_socket_url` (`connection.py:341-358`): read the connection kind once into
  `conn_type`, keep the `local` branch exactly as it is, then order the remaining
  cases as `plan.md` "Step 4" specifies —
  **(2)** a non-empty `info.url` → `_readdress_onto_gateway`;
  **(3)** `conn_type in _PROXY_TARGET_TYPES` → `_compose_onto_gateway`;
  **(4)** anything else → the named error (Task 4).
- Record in a comment why case 2 precedes case 3: a URL the provider issued
  records a routing decision it made, and this ordering is what lets a
  provider-side relay mode added later take over with no change here.
- Extend the method's docstring from two shapes to four cases, naming which
  provider takes each.

**Worked check.** `_Devices(type="proxy", target="ARCA_ARCA-SANDBOX-abc123@0:20003", token="eyJ…")`
on an `openclaw` bot yields the expected URL above. The same fake on a
`claude_code` bot yields the same URL with `/api/claude_code/ws`. The same fake
with `url="wss://agentclawproxy-prod.example.com/proxypass/ARCA_OTHER@0:20099/api/openclaw/ws"`
yields the **provider's** target (`ARCA_OTHER@0:20099`), not the composed one —
that is case 2 winning.

**Verify.** `pytest tests/community/core/engine_runtime/test_connection.py` still
green (no existing case uses `type="proxy"`).

---

## Task 4: Name the unrecognised connection kind  `[x]`

- Add case 4 of `_socket_url` as the method's final statement: raise
  `EngineUpstreamError(f"device connection of kind {conn_type!r} carries no relay url and is not a kind this endpoint can compose one for")`.
  It is reached only when the kind is not `local`, not a bare-target kind, and no
  URL was supplied.
- Leave `_readdress_onto_gateway` and its guards alone. Its wrong-shape message
  (`"…no relay url this endpoint can re-address…"`) now describes only URLs that
  are *present and unmappable* — e.g. BaaS LOCAL's `wss://host/wsrelay/6f2a…` —
  which is what it always meant. Its own empty-`url` path stays reachable in
  principle, so do not delete that guard; the two situations simply read
  differently in a log now.
- Do not touch the `responses.py` mapping: the published body stays
  `502 "Engine service error"`, and the kind reaches logs only.

**Verify.** Existing wrong-shape cases (`/wsrelay/`, unparseable URL, fragment)
still raise their original messages.

---

## Task 5: Correct the stale comment on the provider call  `[x]`

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

## Task 6: Tests  `[x]`

Add to `tests/community/core/engine_runtime/test_connection.py`, beside the
existing local-branch cases, using the reference values above.

- [x] A `proxy` device publishes the expected gateway URL, in full.
- [x] The published URL contains neither the engine proxy's host nor `/proxypass/`.
- [x] The target segment survives verbatim: `@` and `:` are not percent-encoded.
- [x] A bracketed target keeps its brackets.
- [x] The engine's own path is used — `claude_code` → `/api/claude_code/ws`.
- [x] A `proxy` device with an empty token publishes no query string.
- [x] A credential containing reserved characters is percent-encoded.
- [x] Composed and re-addressed branches agree byte-for-byte: a `proxy` device and
      a `baas` device whose `url` is
      `wss://agentclawproxy-prod.example.com/proxypass/{same target}{same path}`
      publish the identical string.
- [x] An unrecognised kind with `url=""` raises the new named error; the message
      carries the kind and not the credential.
- [x] **Ordering:** a `proxy` device that *also* carries a `/proxypass/…` URL is
      re-addressed, not composed. Give the URL a tail that differs from
      `target + socket_path` (a different port, say `ARCA_OTHER@0:20099`) so the
      assertion can tell the two branches apart.
- [x] **Ordering:** a `proxy` device carrying `wss://host/wsrelay/6f2a…` is
      refused with the wrong-shape message rather than quietly composed around.
- [x] `expires_at` on a `proxy` device (which reports none) is `now + 7200s`.
- [x] A `proxy` device with an empty target still raises
      `"device connection carries no routing target"`.
- [x] An unencodable (lone-surrogate) target on a `proxy` device is named, not a
      500 — extend the existing parametrised case rather than writing a new one.

**Verify.** `pytest tests/community/core/engine_runtime/test_connection.py` and
`pytest tests/community/adapters/http/openapi_v1/engine_runtime/test_connection.py`
both green.

---

## Task 7: Verification against the spec  `[x]`

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

---

## Verification record (Task 7)

**Acceptance criteria** — all ten hold, each pinned by a named test in
`tests/community/core/engine_runtime/test_connection.py`:

| criterion | test |
| --- | --- |
| ARCA bot gets a URL, not a 502 | `test_a_bare_target_provider_gets_a_composed_gateway_url` |
| byte-identical to the BaaS path | `test_composing_and_readdressing_agree_byte_for_byte` |
| addresses the gateway, names no proxy | `test_a_composed_url_never_names_the_hop_behind_the_gateway` |
| target survives unchanged | `test_a_composed_target_segment_survives_verbatim` |
| credential as an encoded query param | `test_a_composed_credential_is_percent_encoded` |
| the active engine's path | `test_a_composed_url_addresses_the_bots_own_engine` |
| expiry bounds the published credential | `test_a_composed_expiry_falls_back_to_the_requested_ttl` |
| a provider url wins over composing | `test_a_provider_url_wins_over_composing_one` |
| unrecognised kind named, not misdescribed | `test_an_unrecognised_connection_kind_is_named` |
| BaaS / local / gating unchanged | the 59 pre-existing cases, untouched |

**Runs.** `test_connection.py` 76 passed. `core/engine_runtime` +
`adapters/http/openapi_v1` + `architecture` + `core/devices` 1358 passed.
`ruff check` clean on both changed files. The pre-push contract's
`python_sast_local.sh` gate passed against `origin/REL20260806`.

**Not run here.** `scripts/ci_test.sh` runs all 10 836 `tests/community` cases
under `--cov` over the whole source tree. In this sandbox that does not finish:
collection alone takes ~37 s uninstrumented and over 20 minutes under coverage,
and unrelated suites stall on network egress through the agent proxy. The
change touches one file whose only consumers are the connection router and this
test module, so the blast radius above is covered; the full gate runs on CI.

**Diff scope.** `git diff --stat origin/REL20260806` — `connection.py`,
`test_connection.py`, and this feature's three artifacts. Nothing else.
