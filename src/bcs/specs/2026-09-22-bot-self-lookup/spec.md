# Internal Agent identity Bot self lookup

## Scope and authority

BCS owns `GET /api/v1/collaboration/bots/me`. Its HTTP authority is
[`internal/bot-self.yaml`](../../api-contracts/v1/internal/bot-self.yaml),
registered only in [`internal.yaml`](../../api-contracts/v1/internal.yaml).
It has no `/openapi` alias. The change follows the existing
[BCS layering rules](../../CLAUDE.md#architecture-layering) and
[architecture constitution](../../../../docs/arch/arch.rules.md); it introduces
no new architecture decision or storage migration.

An Agent can have a valid identity before registering a Bot. The
lookup therefore requires `Authorization: Bearer <identity_token>`, verifies
the token on the server, and uses only the resulting trusted `agent_code`.
It does not rely on decoded but unverified JWT claims or a Gateway Bot
Principal. Query parameters such as `bot_id`, `agent_code`, and `provider_id`
are ignored and cannot select another Agent. Refreshing a token leaves the
registration result unchanged when the verified Agent identity is unchanged.

## Response and persistence semantics

Both registration states return HTTP 200 and the existing V1 envelope:
`code: 20000`, `message: "OK"`, `request_id`, and `data` containing:

- `registration_status`: `registered` or `unregistered`.
- `identity`: exactly `{ "agent_code": "agent-001" }`.
- `bot`: null when unregistered; otherwise exactly `bot_id`, `name`, `summary`,
  `provider_id`, and `provider_bot_ref`.

The source of registration is persisted, non-deleted, non-Human Bot rows in
the current BCN environment. Bot liveness or disabled status does not cancel
registration. Two matching rows produce a conflict instead of choosing the
first. Database and decoding failures remain errors.

The dedicated `agent_code` column takes precedence. For historical rows with
a null column, the existing `bot_info.agent_code` identity is retained; both
sources participate in duplicate detection. This does not backfill or mutate
old records.

Legacy Bots can have null `name` or `summary`. Provider ID and reference come
from persisted affiliation. Legacy delivery binding metadata is used only
when both Bot affiliation columns are null, including a disabled binding.
An unaffiliated legacy Bot returns both Provider fields as null; the API does
not invent a Provider from the token. It never returns raw identity, runtime,
or Provider credentials. It does not register, activate, update, or delete a Bot.

Every response, including authentication and server errors, carries
`Cache-Control: no-store`.

| HTTP | `data.error_code` | Meaning |
| --- | --- | --- |
| 401 | `unauthenticated` | Missing, malformed, invalid, or expired Bearer token |
| 403 | `forbidden` | Verified identity is not the required Agent identity |
| 409 | `agent_registration_conflict` | Multiple current-environment registrations |
| 500 | `internal_error` | Database unavailable or persisted data cannot be decoded |
| 502 | `agent_identity_unavailable` | Identity SDK/service unavailable or verification execution failed |

## Architecture and cost

Gateway has a method-specific empty identity rule for this exact path. It
forwards Authorization, strips caller-supplied `X-Avernet-Principal`, and does
not sign an empty identity set. BCS mounts this handler outside Gateway
Principal middleware and performs mandatory Agent identity authentication in the
application service. Other paths retain their existing identity policies.

The HTTP adapter calls the versioned application Service API. The application
orchestrates a typed identity-verification outbound port and Bot Core; Bot
Core calls its repository port, and Bot Store owns SQL and projection. The
composition root injects the resolver. The public contract does not name an
authentication vendor, require its token format, or expose its SDK types or
private endpoints. Deployment-specific verification and claims mapping remain
in the deployment-owned plugin. Existing diagnostic routes keep their behavior.

The response uses `identity`, the HTTP DTO uses `AgentIdentityDto`, and the
security scheme is `AgentIdentityBearer`. These replace implementation-specific
names in the uncommitted draft of this new route; no legacy response alias is
published. The `AgentIdentityPort` contract and implementation selection remain
unchanged, including fail-closed behavior when no verifier is installed.

Each accepted credential costs one identity verification followed by one
bounded registration DB read (`LIMIT 2`, at most two candidate rows returned).
The Provider projection is part of that read. No transaction is held across
identity verification. Authentication failures perform no registration read;
storage failures are returned without retries. There are no writes or new
caches. Concurrent requests each incur this cost, including repeated reads
after a failure; this change establishes bounded query shape, not load-test
evidence about connection-pool capacity or the rows examined by the database.
The compatibility predicate may examine unbackfilled JSON-identity rows in the
current environment; `LIMIT 2` bounds results, not the number of rows examined.

## Validation

After making the new HTTP contract implementation-neutral, reran 154 OpenAPI
tests, five HTTP self-route tests, four dependency-boundary tests, 88 public
Gateway configuration/publication tests, and 21 deployment Gateway tests; all
272 passed. The exact-envelope test first rejected the old implementation-named
response key. Both Gateway snapshots pass compatibility checks against HEAD;
all previously committed operations are structurally unchanged. A diff scan
finds private authentication names only in the new forbidden-dependency guard,
not in the new production source, contract, configuration, or documentation.
Historical authentication coupling elsewhere is outside this change's scope.

Contract tests must cover internal-only publication, Bearer security, both
registration states, legacy nullable fields, rejection of credential fields,
error vocabulary, and no-store headers. Runtime tests must cover the actual
Internal route, missing/invalid tokens, verified but unregistered Agents,
registered/offline/disabled Bots, refreshed tokens, ignored identity selectors,
SDK/DB failures, and ambiguous mappings. Persistence tests must exercise
environment/deletion/Human isolation, bounded query count, and strict decoding.

Gateway tests exercise its actual Authenticator, IdentityChain, and
HttpxForwarder against an ASGI upstream. They verify Authorization passthrough,
no identity-chain invocation or signed/forged Principal, unchanged response
envelopes/status, and no-store propagation.

Gateway catalogs are generated from the contract. Existing snapshots have
unrelated historical drift, so publication adds only the generated self-lookup
operation and its Bearer security scheme to each internal snapshot, using the
normal compatibility gate. Public catalogs remain unchanged.

Completed checks for the contract and Gateway boundary:

- `pytest src/bcs/tests/openapi -q`: 154 passed. The four new self-lookup
  contract tests first failed on the missing operation before it was added.
- `validate_openapi_contract.py --root src/bcs/api-contracts/v1 --entrypoint
  internal.yaml --path-prefix /api/v1/collaboration/`: 23 operations validated.
- Gateway publication/catalog tests: 18 passed; default route-security tests:
  82 passed. Both internal schema updates passed the compatibility gate, and
  a structural comparison confirmed all existing operations were unchanged.
- OCB Gateway config and forwarding tests: 21 passed. Its installed SOFA
  fixture emits four existing Pydantic deprecation warnings.
- Focused Ruff checks and `git diff --check` passed. Changed test source files
  remain below 1,000 lines.

Completed implementation checks:

- `cargo test -p bcs-api-http -p bcs-app-bot -p bcs-bot-store -p bcs-bot
  -p bcs-service-api`: 758 passed, zero failures. Four preexisting external-DB
  integration tests were ignored by their declarations (registration, token
  persistence, streaming recovery and SQL-injection integration tests).
- The suite includes real Internal Router envelopes/no-store/authentication,
  application-to-Core-to-SQLite behavior, refreshed credentials, legacy JSON
  identity, mixed duplicate mappings, strict row decoding and MySQL coercion
  driver fixtures. No live MySQL service was used.
- `cargo check -p bcs --lib` passed. The focused bootstrap test verifies a public
  build without an identity plugin fails closed. Four HTTP boundary tests pass,
  including a permanent guard against private authentication dependencies in the
  public workspace. Cargo metadata also confirmed no such references among
  the 99 public workspace packages.
- The deployment-owned verifier suite passed 25 tests with `--offline --locked`,
  using fake clients and raw response fixtures only. It covers strict remote
  denial/failure distinction, malformed successful responses, SDK response
  precedence, initialization, blocking-worker failure, refresh and log redaction.
  The public interfaces were supplied from this Avernet worktree through a
  temporary dependency overlay. No deployment submodule/gitlink changes were made.
- Independent review findings (legacy identity omission, MySQL JSON coercion,
  malformed authentication response and SDK response precedence) were fixed and
  re-reviewed; no findings remain from that review.
- `git diff --check` passed in both repositories. New source files remain below
  1,000 lines. The existing oversized public `server.rs` and Bot Store `lib.rs`
  received only necessary wiring/delegation; no unrelated split or allowlist
  change was made.

The full architecture gate was attempted but is not green: the unchanged
`check-deps.sh` fails at line 36 with an unbound variable on this host, and
existing import/conformance violations are reported outside this change. The
subsequent broad workspace-discovery compilation was stopped after those
failures; focused changed-boundary tests above passed. The deployment's direct architecture
metadata check is separately blocked by its preexisting public-submodule
manifest/internal-lock version mismatch; unrelated lock downgrades were not
applied. No gate was weakened.

These checks do not establish live identity-service/MySQL availability, full-stack deployment
behavior, or sustained-load capacity. No real identity token was sent, and no
deployment or merge was performed by this task. Commit and push hooks are
skipped at the user's request; the validation evidence above predates submission.
