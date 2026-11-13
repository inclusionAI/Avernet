# AgentCard mutation to Fuse catalog synchronization

## Record

- Status: implemented locally; focused verification recorded in [delivery](delivery.md).
  Not committed, integrated by gitlink, deployed or release-gate approved.
- Date: 2026-11-13.
- Owner: public BCS Core, bootstrap and Fuse client. Embedding hosts own dependency wiring only.
- Public source baseline: `29912c27518c1678a88b1c67a577d314a34a380e` (fetched `origin/dev`).
- Supersedes the earlier proposed structured catalog-event/context propagation design.
- Implementation sequence: [plan](plan.md); acceptance checklist: [tasks](tasks.md).
- Governing boundaries: [architecture constitution](../../../../docs/arch/arch.rules.md),
  [BCS layering](../../CLAUDE.md), [catalog contract](../../docs/bcsfuse-catalog.md),
  [observation contract](../../docs/observability/request-operation-logging.md),
  [app decision index](../../docs/adr/README.md). This proposal does not supersede an ADR.

## Problem and current evidence

AgentCard mutation commits currently do not notify the existing Fuse catalog queue:

- [A2aProviderRegistrationCore](../../crates/services/bcs-bot/src/core/a2a_provider_registration.rs)
  atomically creates Bot, ownership and Card but has no catalog change dependency.
- [A2aBotCore](../../crates/services/bcs-bot/src/core/a2a_bot.rs)
  returns after successful replace/CAS without a catalog notification.
- [Public assembly](../../crates/bootstrap/bcs/src/server/a2a_wiring.rs)
  creates both cores without the catalog port. The queue already exists in
  [server infrastructure](../../crates/bootstrap/bcs/src/server/server_infrastructure.rs).
- [V1 REST](../../crates/adapters/http/bcs-api-http/src/v1/a2a/management.rs)
  calls `A2aApiService`, which delegates to `A2aBotManagementService`.
  [Legacy REST](../../crates/adapters/http/bcs-http/src/routes/a2a_management.rs)
  calls that management service directly. They are separate HTTP adapters, not the same handler.
- An embedding host can assemble an independent management/core instance using the
  same public crates. Sharing the SQL database does not share its injected ports.
- [Fuse Card HTTP client](../../crates/external-clients/bcs-fuse-client/src/cards.rs)
  uses `send_traced()` and validates saved/deleted acknowledgments, but has no
  explicit Card write start/success/failure summary. Optional network tracing is
  not equivalent to an always-visible business acknowledgment log.
- [Queue worker](../../crates/services/bcs-fusion/src/core/card_sync.rs)
  has retry/exhaustion logs but no successful publication summary.

These are static source observations, not proof of the version running on any host.

## Goals

1. Notify the existing catalog port after committed AgentCard registration,
   update (including visibility-only update), explicit refresh and configuration.
2. Prioritize `/api/v1/collaboration/a2a/agents`; preserve legacy REST behavior.
3. Let embedded RPC management instances use the same public implementation and
   the same consumed queue, without duplicating synchronization policy in the host.
4. Log useful identifiers at the notification call site and actual Fuse HTTP
   request boundary, using only information already available there.

## Non-goals

- No change to `CatalogChangePort::agent_changed(&str)` or `group_changed(&str)`.
- No metadata in queue entries; no new event DTO, request-context/task-local framework,
  source/operation propagation contract or tracing SDK dependency for logging.
- No mandatory trace ID, request ID, provider ID or provider ref at every stage.
- No outbox, restart replay, ordering/fencing redesign, global queue or periodic backfill.
- No duplicated HTTP or retry implementation in REST/RPC adapters.
- No proto changes, external response-envelope changes or database migration.
- No change to the Agent discovery projection or Group publication policy.
- No automatic historical backfill or Fuse index-completion polling.

## Required behavior

### Core notifications

Inject the existing optional `Arc<dyn CatalogChangePort>` into both A2A cores
through `with_catalog_changes`, preserving existing constructor compatibility.

