# OAuth live-process E2E

Run `bash apps/bcs/scripts/oauth_e2e_coverage.sh` from the repository root.
Prerequisites: Python 3 (standard library only), OpenSSL, Rust, cargo-llvm-cov
and LLVM tools. No provider credentials, public network, database service or
company infrastructure is required.

The canonical `e2e_coverage.sh` runs this companion after the shared-stack and
caller stories. Failures propagate to Singlebox. Runtime profiles join the same
LLVM profile pool, and `oauth_e2e.json` is copied to the canonical BCS reports
and checked by `verify_singlebox_coverage_artifacts.py`. Thresholds are unchanged and no production paths are excluded; the formerly unreachable identity memory
repository is now exercised and is no longer on the skip list.

## Boundary and isolation

The required-features `bcs-oauth-e2e` binary runs the regular config loader and
SQLite server bootstrap. Its `--memory` mode runs the existing in-memory server
constructor. It exposes no extra routes and never seeds tokens or invokes auth
use cases directly. All identities and current sessions are installed by real
browser-bound callbacks. Production libraries and their compiled provider URLs
are unchanged.

A loopback HTTPS CONNECT simulator implements GitHub, Google, WeChat and Alipay
provider HTTP protocols, including one-use authorization codes, callback checks,
userinfo, token/userinfo failures, and signed Alipay responses. It never forwards
traffic. Only an explicit destination allowlist is accepted. Every run creates
an ephemeral CA and leaf certificate using OpenSSL; only child-process
`SSL_CERT_FILE`/`SSL_CERT_DIR` and the test browser trust it. TLS verification
remains enabled. Proxy and root-store environment settings are scoped to the
child, and loopback BCS calls bypass proxies. Keys, cookies and authorization
codes live only in a temporary directory/in memory and are not artifacts.

## Acceptance journeys

- SQLite and memory: all four providers log in, project the current user,
  refresh across V1/legacy entries, invalidate old tokens, revoke refreshed
  descendants, and protect new logins from stale logout.
- Browser binding: two jars, rotated challenges, duplicate/sibling callbacks,
  missing codes, wrong state/provider, and failed exchange/userinfo cleanup.
  Counters prove rejected foreign-browser callbacks never call token exchange.
- Credential/Origin boundaries: empty, duplicate, tampered, expired, foreign-env,
  legacy and superseded tokens; protected reads/writes, user-context mismatch,
  untrusted origins and first-success chain precedence.
- Deployment compatibility: absent `[api.auth]`, gateway-only, gateway-first,
  OAuth-only/disabled provider and independent explicit versus legacy origins.
- Default environment: with all environment-selection variables absent, both
  entry families retain `local`, the same internal user ID, and cross-entry
  refresh/revoke behavior over both SQLite and memory.
- Rollback: removing `[api.auth]` restores the compatibility OAuth facade;
  explicit gateway-only closes V1 only; disabling legacy OAuth too closes both.
  Disabled-provider refresh is rejected without a new cookie, while legacy
  refresh and logout remain governed by their independent contracts.
- Pending-state capacity: fill the public URL endpoint to its documented limit,
  reject excess issuance with 503 without a cookie, then complete an existing
  login to prove saturation neither evicts live batches nor prevents recovery.
- Concurrent V1/legacy refresh: exactly one HTTP request wins the CAS; stale
  logout still revokes its rotated descendant.
- BCS-only candidate discovery: two actual OAuth browser sessions register Bots,
  search as Human/managed Bot, enforce cross-viewer visibility and reject foreign
  perspectives. Pending requests become approved Connect edges only after the
  target manager accepts them. Lists and semantic search agree on `is_friend`,
  including a friend subsequently made private. A loopback BCSFuse stub checks
  real outbound recall scopes and supplies deterministic ordering, empty recalls
  and HTTP failures; a separate provider-disabled process verifies name fallback.
  Registration and relationship state are created through HTTP, never direct SQL
  or Core calls. These processes use the existing OAuth runtime profile pool and
  fail the companion on assertion errors, without changing coverage thresholds.
- Operator startup: malformed source tables/values, unknown/duplicate sources,
  missing fields, invalid URLs/origins/redirects, secret lookup/key failures,
  and JSON/TOML native-value guards must fail before any server is published.

For non-instrumented diagnosis:

```sh
cargo build --manifest-path apps/bcs/Cargo.toml -p bcs --features test-utils --bin bcs-oauth-e2e
python3 apps/bcs/scripts/e2e-test/oauth/story.py \
  --binary apps/bcs/target/debug/bcs-oauth-e2e --report /tmp/oauth-e2e.json
```

These tests do not claim to validate actual provider accounts/consent screens,
MySQL, or deterministic CAS scheduling. The existing provider, store and race
conformance suites remain complementary; their unit-test profiles are never
merged into the E2E pool.
