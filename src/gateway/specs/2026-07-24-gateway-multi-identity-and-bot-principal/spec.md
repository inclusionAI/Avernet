# Gateway: Multi-Identity AuthN & Bot Principal

**Status:** Draft / ready-for-agent
**Date:** 2026-07-24 (rev 4: 2026-07-26)
**Component:** `src/gateway` (`gateway.community`, Python / FastAPI)
**Scope:** The gateway's authentication (AuthN) output — the identity model, the
auth runner, and the per-route auth declaration. **Authorization (AuthZ) is
removed from the gateway.** A new `bot` identity type is added, a single request
can yield more than one identity, and the **user identity** is acquired by
verifying a presented **Google access token** against Google's userinfo endpoint
(mirroring BCS `bcs-auth-google`); there is no cookie/session fallback.
**Related constraints:** `docs/arch/arch.rules.md` (Rule 1 / 7 / 14 / 19 / 25),
`src/gateway/docs/2026-07-21-auth-design.md` (§4 Principal model, §5 AuthStrategy,
§7 runner, §8 route-security).

**Revisions:**
- 2026-07-24 (initial): multi-identity runner, remove AuthZ, `BotPrincipal`.
- 2026-07-24 (rev 2): user-identity acquisition via two plugins (`token` → `cookie`
  fallback) and a bare-flavor `source` header dispatch.
- 2026-07-24 (rev 3): per-plugin applicability self-selection + config-driven
  `chain` (no `source_override`); fallback semantics.
- 2026-07-26 (rev 4): **drop the cookie user-identity strategy entirely** — user
  identity comes only from a verified Google access token. **Inline the Google
  user-info logic into `GoogleUserStrategy`** (no `ProviderUserInfo` abstraction),
  mirroring BCS `bcs-auth-google` `get_user_info` (call Google's userinfo endpoint
  with the presented token as a bearer). Remove the `UserTokenValidator` SPI,
  the `source` header, and the cookie-last security ordering. Add `httpx` as a
  runtime dependency and a `transport` injection seam for tests.
- 2026-07-27 (rev 5): **drop the `BotTokenValidator` SPI** — fold the BCS
  `BotRegistryLookup` two-step composition (``find_bot_by_token`` + ``get``)
  directly into `BotTokenStrategy`, which now holds a `BotRegistry`
  (`InMemoryBotRegistry`, mirroring BCS `BotRegistryCoreService`). Remove
  `BareBotTokenValidator`, `BotRecord`, and the validator conformance test.

---

## Problem Statement

Today the gateway authenticates each request into **exactly one** identity: the
runner adopts the first passing `AuthStrategy` alternative (OR-fallback) and
stops, returning a single `Principal`. That single-Principal model blocks three
needs:

1. A request that should resolve to **more than one identity at once** has no way
   to express it. A caller may legitimately present credentials that map to
   several identity types in the same call (for example a bot identity **and**
   its owning user identity), but the gateway can only hand the downstream
   component one of them.

2. There is **no identity type for a calling bot**. Bot-originated traffic can
   only be modeled as a `UserPrincipal`, which loses the bot's own identity
   (its `bot_uuid`), its ownership link (`owner_id`), and its credential.

3. The **user identity** can currently be obtained only one way, and that path is
   not how our platform's users actually log in. Real users authenticate through
   Google (an OAuth access token issued by Google after its consent/login), and
   the gateway had no way to verify such a presented token against Google and
   turn it into a `UserPrincipal`. There was also a session-cookie fallback that
   is no longer wanted.

The gateway also couples authentication to an **authorization scope gate**: the
runner enforces `required scopes ⊆ granted scopes`, and `scopes` live on both
`Principal` and the per-route strategy params. This entangles the gateway in
authorization that the architecture constitution (Rule 7) says belongs in
components; it is also dormant in practice — the shipped routes declare no
scopes. The permission machinery is baggage the gateway should not own.

## Solution

Make the gateway **authentication-only (AuthN) and multi-identity-capable**:

- **Remove authorization from the gateway.** Drop the scope dimension entirely —
  from the `Principal` model, the strategy params, the runner, the route table,
  and the OpenAPI security marker. The gateway produces authenticated
  identities; components decide authorization.
- **Let one request yield many identities.** Each route declares the **set of
  identity types it requires** (e.g. `user`, or `bot`, or both `bot` + `user`).
  The runner produces a `Principal` for each required type from the request's
  credentials, collects them into one result, and rejects (fail-closed) if any
  required identity is absent or invalid.
