# Invitation membership preflight

## Record

- Status: implemented and focused verification complete; unrelated baseline failures and unrun checks are documented below. Design approved in the user conversation.
- Owners: BCS V1 invitation application and frontend-nextgen workspace invitation page.
- Scope: a read-only, authenticated invitation preflight followed by automatic navigation for existing Human members. Nonmembers retain explicit consent and message-view-scope selection.
- Architecture: [constitution](../../../../docs/arch/arch.rules.md); no new architectural exception or ADR. Existing [invitation contract](../../api-contracts/v1/openapi/invitations.yaml) owns the public API.

## Decisions and acceptance

- Add `GET /openapi/v1/collaboration/invitations/{token}` with `target_type`, `target_id`, `group_id`, and `already_joined`.
- Authenticate the User and verify token signature, version, target type and expiration. Expired links remain invalid, including for existing members.
- Membership means the authenticated User's `human_{id}` is a Human participant in the target itself. Bot management, public visibility, originator status and parent-Group membership do not substitute for target membership.
- Use strict Group/Session authorization snapshots. Missing targets are 404; snapshot failures propagate as 500 rather than returning `already_joined=false`. Return no member lists, messages or credentials.
- Preflight never creates an actor, joins, emits events, changes modes or changes message scope. Acceptance continues to revalidate independently.
- Frontend loads preflight before showing confirmation. Existing members navigate with history replacement; Session navigation includes its parent Group without another detail request. Nonmembers must click confirm. Authentication, invalid-token and transient failures remain distinct.
- No database migration, new dependency, token-format change or automatic acceptance.
- Rollout is additive for existing clients: deploy BCS before or together with frontend-nextgen. The new frontend requires preflight and does not silently fall back to unchecked confirmation when an older backend returns 404.

## Validation and delivery

Verification on 2026-11-17:

- `cargo nextest run -p bcs-app-invitation -p bcs-service-api --no-fail-fast`: 344 passed, including 10 read-only preview regressions and existing invitation/friendship tests.
- `cargo nextest run -p bcs-api-http --no-fail-fast`: all HTTP test targets compile; 342 passed, including 12 invitation route tests.
- `cargo test -p bcs --lib config --quiet`: 159 passed, 204 filtered; includes compilation of production snapshot wiring.
- Frontend focused controller/service/hook/page/route regressions: 14 suites, 121 passed. Open Core boundary check, TypeScript check and affected-file ESLint passed.
- Focused public OpenAPI inventory/preview/export tests: 8 passed; validator confirms 81 public operations.
- Entire OpenAPI suite: 174 passed, 5 failed. The same five tests fail against an isolated archive of HEAD before these changes: Bot-self internal validator, internal operation count/inventory, Group detail authorization wording and Session participant-mode authorization wording. These unrelated baseline contracts were not rewritten.
- The aggregate architecture run found existing dependency/import/naming/conformance failures in unchanged declarations. Bootstrap config tests and static port-purity/forbidden-symbol/store-boundary checks passed. The aggregate run was stopped during unrelated broad R25 scanning; workspace-wide conformance discovery and the remaining aggregate checks were not completed. No gate or hook was disabled or weakened.
- Independent diff review found no actionable issues; preexisting extracted function bodies were compared against HEAD. A second review verified the read-only identity helper and ran its three closest suites (19 passed).
- `git diff --check` and changed/added source line-count checks passed; all affected source files are below 1,000 lines.

Not run: browser end-to-end flow, frontend production build, real SQL deployment/load validation, and the complete Rust workspace suite. Existing snapshot stores are reused without SQL/schema changes; the flow document records the traced two-query Group and three-query Session cost rather than claiming measured production latency.

Delivery: BCS is prepared as a standalone commit/branch targeting `dev`; the frontend companion is prepared separately against `REL20261117`. Git and the resulting PRs record publication status; no deployment is claimed. Durable navigation/consent rules are in the [cross-app flow](../../../../docs/flows/collaboration-invitation.md).

## Changed-line coverage follow-up (2026-11-17)

The BCS job in Unit Tests run `44026200654` passed all 8,678 tests and overall
line coverage (89.24%), but failed changed-line coverage: 155/185 = 83.78%.
The unchanged `cov_gate.py` reproduces that failure against the downloaded CI
JUnit/Cobertura artifacts and base `f3f3267ea884495ce904ced1bc6444b801c69294`.
All 30 missed changed lines were in the extracted invitation module, including
rare legacy join error projections and absent Session-snapshot wiring.

Added assertions for each join-failure category and its stable V1 message/code,
including storage and login failures remaining errors rather than success. A
separate integration regression verifies missing Session-snapshot wiring returns
an internal error without creating a Human. Product behavior, HTTP/service
contracts, coverage exclusions, thresholds and CI scripts are unchanged.

Fresh instrumented invitation tests: 66 passed; the invitation module covers
149/165 instrumentable lines (90.30%). These tests hit 14 exact lines missed by
the original CI report. Combining that evidence with the original CI baseline
would yield 169/185 = 91.35%; this is an explicitly scoped comparison, not a new
full-workspace coverage result. The pushed CI rerun remains the authoritative
full-workspace gate result.

## Live E2E coverage follow-up (2026-11-17)

Singlebox run `44030670688` failed the separate runtime changed-line gate at
10/185 = 5.41%. Its artifacts confirm that the default suite did not exercise the
V1 invitation application; its older sharing journeys use legacy invite routes.
The unchanged E2E gate reproduces that result from the original runtime report.

Added a default `stories` entry covering real authenticated Group/Session sharing,
read-only nonmember/member preflight, explicit and idempotent acceptance, scope
and posture preservation, Session-only membership, membership revocation,
forbidden sharing, invalid/expired tokens and deleted targets. The story uses
HTTP for registration, state changes, inspection and cleanup. It neither edits
persistence nor calls application code or synthesizes invitation signatures.

Focused verification used a newly built instrumented BCS with an isolated SQLite
database and loopback port. The registered shell story passed 168 live HTTP
assertions. After graceful server shutdown, only that process's runtime profiles
were merged and exported with the pinned LLVM tools; no unit-test profiles were
included. The unchanged `e2e_cov_gate.py`, against the original PR base and the
same 185 instrumentable changed lines, reports **144/185 = 77.84%**, passing the
existing strictly-greater-than-65% threshold. The invitation application accounts
for 124/165 covered changed lines and its HTTP adapter for 18/18.

Shell syntax, Python compilation, Ruff, default runner registration and diff/file-size
checks passed. Coverage-helper/runner-phase regressions passed 38 tests and 8
subtests. This focused run is not a fresh all-module Singlebox run: global
line/method, CLI/endpoint inventories and all other stories remain the CI
aggregate's responsibility. No gate, exclusion, production behavior or frontend
code changed.