| Core boundary | Notify when | Do not notify when |
| --- | --- | --- |
| A2A registration | Composite create transaction succeeds | Validation/auth/duplicate checks or transaction fail |
| `replace_card` | Store returns the committed replacement | Store fails |
| `commit_card_with_visibility` | CAS returns true | CAS returns false or errors |
| `commit_card` | CAS returns true | CAS returns false or errors |

Each successful commit emits exactly one notification from its owning Core.
Do not add a second notification in REST, V1 facade or RPC handlers.
Emit before a later response/read-back failure could suppress notification for
an already committed write. DB failures remain errors; Fuse failures do not
roll back committed Bot/Card state.

The explicit `refresh_agent_by_id` path commits even when fetched content is
unchanged, and therefore notifies. Existing background `refresh_due` no-op
optimization remains: a no-write refresh does not introduce a notification.
A changed background refresh uses the same commit hook. An inline Card still
cannot be URL-refreshed; its existing error must not enqueue anything.

### Shared queue and lifecycle wiring

The public bootstrap owns queue creation/selection, client configuration and worker
lifecycle. Enable only with `bcsfuse.enabled=true` and `api_version=v2`.
Public `build_card_management` injects the selected queue into both cores.

Provide an additive embedding extension allowing an externally supplied
`Arc<CardChangeQueue>` to be consumed by the public bootstrap and its existing
`FuseCardSyncLifecycle`. Reuse that queue for all existing Agent/Group producers.
The existing startup entry delegates with default extensions and preserves behavior.
Do not create a second worker or a second unconsumed queue. If a queue is explicitly
supplied while Fuse V2 is disabled, reject the incompatible startup combination;
normal startup without an injected queue retains the existing disabled/V1 behavior.

An embedding host with a separately assembled RPC management instance needs only:

1. Select/create one queue at composition time when Fuse V2 is enabled.
2. Pass its `CatalogChangePort` view to its two A2A cores.
3. Pass the same queue through the public bootstrap extension.
4. Update its public dependency revision and verify the real assembled path.

The host MUST NOT add `agent_changed` calls, Fuse HTTP requests, retries,
projection rules or Card logging policy to its RPC handlers. This is a one-time
wiring adaptation, not a second implementation. A public-only patch plus the
old host assembly is insufficient: its newly optional dependencies stay absent.
Do not hide this gap through a process-global singleton or database polling.

### Logging before notification

At the shared Core call sites, emit an INFO structured event
`agent_card.catalog_change_requested` when the port is present, immediately before
calling `agent_changed(bot_id)`. The wording means notification requested, not
remote saved, indexed or even a durable enqueue acknowledgment.

Fields available from the committed record/binding:

- `bot_id`, `provider_id`, `provider_bot_ref`, `revision`.
- `mutation`: actual Core boundary, e.g. `register`, `replace_card`,
  `commit_card_with_visibility`, `commit_card`. Do not guess update versus refresh.
- Sanitized bounded `bot_name`, if already locally available; no Card body.
- Existing `request_id` when nonempty. Existing trace correlation only if already
  available through an approved context; otherwise omit it.

No extra database reads, tracing SDK dependencies, RPC-handler edits or context
framework solely to fill missing log fields. In particular, a Triple request ID
in its existing RPC digest need not also appear in the public Core log.

### Logging at actual Fuse HTTP calls

Add logs inside `FuseClient::put_card` and `delete_card`, not only queue/worker logs.
Use `target="bcs_observation"`, matching the existing observation output routing.
Do not alter generic `Operation` records or their no-distributed-trace contract.

| Event | Boundary and fields |
| --- | --- |
| `fuse.card.write_started` | Before `send_traced`; resource ID, HTTP method, fixed route template; PUT resource type, bounded name, skill count, known visibility labels |
| `fuse.card.write_succeeded` | After HTTP status and body acknowledgment validation; resource ID, method, duration, actual status; PUT `saved=true` and returned `card_version`, DELETE `deleted=true` |
| `fuse.card.write_failed` | Request/status/decode/negative-ack failure; resource ID, method, duration, sanitized fixed error category and actual status if known |