- **Add a bot identity.** Introduce a `BotPrincipal` type in the `Principal`
  discriminated union, carrying `bot_uuid`, `owner_id`, and `token` (plus the
  `type` discriminator and a `tenant`). It is produced by a `bot_token` auth
  strategy from a bot credential in the request.
- **Acquire the user identity by verifying a presented Google access token.**
  The `user` requirement is served by a single strategy, `GoogleUserStrategy`:
  it reads a Google OAuth access token from a request header and verifies it by
  calling **Google's userinfo endpoint** with the token as a bearer (mirroring
  BCS `bcs-auth-google` `get_user_info`), then maps the normalized user info
  onto a `UserPrincipal`. There is **no cookie/session fallback** and no
  `source` header — user identity in the gateway comes only from a verified
  Google access token.

From an integrator's perspective: a bot can now call the gateway **as a bot**,
one call can carry both a bot and a user identity, and a user identity is
established by presenting a Google access token that the gateway verifies
against Google. The gateway hands downstream the full authenticated set instead
of picking one, and enforces no permission (that moves entirely to components).

## User Stories

1. As a **gateway maintainer**, I want the gateway to stop enforcing authorization
   scopes, so that the gateway is purely an authentication layer and authorization
   lives in the components (architecture Rule 7).
2. As a **gateway maintainer**, I want `scopes` removed from the `Principal` model
   and from per-strategy params, so that identity no longer carries permission
   data.
3. As a **gateway maintainer**, I want the runner's `required ⊆ granted` scope
   check removed, so that authentication success no longer depends on scope
   subsets.
4. As a **gateway maintainer**, I want `route_security` and `x-avernet-security`
   to no longer accept `scopes`, so that no route declares gateway-side
   permissions.
5. As a **third-party integrator**, I want a single request to authenticate as
   more than one identity, so that one call can carry both a bot identity and its
   owning user identity.
6. As a **bot developer**, I want my bot to call the gateway with its own
   identity, so that the call is attributed to the bot instead of being
   impersonated as a user.
7. As a **bot developer**, I want the bot identity to carry `bot_uuid`,
   `owner_id`, and `token`, so that downstream components know which bot called
   and who owns it.
8. As a **gateway maintainer**, I want a new `BotPrincipal` type in the
   `Principal` discriminated union, so that bot callers are a first-class
   identity alongside users.
9. As a **gateway maintainer**, I want each route to declare the **set of
   identity types it requires**, so that the gateway knows exactly which
   identities to produce per route.
10. As a **gateway maintainer**, I want the runner to produce a `Principal` for
    each required identity type and collect them, so that one request yields
    multiple identities.
11. As a **gateway maintainer**, I want the runner to reject a request when any
    required identity type is absent or invalid, so that fail-closed behavior is
    preserved.
12. As a **downstream component**, I want to receive the full authenticated
    identity set (not a single `Principal`), so that I can project whichever
    identity I need onto my own domain DTO.
13. As a **downstream component**, I want the bot identity's `owner_id`
    available, so that I can anchor resource ownership to the bot's owner.
14. As a **gateway maintainer**, I want a `bot_token` auth strategy that builds a
    `BotPrincipal` from a bot credential, so that bot traffic is authenticated
    consistently with the existing strategy pattern.
15. As a **gateway maintainer**, I want each auth strategy to declare which
    `PrincipalType` it produces, so that the runner can map each required type to
    the right strategy(ies).
16. As a **caller**, I want to present a Google OAuth access token, so that the
    gateway verifies it against Google and authenticates me as a user.
17. As a **gateway maintainer**, I want the Google verification to mirror BCS
    `bcs-auth-google` `get_user_info` (call Google's userinfo endpoint with the
    token as a bearer), so the gateway's notion of a Google user matches BCS.
18. As a **gateway maintainer**, I want the Google user-info logic to live
    directly in `GoogleUserStrategy` (no `ProviderUserInfo` abstraction), so the
    strategy is self-contained and easy to follow.
19. As a **gateway maintainer**, I want the session-cookie user-identity path
    **removed**, so the gateway does not authenticate users from a cookie — only
    from a verified Google access token.
20. As a **community-edition operator**, I want a `bare` bot-token verifier, so
    that the open-source edition runs without a bot-registry backend.
