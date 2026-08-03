# Tasks: Path-specific gateway domain routing, and the bot socket under `bots`

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## [x] Task 1: Extract the shared path pattern into `core/paths`

- **Goal:** One parse/match/rank implementation for path patterns, importable by
  both the routing and the auth plane without a private-module import.
- **Files:**
  - `src/gateway/src/gateway/community/core/paths/__init__.py` (new)
  - `src/gateway/src/gateway/community/core/paths/_pattern.py` (new)
  - `src/gateway/tests/test_path_pattern.py` (new)
- **Done when:**
  - [x] `PathPattern.parse`, `.matches`, `.specificity`, `.literal_prefix` and
        `split_segments` exist, with `__all__` declared.
  - [x] `specificity` is `(exact?, literals, params)` — today's
        `_route_security._specificity` **minus** its method tie-break.
  - [x] `**` matches zero segments, so `/openapi/v1/bots/**` matches the bare
        `/openapi/v1/bots` (the property `test_domain_at_root_resolves` rests on).
  - [x] `literal_prefix` returns the leading run of literal segments as a path.
  - [x] `tests/architecture/` stays green (`test_all_exports_valid.py` requires
        the new package's `__all__`).
- **Depends on:** —

## [x] Task 2: Re-point `RouteSecurity` at the shared pattern

- **Goal:** Delete route security's private matcher/ranker in favour of
  `PathPattern`, with no behaviour change on the auth plane.
- **Files:**
  - `src/gateway/src/gateway/community/core/authn/_route_security.py`
  - `src/gateway/tests/test_route_security.py`
- **Done when:**
  - [x] `_Rule` holds a `PathPattern`; `_match_segments`, `_is_param` and the
        segment-counting half of `_specificity` are gone from this module.
  - [x] The method tie-break stays here — the domain map has no methods.
  - [x] `tests/test_route_security.py` passes **unchanged** (the engine-prefix
        case still asserts the old path at this point; Task 7 moves it).
  - [x] `bootstrap/_authn.py` logged the table via `_Rule.segments` — a caller the
        plan did not list; re-pointed at `rule.pattern.segments`.
- **Depends on:** Task 1

## [x] Task 3: Give `Domain` a pattern, and validate it at boot

- **Goal:** A domain declares `match:` (or inherits `{base_path}/{name}/**`), and
  a pattern that would make the gateway an open or ambiguous proxy is refused at
  startup, naming the domain.
- **Files:**
  - `src/gateway/src/gateway/community/core/forwarding/_domains.py`
  - `src/gateway/tests/test_domain_map.py`
- **Done when:**
  - [x] `Domain.pattern: PathPattern` and `Domain.mount_prefix` exist; `match` is
        added to `_DOMAIN_KEYS`.
  - [x] A domain with no `match` gets `{base_path}/{name}/**` — every shipped
        HTTP domain keeps its current address.
  - [x] Accepted shape is `<literal segments>/**` only. Refused, at boot, naming
        the domain: `/**`, `/openapi/**`, `/openapi/v1/**` (over-broad);
        `/openapi/v1/{x}/**` (a parameter cannot pin a prefix);
        `/openapi/v1/bots` (missing `/**`).
  - [x] Two domains declaring the same pattern with overlapping protocols are
        refused at boot.
  - [x] `_parse_rewrite`'s anchor is `pattern.literal_prefix`; its existing
        `"can never match"` and segment-boundary checks still fire.
- **Depends on:** Task 1

## [x] Task 4: Resolve on (pattern, plane) — match first, then most specific

- **Goal:** Replace leading-segment lookup with plane-filtered, specificity-ranked
  resolution, and hand the adapters two named entry points so no protocol
  constant crosses the layer boundary.
- **Files:**
  - `src/gateway/src/gateway/community/core/forwarding/_domains.py`
  - `src/gateway/src/gateway/community/adapters/web/_forward.py`
  - `src/gateway/tests/test_domain_map.py`
- **Done when:**
  - [x] `domain_for(path, protocol)` filters candidates by plane **before**
        ranking, and returns the most specific match or `None`.
  - [x] `http_domain_for` / `websocket_domain_for` wrap it; `resolve(path)` is
        gone (a default plane would silently pick one).
  - [x] `_forward.py` calls `http_domain_for` and drops its `serves_http` check.
  - [x] A test proves an HTTP request under a websocket-only prefix resolves to
        the **broader HTTP domain**, not to `None` — the trap this design exists
        to avoid.
  - [x] A test proves resolution does **not** retry after a plane mismatch.
- **Depends on:** Task 3

## [x] Task 5: Mount socket routes and the raw-path guard from the pattern

- **Goal:** The socket entrypoint derives its mount paths and its
  encoded-prefix guard from the declared pattern rather than from `base_path +
  domain name`.
- **Files:**
  - `src/gateway/src/gateway/community/adapters/web/_relay_ws.py`
  - `src/gateway/src/gateway/community/adapters/web/app.py`
  - `src/gateway/src/gateway/community/core/forwarding/_domains.py`
- **Done when:**
  - [x] `relay_routes(prefix)` takes the prefix; `websocket_domains()` returns
        the domains themselves.
  - [x] Both mount forms are still produced together (bare prefix **and**
        `/{full_path:path}`) — a handshake to exactly the prefix must still be
        served.
  - [x] `_required_raw_prefix` returns `rewrite.from_prefix`, else
        `domain.mount_prefix`. **Guard logic unchanged** — it is what defeats a
        percent-encoded routing prefix.
  - [x] `forward_websocket` calls `websocket_domain_for` and drops its
        `serves_websocket` check.
  - [x] Docstrings naming `/openapi/v1/engine` in worked examples are rewritten
        against the new prefix.
- **Depends on:** Task 4

## [x] Task 6: Move the shipped socket domain to `/openapi/v1/bots/messages`

- **Goal:** The gateway serves the socket at its new address and no longer at the
  old one — configuration only.
- **Files:**
  - `src/gateway/configs/application.yaml`
- **Done when:**
  - [x] The `engine` domain block becomes `bots-messages-ws` with
        `match: /openapi/v1/bots/messages/**`, `protocols: [websocket]`, and
        `rewrite: {from: /openapi/v1/bots/messages, to: /proxypass}`.
  - [x] Its explanatory comment carries over: Upgrade pass-through, **no read
        timeout** on the path, and no `schema:` because a socket has no OpenAPI
        representation.
  - [x] `route_security` declares `"/openapi/v1/bots/messages/**": {}` and no
        longer declares `"/openapi/v1/engine/**"`; the comment explaining why it
        requires no identity carries over.
  - [x] The `domains:` block comment documents the new `match:` key.
- **Depends on:** Task 5

## [x] Task 7: Re-point the gateway test suites at the new address

- **Goal:** Every gateway test that pins the socket address moves with it, and the
  old address is pinned as *no longer resolving*.
- **Files:**
  - `src/gateway/tests/integration/test_relay_ws_route.py`
  - `src/gateway/tests/test_route_security.py`
  - `src/gateway/tests/test_domain_map.py`
  - `src/gateway/tests/test_log_redaction.py`
- **Done when:**
  - [x] Relay integration cases run against `/openapi/v1/bots/messages/…`; the
        rewrite still lands on `/proxypass/<target><path>` byte for byte.
  - [x] The encoded-prefix matrix moves to the new prefix (the `%62ots` /
        `%6dessages` spellings) rather than being dropped, and still refuses.
  - [x] The dot-segment matrix still refuses at the new prefix.
  - [x] A handshake to `/openapi/v1/engine/**` no longer resolves.
  - [x] An HTTP request to `/openapi/v1/bots/messages/x` reaches **backend**.
  - [x] `test_route_security.py` asserts the exemption at the new prefix **and**
        that it beats the `/openapi/v1/bots/**` user requirement.
  - [x] **Security fix, not in the plan:** the route-security exemption is
        qualified by plane (`WEBSOCKET /openapi/v1/bots/messages/**`). Unqualified,
        it exempted HTTP requests that now fall through to the backend — see
        `spec.md`'s corrected answer to objection 2.
- **Depends on:** Task 6

## Task 8: Publish the new address from the backend

- **Goal:** `GET /openapi/v1/bots/connection/{bot_id}` hands out the new socket
  URL; its own path, name, and response shape are unchanged.
- **Files:**
  - `src/backend/src/agentclaw/community/core/engine_runtime/connection.py`
  - `src/backend/src/agentclaw/.../openapi_v1/engine_runtime/connection/schemas.py`
  - `src/backend/tests/community/core/engine_runtime/test_connection.py`
  - `src/backend/tests/community/adapters/http/openapi_v1/engine_runtime/test_connection.py`
- **Done when:**
  - [ ] `_ENGINE_PREFIX = "/openapi/v1/bots/messages"`.
  - [ ] The module docstring, the constant's comment, and the docstrings at
        `_readdress_onto_gateway` and `_gateway_ws_base` no longer name the old
        address.
  - [ ] Both example URLs in `schemas.py` use the new address.
  - [ ] Published-URL assertions in both test files move to the new prefix.
  - [ ] `coverage_baseline.txt` needs **no** edit — verify, don't assume: the
        endpoint's own path did not change.
- **Depends on:** Task 6

## Task 9: Record `messages` as a reserved component name

- **Goal:** The reservation is written down where the addressing rule lives, and
  cannot silently fall out of step with the routes.
- **Files:**
  - `src/backend/docs/openapi-v1/README.md`
  - `src/backend/docs/openapi-v1/README.zh-CN.md`
  - `src/backend/tests/community/adapters/http/openapi_v1/test_path_convention.py`
- **Done when:**
  - [ ] A second fenced block under a new `<!-- reserved-component-names-unrouted -->`
        anchor holds `messages`, placed **after** the existing block (the parser
        takes the first fence following its anchor).
  - [ ] `test_the_docs_reserved_names_match_the_routes` is **unchanged** — still
        an equality check.
  - [ ] A new test asserts the unrouted block is disjoint from the routed
        components, so a name that gains a route must move blocks.
  - [ ] The prose states the accurate reason: `messages` is reserved because the
        gateway claims the prefix on the **socket plane** and a component is
        planned there — *not* because a bot id would be unreachable, which is
        untrue for this name today.
  - [ ] The Chinese README carries the same record. It has **no** anchor today;
        add one so both stay parseable.
- **Depends on:** Task 8

## Task 10: Update the engine-surface docs and regenerate the pinned artifact

- **Goal:** Published documentation and the gateway's pinned OpenAPI artifact
  quote the new socket address.
- **Files:**
  - `src/backend/docs/openapi-v1/engine-surface.md`
  - `src/backend/docs/openapi-v1/engine-surface.zh-CN.md`
  - `src/gateway/configs/schemas/bots.openapi.json`
- **Done when:**
  - [ ] Both documents quote `wss://<gateway>/openapi/v1/bots/messages/…` and
        describe the rewrite against the new prefix.
  - [ ] `bots.openapi.json` is regenerated via `dump_openapi.py` +
        `gate_and_publish_openapi.py` — the *paths* do not change, but the socket
        URL appears as an example value in two places.
  - [ ] `src/gateway/tests/fixtures/bots.openapi.json` is **not** regenerated —
        it is a hand-written fixture, not a copy.
- **Depends on:** Task 8, Task 9

## Task 11: Tests & Verification

- **Goal:** Every spec acceptance criterion is demonstrably met, and what could
  not be run is stated.
- **Files:** —
- **Done when:**
  - [ ] Gateway suite green: `test_path_pattern`, `test_domain_map`,
        `test_route_security`, `test_relay_ws_route`, `tests/architecture/*`.
  - [ ] Backend suite green for `engine_runtime` and `openapi_v1` tests.
  - [ ] Coverage gate checked by grepping for **`ERROR` as well as `FAILED`** —
        it errors on a missing fixture in isolation and would otherwise drop out
        of a before/after diff silently.
  - [ ] Every acceptance criterion in `spec.md` ticked, each against a named test
        or a named file.
  - [ ] Pre-existing sandbox failures reported as such, not as regressions:
        legacy `/api/bots` 403s, missing SQLite fixtures under
        `tests/community/{endpoints,e2e}/*`, gateway `tests/e2e/*` needing a live
        server, and `ruff format --check` on gateway `docs/*.md`.
- **Depends on:** Task 10

---

## Groups

- **Group A — The shared pattern:** Tasks 1, 2
  - Theme: One parse/match/rank implementation, adopted by the auth plane with no
    behaviour change. Lands independently and is provably inert.
- **Group B — Pattern-based routing:** Tasks 3, 4, 5
  - Theme: The gateway resolves on (pattern, plane) with boot-time refusal of
    over-broad and ambiguous declarations. Still serves the *old* address — the
    mechanism arrives before the move, so a failure here is unambiguous.
- **Group C — The move:** Tasks 6, 7
  - Theme: The socket changes address, and the gateway suites prove both the new
    address works and the old one is gone.
- **Group D — Backend and docs:** Tasks 8, 9, 10
  - Theme: The backend publishes the new address; the reservation and the
    published docs and artifact catch up.
- **Group E — Verification:** Task 11
  - Theme: Final spec acceptance check.
