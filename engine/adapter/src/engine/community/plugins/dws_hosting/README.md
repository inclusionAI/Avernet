# DWS connection and SQLite hosting state

## Context Boundary

```yaml
purpose: Transactional implementation of the personal DWS hosting state port.
provides:
  - SqliteHostingStateStore
  - SqliteHostingRecoveryStore
  - DwsCliConnection
  - SqliteHostingExecutionStore
  - OpenClawHostingEngine
  - SqliteHostingOutbox
  - DwsCliSender
  - SqliteHostingRuntimeStore
  - SqliteHostingManagementStore
consumes: []
internal_dependencies:
  - engine.community.kernel.dws_hosting
  - engine.community.kernel.dws_runtime
  - engine.community.plugins.dws_hosting
  - engine.community.openclaw.protocol
  - engine.community.openclaw.client.gateway_client
```

### Change impact

Implements the state, recovery, connection, execution and delivery Plugin APIs structurally.
No core imports or ambient environment reads. Explicit construction requires
private state/credential directories, trusted executable argv, an environment
allowlist, expected account identity, authorization host allowlist and history
timezone. These are composition-root configuration, never request-controlled paths.

CLI projections were checked against official DWS v1.0.62 source and pre-production
command help. Automated tests use offline executable fixtures, not a live account.
Authorization URL extraction is isolated because device login emits the challenge
on stderr, not as a machine-readable pending response. CLI upgrades require
rerunning the contract suite and live authorization/history acceptance.

## Persistence

| Table | Durable data |
|---|---|
| `bindings` | Trusted identity, enabled epoch, activation time, confirmed rule/version |
| `conversations` | Stable session mapping, manual-takeover epoch and activation time |
| `inbox` | Deduplication key, incoming text and optional batch association |
| `batches` | Collecting/ready/running/unknown/completed/cancelled state and immutable turn snapshot |
| `intake_checkpoints` | Epoch, complete-history watermark, recovery attempt and claim barrier |
| `intake_hints` | Durable conversation/message-ID hints awaiting canonical history |
| `executions` | Reserved run/request, ACK/uncertain/result state, raw result and validated decision or manual reason |
| `outbox` | Immutable labelled delivery/target, lease token, retry/poll deadlines and correlated task/message receipts |
| `hosting_resolutions` | Owner acknowledgement, target, evidence and timestamp; never a forged delivery receipt |
| `hosting_settings` | Owner-confirmed form settings, saved atomically with the binding rule snapshot |
| `hosting_request_nonces` | Short-lived, one-use signed management request IDs |

Transactions use `BEGIN IMMEDIATE`, foreign keys and parameterized SQL. A
partial unique index prevents two active turns in one conversation across
processes. SQLite failure rolls back state; it must not trigger external work.

Files are private (directory 0700, database 0600). Symlinks, shared permissions,
unrelated databases and unknown schema versions are rejected. DELETE journaling
and FULL synchronization do not guarantee that arbitrary NAS supports SQLite
locking: the actual persistent mount must be qualified before deployment.

This database contains private message bodies and rules, but no OAuth tokens.
POSIX permissions are not isolation from tools running as the same container
user. Owner-only routing and signed management commands are implemented; actual
deployment isolation and secret provisioning remain mandatory. Retention/storage
bounds and owner operational tooling are implemented; encryption is a deployment
concern. See `specs/dws-management.md` for phase-7 opt-in activation.
Schema v1-v5 migrates to v6 transactionally without changing message/session identities.
The v4 migration rebuilds only the outbox CHECK constraint to allow terminal
`accepted` owner notices; failure rolls back the entire migration. Older workers
reject newer schemas rather than silently interpreting them. V6 adds inbox receipt
time and owner acknowledgement records. Cleanup retains deduplication IDs and
conversation mappings; unresolved work is never evicted. Capacity overflow stops
intake without advancing its cursor. See the phase-6 spec for limits and recovery.

CLI commands use argv without a shell, bounded output/timeout, private file modes
and cancellation-safe process cleanup. The connection only releases its own
consumer; it never stops the shared DWS bus or other subscriptions. History reads
do not mark messages read. Sending is explicitly constructed and never started
by imports, connection supervision or ordinary chat.
`HostingCommandProcess` provides the bounded argv runner; `DwsProcess` preserves
the DWS-specific environment and credential directory. Internal notification
composition can reuse the runner without inheriting DWS configuration.

`DwsCliSender` uses `chat +messages-send --as=user`, the immutable source
conversation, `--idempotency-key` and forced `--ai-tag=true`. Model text is one
argv value, not shell syntax. Only the fixed AI-marked body is sent. Owner
notices are rejected here; an explicitly composed internal sender handles them.
No recipient lookup or peer/self-message fallback is permitted.

Versioned `im.message-send-receipt.v1`/`im.message-send-status.v1` projections
are parsed conservatively. `openTaskId` alone is not delivery confirmation;
`+messages-query-send-status` polls that exact task. Negative/unknown response
states cannot mark sent. Opaque CLI failures may be post-write and are not safe
to retry. A stable key is included but the server's deduplication retention is
not assumed: no uncertain write is replayed. Preflight retries and read-only
polls are bounded/persisted, while uncertain sends block the source conversation.
Source/fixture checks are not live acceptance: DM targeting, AI tagging and
actual receipt schemas must be tested before any production activation.

The OpenClaw implementation reuses existing protocol DTOs and pure signing
helpers only. It does not create an `OpenClawGatewayClient` or use its global pool.
One bounded dedicated socket handles `agent` ACK and final responses, or a
read-only `agent.wait` inspection. Explicit trusted URL/token/existing identity
are required; redirects, unsafe plaintext remote URLs and shared/symlink key files
are rejected. No credentials or private WebSocket frames are logged. The key is
read-only, never regenerated. The configured device must already be paired and
have `operator.read`/`operator.write`; the library does not auto-approve devices.

Wire compatibility is pinned to the inspected OpenClaw 2026.5.12 `agent` and
`agent.wait` handlers. Offline fixtures validate the contract, not a live gateway
deployment; that acceptance remains required. A live engine connection may run
the existing agent's tools, so only the eventual explicit activation path may
invoke this library, never an import or an interactive-chat fallback.