21. As an **enterprise operator**, I want a `sofa` bot-token verifier pluggable
    via `GATEWAY_RUN_MODE`, so that the enterprise edition resolves bots from the
    real registry without changing strategy code.
22. As a **gateway maintainer**, I want the request-level auth dependency to yield
    the identity collection, so that router handlers receive all authenticated
    identities.
23. As a **router author**, I want a helper to declare required identity types in
    `x-avernet-security`, so that the OpenAPI document reflects the new auth
    contract.
24. As a **reviewer**, I want the OpenAPI marker on every `/openapi/v1` operation
    to declare a non-empty required-identity set, so that no exposed route is
    missing an auth declaration.
25. As a **gateway maintainer**, I want the existing unit / integration /
    contract tests updated to the new model, so that the suite keeps passing
    without introducing a new test layer.
26. As a **gateway maintainer**, I want the architecture rules to keep passing,
    so that the new SPI surface has a conformance test and respects layer
    boundaries (Rule 25).
27. As a **security reviewer**, I want the bot `token` carried in the
    `BotPrincipal` to be documented as a secret flowing downstream, so that
    components treat it as sensitive.
28. As a **gateway maintainer**, I want `Delegation` / `StrategyParams`
    simplified or removed now that routes declare identity types, so that
    permission-era baggage does not linger.
29. As a **gateway maintainer**, I want every `BotPrincipal` to carry a `tenant`
    derived from the owner, so that the "every Principal has a tenant" invariant
    from the auth design is preserved.
30. As a **gateway maintainer**, I want a request that carries no credential for a
    required identity type to receive `401`, so that the failure mode is clear.
31. As a **gateway maintainer**, I want a request with an unresolvable bot token
    (present but the bot is unknown) to receive `401`, so a bad credential never
    authenticates as a bot. (Mirroring BCS, an unknown token is a soft miss for
    the bot strategy — `None`, not a hard raise — so the chain may continue;
    with the bot chain being single-plugin today the request still fail-closes
    to `401`.) A JWT-shaped bearer is never treated as a bot token.
32. As a **bot developer**, I want my bot's `owner_id` resolved by the gateway
    from the bot token, so that I do not have to separately present the owner's
    credential when only the bot identity is required.
33. As a **downstream component**, I want bot-originated and user-originated
    traffic distinguishable by `PrincipalType`, so that I can apply different
    authorization logic per identity.
34. As a **gateway maintainer**, I want the route table's default to require a
    `user` identity, so that existing v1 routes keep behaving as
    user-authenticated unless overridden.
35. As a **gateway maintainer**, I want tests to verify the Google strategy
    without network, so CI is hermetic.
36. As a **gateway maintainer**, I want an HTTP-transport injection seam on the
    Google strategy, so tests pass an `httpx.MockTransport` instead of hitting
    Google.
37. As a **caller**, I want an unverifiable Google access token to be rejected
    with `401` (no fallback), so a bad token is never silently accepted.

## Implementation Decisions

> Module-area level only (no file paths); the discriminated-union and runner
> type shapes below are decision-rich and inlined as schemas, not working code.

### Identity model (authn SPI models)

- `Principal` is a **real discriminated union** `UserPrincipal | BotPrincipal`
  discriminated by `type`. `PrincipalType` has `USER = "user"` and `BOT = "bot"`.
  The deferred `AppPrincipal` (third-party app, auth design §15) is untouched.
- `UserPrincipal` — **no `scopes`**; keeps `type`, `tenant`, `subject` (the
  existing `AuthenticatedUser`).
- `BotPrincipal` (frozen pydantic model):

  ```python
  class BotPrincipal(BaseModel):            # type shape — the decision
      model_config = ConfigDict(frozen=True)
      type: Literal[PrincipalType.BOT] = PrincipalType.BOT
      tenant: str        # owner's tenant, resolved during bot verification
      bot_uuid: str      # the bot's stable identifier
      owner_id: str      # the user who owns the bot
      token: str         # the presented/verified bot credential
  ```

  - `tenant` honors the auth-design invariant "every Principal carries a tenant";
    it is resolved from the bot's owner. It is a review point — the reporter named
    only `bot_uuid`, `owner_id`, `token`; `tenant` can be dropped if exactly those
    three are wanted, but the invariant is recommended.
  - `token` is the **raw bot credential** carried through, per the reporter's
    explicit field list. This **intentionally deviates** from the auth design's
    "components never see raw credentials" rule — see Further Notes.

