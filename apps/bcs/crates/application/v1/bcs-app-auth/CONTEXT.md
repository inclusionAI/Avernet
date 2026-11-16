# bcs-app-auth Context

## Provides

- `AuthApplicationService`, the transport-neutral implementation of the
  service-api `AuthService` contract (the Task 11 atomic swap replaced the
  legacy `AuthService` surface with it and restored the stable
  `AuthService` trait).
- `login_urls`: the browser-bound authorize-URL batch. One pending-login
  batch per initiating browser, provider chain (subsequence) order
  preserved, exactly one `SetLoginChallenge` cookie instruction whose
  nonce never appears in the URL list, in any JSON body, or in any log.
- `complete_login`: the binding-complete callback order — validate
  enabled provider + required code, consume the pending state, exchange
  the code, install the strict session. A failed consume replays as an
  error with NO cookie changes (the initiating browser's batch stays
  alive); anything after a successful consume always carries
  `ClearLoginChallenge`; full success additionally sets the session and
  returns the configured same-origin redirect.
- `current_user`: projection of an already-authenticated Human caller
  only. `provider_label`/`avatar` are trusted-adapter display data, never
  authority; no second credential chain runs here.
- `refresh_session`/`logout`: strict session rotation and (idempotent)
  revocation through the session port, with credentials arriving only as
  the required `PresentedSession` struct — never a `Cookie` header.
- The exact-callback byte-identity join `{callback_base}/{provider}`:
  both the issue side (`login_urls`) and the consume side
  (`complete_login`) derive the callback through ONE shared
  normalization helper, so a genuine callback is never rejected for
  side-specific re-normalization (plan §8.4).
- Redaction (plan §8.4 credential-log red line): the orchestration logs
  only stable categories (`ApplicationError::code()`), configuration
  names, and flow outcomes — never a nonce, state, code, token,
  authorize URL, or a port error's message text. `tests/log_redaction.rs`
  pins this with sentinel-laden fakes under a DEBUG capture so an empty
  log can never fake a pass.

## Consumes

- `bcs-service-api` application contracts (`AuthService`,
  `AuthFlowReply`, `CompleteOAuthLogin`, `BuildLoginUrls`,
  `AuthenticatedUserQuery`, `PresentedSession`, `ApplicationError`) and
  the outbound `port::oauth` ports (`OAuthProviderPort`,
  `OAuthSessionPort`, `PendingOAuthLoginPort`).
- Utility crates: `async-trait` (port traits), `time` (unix clock for
  TTL/install instants), `tracing` (category-only events).

## Allowed dependencies

- `service-api/*`
- Pure utility crates (`async-trait`, `time`, `tracing`, `serde`)

## Forbidden dependencies

- `bootstrap/*` (composition stays outside)
- `adapters/*` (no HTTP/WS types ever cross this boundary)
- Concrete `plugins/*` or `plugin-api/*` in production code (all
  provider/session/pending behavior arrives through injected ports;
  bootstrap's `api_auth_ports.rs` owns the translation)
- Direct environment-variable reads, filesystem access, or ad hoc SQL —
  the crate configures nothing, it is configured (redirect path and
  flow namespace are injected; secrets and provider selection never
  enter this layer)

## Configuration

- Bootstrap injects the three ports, the configured same-origin success
  redirect path (`api.auth.success_redirect_path` shape), and the login
  flow namespace (`"v1"` for the V1 entry; the legacy entry keeps its
  own). The clock is `time`-based unix seconds.
- This crate must not select implementations or inspect environment
  variables; a change of provider, store, or redirect is a bootstrap
  wiring change, not a code change here.

## Runtime ownership

- The crate owns login/session use-case ORDER and the cookie
  instruction contract (what must change on success/rejection), nothing
  else. Cookie/Headers parsing, Origin checks, provider allowlisting,
  and secrets stay in delivery and bootstrap; the strict engine keeps
  signature/CAS semantics; the pending store keeps match-and-invalidate
  semantics.
- Fault taxonomy: node failures map to the application-level
  `Unavailable` category (HTTP 503 / 50300 with the stable
  `auth_dependency_unavailable` code family); a lost login race maps to
  `Conflict` (409-side); denials map to `Unauthenticated`/`Forbidden`.
  Raw third-party or store error text is never propagated upward.

## Tests

- `cargo test --package bcs-app-auth` — all integration suites:
  `login_flow` (binding order + cookie instructions), `session_flow`
  (projection/refresh/logout), `conformance` (every port method reached
  with recorded arguments; rejected paths never touch the next port),
  `conformance_auth_service` (R25 central
  `bcs-test-support::contract::application::auth_service_contract_tests`
  over the real service), `log_redaction` (DEBUG-captured category-only
  output). Ports get fake doubles in `tests/support/` that record every
  call.
- `cargo check --package bcs --all-targets` keeps the bootstrap bridge
  wiring compiling.