Start/success use INFO; failure uses WARN. Existing worker exhaustion remains ERROR.
Validate URL/ID before claiming HTTP dispatch. A pre-dispatch validation failure
may log a failure with stage `validation`, but must not claim a request was sent.
Timeout/network failures have no fabricated HTTP status. A 2xx `saved=false`,
`deleted=false`, or malformed body is failure. Do not discard the returned Fuse
`card_version` from the success log, and do not equate it with BCS Card revision.

Do not alter `CardWriteRequest`, response types or client signatures just to log
provider metadata. Those fields are not normally present in the current discovery
request. Record provider ID/ref upstream, and correlate with the resource ID and
time window. Background request/trace IDs may legitimately be absent.
There is no guaranteed one-to-one request-to-HTTP mapping: the queue coalesces
changes and retries generate multiple attempts. Per-commit logs are not an audit ledger.

Security: allowlist fields; bound and sanitize strings; never log full Card JSON,
full descriptions, skill descriptions, extensions, owners, credentials, raw headers,
URLs with secrets/query/fragment, or upstream response bodies. Do not serialize
request/error objects with `Debug` or unfiltered `Display`.

### Payload scope

Continue publishing `agent_card(BotControlPlaneRecord)` through the existing
Fuse V2 Card endpoint. This is a discovery projection, NOT a byte-for-byte copy
of persisted A2A AgentCard JSON. Triggering synchronization does not imply all
A2A skills/interfaces/extensions are newly projected. If that fuller payload is
required, it needs a separate projection contract; it is not silently added here.

## Compatibility, load and operational limits

- REST responses, Triple wire contracts, authorization, CAS and fetch rules stay unchanged.
- No logging-only DB reads or new write transactions. Notifications are in-process.
- Each drained Agent attempt retains the existing current-state lookup (at most
  one control-plane row for this ID) and at most one HTTP write. Query-count tests
  must check the actual repo rather than treating all repo calls as one SQL call.
- Existing batches of up to 100 identities, serial HTTP and bounded retry budgets
  remain. New producers intentionally add outbound traffic; this is not zero cost.
- No HTTP request is issued while holding a database transaction or queue lock.
- Fuse saved acknowledgment and search/index readiness remain different outcomes.
- Disabled/V1 integration is not reported as successful V2 synchronization.

## Acceptance

1. Real DB-backed V1 REST register/update/refresh each produces a Core notification
   and, in isolated non-coalesced success cases, one stub Fuse PUT.
2. Legacy REST smoke checks use the same core behavior without contract changes.
3. Embedded RPC register/update/refresh reach public Core and the same active queue;
   a test must cover composition, not merely a mocked management trait.
4. Validation/auth/transaction/CAS/fetch failures before a successful commit produce
   no notification and no Fuse write. A later response failure must not lose the
   notification for an already successful commit.
5. Successful explicit unchanged refresh still notifies; automatic no-write refresh
   retains its no-notification behavior.
6. HTTP 2xx with valid ack logs success; 5xx, timeout, negative ack and invalid JSON
   log failure. Log capture asserts presence of useful IDs and absence of secrets.
7. Test the observation file target filter at INFO, not just console capture.
8. Rapid updates may coalesce. Test eventual current projection; do not require
   one remote call per inbound request or claim exactly-once delivery.

## Delivery and rollback

Implement public behavior first, then update embedding-host dependency/wiring.
Do not declare Triple fixed until that host integration test passes. Keep source,
build and deployed revisions distinct. Rollback the paired host/public changes or
use the existing Fuse enable switch, noting that this disables other Fuse consumers
too; no data/schema rollback is introduced.

Current evidence: focused Rust tests, real SQLite + local HTTP stub integration,
and host compile checks; see [delivery](delivery.md). No deployment or live Fuse
verification has been performed.