### Removed permission machinery

- Remove `scopes` from `UserPrincipal` and from the per-route strategy params.
- Remove the runner's `required scopes ⊆ granted scopes` check.
- Remove `Delegation` and `StrategyParams` (they existed only to carry
  `scopes`/`delegation`). A route now declares required identity *types*. If a
  future strategy needs a per-route param, reintroduce a minimal param container
  then — not now.
- `AuthPlugin` (the `spi/auth` auth-plugin SPI) is **unchanged**: `get_login_user`
  stays (no longer used by the gateway's authn flow after the cookie strategy was
  removed); `is_allowed` / `check_permission` are **retained on the SPI** per
  decision, for component-side use. Resource-level authorization stays in
  components.

### AuthStrategy contract

- `AuthStrategy.build(creds) -> Principal | None` — **no `params` argument**.
  Applicability is decided inside `build` by reading `creds`.
- Each strategy declares `principal_type: PrincipalType`. **Multiple strategies
  may produce the same type** (the runner tries them in the configured chain
  order); today each type has a single strategy (`google` for `USER`,
  `bot_token` for `BOT`).
- `None` means "not applicable / no credential" → the runner **falls through**;
  raising `AuthError` means "applicable but the credential is invalid" →
  **terminal**, no fallback.

### Strategy configuration — the chain only

- A new authn config (`configs/authn.yaml`) maps each identity type to an
  **ordered list of strategy names**:

  ```yaml
  identity_strategies:
    user:
      chain: [google]
    bot:
      chain: [bot_token]
  ```

  Names reference strategies in a per-flavor strategy pool (name → instance).
  The composition root loads this config, validates every name exists in the pool
  (startup fails on an unknown name), and builds the ordered registry the runner
  consumes. The chain mechanism (ordered fallback across multiple plugins per
  type) is retained for future multi-provider user identity, even though each
  shipped type is currently single-plugin.
- A built-in default chain (mirroring the shipped `authn.yaml`) is used when the
  config file is absent or unresolvable (e.g. tests with a cwd that has no
  `configs/`), so `create_app()` is always buildable.
- This is orthogonal to `route_security.yaml`: `route_security` says *which
  identity types a route requires*; `authn.yaml` says *which plugin chain
  produces each type*.

### Runner — multi identity, ordered fallback per type

- `authenticate(creds, requirement, registry) -> Identities`, where
  `requirement: frozenset[PrincipalType]` and
  `registry: dict[PrincipalType, tuple[AuthStrategy, ...]]` (the configured
  ordered chain per type).
- For **each** required type: run its chain **in order**. The first plugin that
  returns a `Principal` wins. A plugin returning `None` (not applicable / no
  credential) **falls through** to the next. A plugin raising `AuthError`
  (applicable but invalid) is **terminal** — no fallback. If the chain is
  exhausted with no `Principal` → **fail-closed**, raise (401) for that type.
  Collect one `Principal` per required type into the result. No scope check.
- `Identities` container (frozen, lives in the authn SPI): queryable by
  `PrincipalType` — `.get(type) -> Principal | None`, `.require(type) -> Principal`
  (raises if absent), iterable. This is what the delivery layer hands to handlers.

### User identity acquisition — Google userinfo (inlined, no `ProviderUserInfo`)

- The `user` requirement is served by **`GoogleUserStrategy`** (renamed from an
  earlier `GoogleTokenStrategy`). It reads a Google OAuth access token from a
  designated request header (`x-user-token`) and verifies it **by calling
  Google's userinfo endpoint** with the token as a bearer — directly, mirroring
  BCS `bcs-auth-google` `get_user_info`. No separate provider type and no
  `ProviderUserInfo`/`UserInfoError` abstraction: the get-user-info-from-token
  logic lives inside the strategy.
- Behavior of `GoogleUserStrategy.build(creds)`:
  - no token presented → return `None` (not applicable; the runner fail-closes
    for `user` → 401).
  - a presented token whose userinfo call returns non-200, or whose body cannot
    be parsed (`sub` missing) → raise `AuthError` (terminal, no fallback → 401).
  - a verified token → read `{sub, name, email}` from the userinfo response,
    map onto `AuthenticatedUser(id=sub, username=email or sub,
    display_name=name)`, and return `UserPrincipal(tenant=default_tenant,
    subject=...)`. `tenant` is a configured default (Google userinfo carries no
    tenant).
- Google endpoint constants live in the plugin's `_config.py`, mirroring BCS
  `bcs-auth-google/src/config.rs` (`GOOGLE_AUTH_URL`, `GOOGLE_TOKEN_URL`,
  `GOOGLE_USERINFO_URL`, `GOOGLE_SCOPES`, plus a bounded userinfo timeout). Only
  `GOOGLE_USERINFO_URL` (and the timeout) is used today; the others are kept for
  parity with BCS and a possible future login-flow port.
- `httpx` is a **runtime dependency** of the gateway.
- **Transport seam for tests:** the strategy accepts an optional
  `transport: httpx.BaseTransport | None`. Production omits it (real network);
  tests pass an `httpx.MockTransport` so the userinfo call never reaches Google.
  This is an HTTP-transport injection, not a user-info business abstraction.
- There is **no `source` header** and **no cookie/session fallback**. The earlier
  `UserTokenValidator` SPI and its bare in-memory impl (rev 3) are **removed**.
  The `BotTokenValidator` SPI was also removed (rev 5): the registry composition
  now lives directly in `BotTokenStrategy`.

### bot_token strategy (single bot-token → bot lookup)

- `bot_token` strategy (`principal_type = BOT`) — **mirrors BCS
  `bcs-auth-session::SessionTokenPlugin`**, with the bot-token resolution folded
  into the strategy itself (no separate validator/SPI):
  - **Token extraction** (`extract_bot_token`): a dedicated bot-token header
    (`x-bot-token`) **wins**, taken as-is when non-empty; otherwise an
    `Authorization: Bearer <token>` (or a bare token) is used, but **only when
    NOT JWT-shaped** — `is_jwt_format` (three dot-separated segments, mirroring
    BCS `jwt.rs`) skips JWTs so a (future) JWT identity is never mistaken for a
    bot session token. Absent/empty → not applicable.
  - **Resolution** in a **single** registry lookup:
    `find_bot_by_token(token) → Bot | None` — the registry indexes the token
    directly against the bot record, so no second `get`/token-uuid step is
    needed. No `verify()`/`BotRecord` abstraction.
  - A resolved bot → `BotPrincipal(bot_uuid, owner_id, token, tenant)`. An
    **unknown** or empty token → `None` (a *soft miss* mirroring BCS
    `find_bot_by_token → None`, letting the chain continue / fail-close).
- **Bot registry**: `BotRegistry` protocol (read-only) + `InMemoryBotRegistry`
  bare impl — a token → `Bot` map exposing only
  `find_bot_by_token(token) → Bot | None` (the single lookup). `Bot` carries
  `bot_uuid`, `owner_id`, `tenant`. How bots enter the map (seeding, a
  DB-backed loader) is a flavor implementation detail; `bare` seeds one demo
  bot (and accepts explicit `entries` for tests). `sofa` impl (real registry /
  DB-backed) is a follow-up. The composition root wires
  `BotTokenStrategy(registry=InMemoryBotRegistry(), token_header="x-bot-token")`.
- `BotPrincipal.token` is the **raw bot credential** carried downstream (see
  Further Notes).
- Deriving a full `UserPrincipal` for a bot's owner purely from the bot token
  (without a presented user credential) is out of scope. A `[bot, user]` route
  requires the request to present credentials for both.

### Route security (per-route required identity set)

- The route requirement is a **set of required `PrincipalType`s**:
  `frozenset[PrincipalType]`. Routes declare identity **types**, not strategy
  names. Unknown type strings are rejected at parse time.
- YAML shape `path -> list of type strings`, e.g.:

  ```yaml
  route_security:
    "/**":                        [user]
    "/openapi/v1/bots/**":        [user]
    "/openapi/v1/bots/{id}/chat": [bot, user]
  ```

  Matching/specificity behavior is unchanged; only the value shape changed.

### OpenAPI security marker

- `x-avernet-security` is a **list of `PrincipalType` strings** (e.g. `["user"]`,
  `["bot", "user"]`). The helper `requires_identities(*types)` emits it;
  `requires_user_principal()` is a thin shim (`= requires_identities(USER)`).

### Delivery dependency

- The web-adapter auth dependency is `require_identities -> Identities`; handlers
  receive `Identities` and call `identities.require(PrincipalType.X)`. The
  adapter only bundles credentials; it has no awareness of `source`.

### Composition root

- Reads `authn.yaml`, validates chain names against the flavor pool, and builds
  the ordered registry (`USER -> (google,)`, `BOT -> (bot_token,)`) plus the
  route table. The flavor swaps validator SPIs / plugins via the existing
  `PluginAccessor` + `GATEWAY_RUN_MODE` mechanism. Strategy and runner code stay
  flavor-agnostic.
- `build_authenticator(*, google_transport=None)` and
  `create_app(google_transport=None)` are the test-injection seams (`None` =
  production real Google; an `httpx.MockTransport` for tests). A built-in
  `_DEFAULT_CHAINS` is used when `authn.yaml` is absent.

### CI gate

- Every `/openapi/v1` operation's `x-avernet-security` must declare a
  **non-empty** required-identity set, or the build/test fails (prevents
  route/auth drift, per auth design §8.3 / `docs/arch/ci.enforce.md`).

## Testing Decisions

A good test exercises **external behavior through existing seams, not
implementation details**. This reuses the existing seams (no new test layer;
runner-primary).

- **Primary seam — the runner** (`core.authn.authenticate`, `test_auth_runner.py`):
  `Identities` collection with one `Principal` per required type; required type
  absent → fail-closed (401); a strategy raising `AuthError` is terminal (no
  fallback) — exercised by a fake strategy; `[bot, user]` yields both;
  scope-subset cases removed. Ordered fallback per type is unit-tested with fake
  strategies in the registry.
- **Model seam** (`test_authn_models.py`, `test_bot_principal.py`): `BotPrincipal`
  construction, `type="bot"` discriminator, immutability, serialization;
  `UserPrincipal` has no `scopes`; the `Principal` union round-trips by `type`.
- **Route-table seam** (`test_route_security.py`): YAML `path -> [types]`;
  most-specific rule wins; default `"/**": [user]`; unknown type rejected;
  fail-closed unmatched.
- **Config seam** (`test_authn_config.py`): `authn.yaml` parses to the shipped
  chains (user `[google]`, bot `[bot_token]`); `build_strategy_registry` rejects
  unknown names and wrong `principal_type`.
- **Google strategy unit seam** (`test_google_user_strategy.py`): inject an
  `httpx.MockTransport` that returns a verified user for the `good` token, a 401
  for others, and a 200-without-`sub` for an unparseable case. Assert: no token →
  `None`; verified token → `UserPrincipal` with mapped `id`/`username`/
  `display_name`; 401 / unparseable → `AuthError`. No network.
- **Bot strategy + registry unit seam** (`test_bot_token_strategy.py`,
  `test_bot_registry.py`): mirrors BCS `SessionTokenPlugin` cases — `is_jwt_format`
  (three segments) and `extract_bot_token` (dedicated header wins; non-JWT bearer
  accepted; JWT bearer and empty values ignored); `build` returns `BotPrincipal`
  from the dedicated header **and** from a non-JWT bearer; an unknown / empty
  token → `None` (soft miss); no bot token → `None`. The registry tests
  (`register(bot, token)` indexes the token; `find_bot_by_token` one-step
  round-trip; overwrite semantics; seeded demo bot) cover the
  single-lookup registry shape (in-memory, no network).
- **App seam** (`test_bots_router.py`, `test_groups.py`, via `TestClient`): no
  token → 401; a verified Google access token (MockTransport via
  `create_app(google_transport=...)`) → past auth into the stub handler;
  present-but-unverifiable → 401. Every `/openapi/v1` operation declares a
  non-empty `x-avernet-security`.
- **Contract seam** (`contracts/spi/test_auth_strategy.py`): conformance for
  `GoogleUserStrategy` (with a MockTransport) and `BotTokenStrategy` against the
  `AuthStrategyContract` base (re-uses the contract-test pattern). The
  `AuthStrategyContract` base continues to assert `.principal_type` and
  `build(creds)`.
- **Architecture seams**: layer rules / private-import rules / structure rules /
  ruff rules pick up the new SPI/plugin surface automatically. (Rev 5 removed
  the `BotTokenValidator` SPI and its conformance test; `BotTokenStrategy`
  conformance is covered by the `AuthStrategyContract` contract seam above.)
- **Update, don't delete** the existing tests that referenced `scopes` /
  `delegation` / a single `Principal` / cookie / `UserTokenValidator`. The cookie
  strategy test file and the user-token-validator conformance test are removed.

## Out of Scope

- The deferred `AppPrincipal` / third-party-app identity (auth design §15).
- Verified delegation (`DelegatedPrincipal`, app acting for a verified real user,
  `xoneid`).
- Principal **signing/verification** between gateway and components (§7.1); the
  identity *set* is what gets signed.
- **Component-side** `Principal → domain DTO` projection (axis B, §9) — each
  component owns it; this spec is gateway-only.
- The **scope vocabulary/taxonomy** — removed, not deferred.
- **Resource-level authorization** in components — untouched; the gateway stops
  at AuthN.
- Removing `is_allowed` / `check_permission` from the `AuthPlugin` SPI — kept per
  decision.
- The `identity` group router (bot identity **files** — RULES / SOUL) — unrelated
  to `PrincipalType`.
- Deriving a full `UserPrincipal` for a bot's owner purely from the bot token.
  A `[bot, user]` route requires the request to **present credentials for both**.
- The real `sofa` bot-token registry **and** `sofa`-flavored Google user
  resolution. The `BotTokenValidator` SPI + bare impl land this round; a `sofa`
  Google resolver is a follow-up (the strategy already isolates the userinfo call,
  so swapping the transport/resolver is localized).
- Importing the full BCS OAuth login flow (authorization-URL / code-exchange
  routes, state store, JWT session minting) into the gateway. The gateway only
  *verifies a presented* Google access token (mirrors `get_user_info`); it does
  not host the redirect login endpoints.
- The cookie/session user-identity strategy — removed (rev 4).
- The `source` header and the `UserTokenValidator` SPI — removed (rev 4).
- Any backend/engine wiring behind the v1 endpoints — handlers remain stubs.

## Further Notes

- **`token` on `BotPrincipal`** carries the bot's raw credential into the
  forwarded identity. This deviates from the auth design's "components never see
  raw credentials" principle. Flag for security review: components must treat
  `BotPrincipal.token` as a secret. If a short-lived verified handle is later
  preferred over the raw token, the field can be repurposed without changing the
  type shape — but for now it is the raw credential.
- **`tenant` on `BotPrincipal`** preserves the "every Principal has a tenant"
  invariant; resolved from the bot's owner. If exactly the three named fields are
  wanted, `tenant` can be dropped — a review point.
- **Google access token as the user credential.** The gateway verifies the
  presented token against Google's userinfo endpoint and trusts the returned
  `sub`/`email`. It does not validate token audience, expiry beyond what Google
  reports, or scopes; those are Google's responsibility at issuance. A bad or
  revoked token yields a non-200 from Google → `AuthError` → 401. Operators must
  ensure the gateway can reach Google's endpoint (network egress) and that
  `httpx` timeouts bound the call.
- **No cookie, no `source`.** Rev 4 removes both. User identity is purely a
  Google-verified access token. If a session/cookie front door is needed later,
  it belongs upstream (e.g. BCS's OAuth login flow that mints a credential the
  caller then presents to the gateway), not in the gateway's authn chain.
- **Test hermeticity.** The Google strategy takes an `httpx.MockTransport` via a
  `transport` seam; no test contacts Google. The composition/app seams
  (`create_app(google_transport=...)` / `build_authenticator(google_transport=...)`)
  thread it through.
- **Chain mechanism retained.** Even though each shipped identity type is
  currently single-plugin (`google`, `bot_token`), the ordered-chain runner and
  `authn.yaml` config remain, so adding a second user-identity plugin later (e.g.
  another OAuth provider) is a config + plugin addition, not a runner change.
- **`AuthPlugin` retention.** `is_allowed` / `check_permission` stay on the SPI
  (component-facing) even though the gateway no longer uses them; this keeps the
  gateway-focused change from rippling into the component auth contract. The
  cookie strategy's former use of `get_login_user` is gone; the method is kept on
  the SPI, unused by the gateway's authn flow, for component use.
- **Publishing.** This spec lives at
  `specs/2026-07-24-gateway-multi-identity-and-bot-principal/spec.md` per the
  repo's spec-doc convention. Publishing to the project issue tracker (GitHub
  on `inclusionAI/Avernet`) with the `ready-for-agent` triage label remains
  pending (reporter chose to keep the spec file, not publish).
