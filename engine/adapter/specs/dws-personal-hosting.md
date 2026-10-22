# DWS Personal Hosting: Implementation Progress

Base: `origin/dev` at `836573c26`, branch `codex/dws-personal-hosting`.
Updated: 2026-10-19. This is a development checklist, not a deployment claim.

## Agreed Scope

Personal Bots, owner-managed, direct messages only; OpenClaw first behind an
engine-neutral contract. Hosting lives in the container Adapter with durable
SQLite state. No shared Backend business database or account-global Bot limit.
Group/OA/calendar/todo subscription research does not expand automatic replies.

One session per bound direct conversation. One `agent` turn receives current
rules via `extraSystemPrompt` and generates a structured decision/reply, using
the Bot's existing skills and MCP tools. No separate restricted agent or routine
LLM rule-router step. `llm-task` is reserved for at most one tool-free formatting
repair when safe; invalid/ambiguous results require manual handling.

## Checklist

- [x] Fetch latest dev and create an isolated public development branch.
- [x] Shared immutable values and `HostingStateStore` persistence Plugin API.
- [x] Private SQLite state, message deduplication and stable conversation mapping.
- [x] Durable batching: quiet 4s, maximum 15s, plus count/size bounds.
- [x] Snapshot rules at turn claim; later rule edits do not cancel in-flight work.
- [x] Disable/takeover epochs, per-conversation serialization and unknown-run blocking.
- [x] State conformance tests: real SQLite, parallel callers, rollback and reopen.
- [x] Owner-only management authorization and explicit activation gates (offline integration).
- [ ] Live device authorization/token lifecycle acceptance on the provisioned Bot.
- [x] Owner-only authorization service, device challenge and expected-account verification (library).
- [x] DWS CLI wrapper and supervised listener; bounded subprocesses and safe error codes.
- [x] Startup/reconnect/periodic history compensation with durable cursors and overlap (library).
- [x] Claim barrier, message-ID hint reconciliation and schema v1-to-v2 migration.
- [x] Explicit supervised runtime, recovery-before-work barrier and single-database ownership.
- [x] Retention, intake/backlog/disk bounds and owner-only unresolved-work projections.
- [x] OpenClaw `agent` runner: dedicated socket, request/ACK/final correlation and status-only recovery (library).
- [x] Durable execution reservation/result ledger, strict reply/ignore/escalate parsing and manual records.
- [ ] Optional one-shot tool-free format repair with safe semantics and temporary-session cleanup qualification.
- [x] Lossless missing/trailing-comma repair; ambiguous/missing decisions still require manual handling.
- [x] Durable outbox, mandatory AI label, reply delivery and uncertain-send handling (library).
- [x] Disable/takeover send fencing, own/AI-message echo filtering and bounded retry/backoff (library).
- [x] Purpose-based delivery routing and non-queryable owner-notification acceptance (library).
- [x] Internal owner-notification MCP sender; one authorized pre-production message received by owner.
- [x] Owner acknowledgement of uncertain outcomes with durable audit and conversation takeover.
- [ ] Live DWS echo qualification.
- [x] Management API and new OpenAPI frontend integration; isolated from other engines.
- [ ] Container restart/NAS qualification, fault-injection E2E and pre-production acceptance.

The checked libraries, opt-in lifecycle, management API and frontend are implemented,
not automatically enabled. Phase 7 installs only an explicitly provisioned
personal OpenClaw binding; no real replies were sent during development. Phase 5 sent one explicitly
authorized owner-notification test, without deploying code or changing chat.
Existing OCB/DAAS worktrees and unrelated local changes are preserved; the internal
sender is in a separate OCB worktree on `codex/dws-owner-notification`.

After the 2026-10-19 rebase, local phases 1-5 are `234fc676c`, `5be13b855`,
`7f8404b4b`, `b0819fb9f`, `c05b99d70`. Internal phase 5 is `ab6a4428c8`
on `codex/dws-owner-notification`, based on internal dev `4cdbbbb1b7`.
All remain unpushed. Phase 6 (`ef79948a9`) adds runtime composition and operational recovery.
Phase 7 adds management/lifecycle HTTP integration and frontend.
Next: provision the test Bot, then live fault-injection and deployment acceptance.

Phase 3 never replays uncertain `agent` calls or fetches history[-1] as a result.
Saved results can be parsed after restart; unavailable or malformed results stay
explicitly unknown/manual. Resolved reply/escalate decisions can be explicitly
enqueued as immutable outbox records; phase 6 composes these workers explicitly.
Format repair is deliberately not wired in this phase, not silently approximated
by rerunning the tool-enabled agent. Existing chat/other-engine paths are unchanged.

Phase 4 sends each reply at most once from this queue. Peer reply bodies have
no adapter-added AI prefix; DWS's default native AI tag is forced on.
Only preflight failures before dispatch retry, at
2/5/15 seconds plus bounded jitter. A task ID means accepted, not delivered:
read-only receipt polling is bounded (default 12 attempts, 5 seconds apart).
Timeout/disconnect/malformed ACK or a crash after dispatch stays unknown; it
does not resend or rerun the agent. Exact task/message/conversation evidence is
persisted. Unknown outcomes retain the conversation block for reconciliation.

For a Linux loopback OpenClaw gateway, authenticated hello uptime is converted
to a monotonic process-start interval tied to the host boot ID. A `prepared`
update durably stores this witness before the original RPC is sent; it is not
an ACK. It survives Adapter restart in invocation JSON (no schema migration).
Reconciliation can prove replacement only on the same host boot with disjoint
start intervals and a one-second margin. A proven restart retires the old run
to manual/ended-result-unavailable and releases the execution fence; it never
replays the request, recovers text from history, or sends the old reply.
Missing witnesses, remote gateways, ambiguous intervals and live runs remain
unknown; an elapsed timeout alone is not proof of completion. Legacy unknown
records require separately verified terminal/restart evidence.

Disable/takeover gates run before dispatch; rule edits do not invalidate the
turn's snapshot. Once the durable `sending` transition commits, the write is
in-flight and cannot be promised cancelled. Missing/failed storage never bypasses
the queue. The caller must run intake first and use one active container; copied
databases are not a distributed lock. The delivery router sends owner notices to
an explicitly supplied internal MCP sender, never to the peer or DWS self-chat.
That sender binds the recipient to the trusted Bot owner, not model output.

Phase 5 keeps MCP acceptance separate from delivery. A validated successful
response with a task ID but no query capability becomes terminal `accepted`,
not `sent`: retain the receipt, release the conversation and do not resend/poll.
Timeout, malformed output or failure after dispatch remains `unknown`, blocking
the conversation for reconciliation. This exception applies only to owner notices;
peer replies still require confirmed delivery. SQLite schema v4-to-v5 migration
preserves queued/in-flight records atomically. Older binaries reject schema v5.

Phases 1-6 did not add startup/HTTP/UI wiring. `di/dws_hosting.py` provides an
opt-in factory, returning None without I/O by default. DWS send/task projections
are pinned to v1.0.62 source, not live
delivery acceptance; stable DM conversation targeting, server idempotency semantics,
AI tags and actual status projections must be qualified before enabling sends.

## Phase 6 Runtime Contract

- Trusted configuration permits only personal OpenClaw Bots and requires an
  explicitly qualified persistent mount. Factory construction never authorizes
  DWS or enables the binding; `run()` starts only an already-enabled binding.
- A database-wide OS lock covers intake, executor, sender and maintenance until
  shutdown completes. Intake closes the durable barrier before workers start.
  Saved results/outbox gaps resume without re-executing; interrupted dispatches
  become unknown. Preflight/status reads may resume, uncertain writes may not.
- An intake/storage/worker failure stops all hosting tasks, joins SQLite writes,
  closes the barrier and propagates the error. It does not affect chat's sockets
  or global engine startup. Stop is not a promise to cancel an external write or
  agent tool call already dispatched.
- Owner-only status/issues projections expose IDs and states, not private
  message bodies. Manual resolution requires the runtime stopped, records the
  owner's evidence and takes over that conversation. An uncertain engine run
  additionally requires a correlated ended observation. No action invents a
  sent receipt or retries the old message; future hosting requires explicit resume.
- Defaults: 100,000 deduplication rows, 1,000 unfinished batches, 10,000 hints,
  256 MiB database and 64 MiB free-space reserve. Intake over a limit fails
  without consuming the message/checkpoint. Operations must resolve backlog or
  adjust storage before restarting; there is no silent eviction or skip.
- After 30 days, bounded cleanup removes confirmed terminal payloads and keeps
  deduplication IDs/session mapping. Unknown/manual evidence is retained. SQLite
  reuses freed pages; file compaction and encryption are deployment concerns.
  No WAL/NAS lock assumptions or distributed ownership across copied databases.
- Schema v6 adds ingestion time and owner acknowledgement records transactionally;
  older workers reject v6. Stop all workers and restore a pre-upgrade backup to
  downgrade. The optional LLM formatting repair is still disabled/unqualified;
  this phase only fixes delimiters with original decoded values unchanged.

## Verification

Phase 7: **1,302 engine regression tests**, **498 Backend contract/relay tests**
(one pre-existing skip), **60 internal sender/composition tests**, **39 frontend
tests** and the Gateway catalog test passed. New management engine modules cover
**358/358 executable lines**, Backend management modules **107/107**. TypeScript
typecheck and frontend guardrails passed. Desktop (1200px) and mobile (390px)
screenshots exercised the real frontend with intercepted local API fixtures;
no real Bot calls or messages. Static security review found no qualifying
High/Medium findings. Full repository CI/singlebox and live deployment acceptance
remain unrun. The borrowed local frontend dependencies have an existing unrelated
Ant Design Flex warning; no new browser errors were observed.

Gateway schema was published with the repository's additive-operation tool:
only the seven new DWS paths (eight operations) and transitive components changed,
without overwriting unrelated catalog drift. Dev advanced again during phase 7;
these local commits retain the previously verified base and have not been pushed.

Phase 2 after rebase: **628 tests passed**, including four architecture checks.
Previous phase 2 scoped coverage: **99.13% lines**, **97.13% branches**.
Ruff and whitespace checks passed. One existing Starlette/httpx deprecation
warning remains unrelated to this change.

Phase 3 focused suite: **269 tests passed**. Execution/parser/transport and SQLite
schema source coverage: **458/459 lines (99.78%)**, **148/148 branches (100%)**.
Offline WebSocket-to-worker-to-SQLite tests exercise signed auth, ACK/final,
cached completion, timeout, disconnect, cancellation and redirect rejection.
Combined regression: **857 tests passed**, including the existing OpenClaw client,
OpenClaw/Claude adapters, session favorites and architecture suites. Static review
found no qualifying new high/medium security issues under the documented trust
model. No live Bot/engine or DWS account was contacted for phase 3 verification.

Phase 4: **946 tests passed** across the same combined regression scope, including
**358 DWS tests**. Delivery worker/values, outbox, sender, receipt parser and
SQLite schema source coverage: **390/390 lines (100%)**, **133/134 branches
(99.25%)**. Real offline subprocess/SQLite tests cover concurrent workers,
cancel/restart, backoff, revocation, wrong receipts and disk failures before/after
external sends. Ruff/whitespace checks passed. Static review found no qualifying
new high/medium security issues under the documented trust model. The existing
Starlette/httpx warning remains. No live messages, container changes or push.

Phase 2 verification includes state, recovery, wire parsing, real subprocess
cancellation, owner/account checks and supervised fault injection, plus existing
adapter/session-favorite and architecture regression tests. This is scoped
verification, not a full-repository or live DWS end-to-end result.

Phase 5: **960 public regression tests** and **53 internal sender tests** passed.
Scoped public source coverage: **405/409 lines (99.02%)**; the internal sender,
receipt parser and composition factory have **100% line and branch coverage**.
Tests cover fixed-recipient routing, nested MCP errors, no-repeat acceptance,
uncertain writes, cancellation, disable/takeover and atomic v4 migration/rollback.
One live MCP test on 2026-10-19 returned successful acceptance with `openTaskId`;
the owner separately confirmed receipt. No delivery-query API was observed, so
the runtime deliberately does not label accepted tasks as delivered. No deployment,
automatic hosting activation, push or PR is included in this phase.

Phase 6: **1,000 combined public regression tests**, **26 additional Hermes and
session-favorite tests**, and **53 internal sender tests** passed. Executable
changed production lines in this phase: **406/406 covered (100%)**. Ruff and
whitespace checks passed. Tests cover restart barriers, interrupted operations,
owner resolution, cancellation races, capacity limits, retention, migration and
lossless JSON delimiter repair. This is scoped offline verification, not a full
repository/singlebox run or live storage/DWS acceptance. No deployment or push.

The state contract suite lives at
`engine/adapter/src/engine/community/core/dws_hosting/tests/test_state_contract.py`.
Run from the adapter environment with its `src` on `PYTHONPATH`:

```sh
python -m pytest src/engine/community/core/dws_hosting/tests -q \
  --cov=engine.community.core.dws_hosting.connection \
  --cov=engine.community.kernel.dws_hosting \
  --cov=engine.community.kernel.dws_connection \
  --cov=engine.community.plugins.dws_hosting \
  --cov-branch --cov-report=term-missing
```

The explicit runtime factory now composes authorization, recovery and send-safety
gates. Phase 7 adds owner management, signed relay commands and opt-in lifecycle;
see [management contract](dws-management.md) for endpoints and provisioning.
A successful unit test does not qualify the storage mount or prove live
DWS/engine integration.

## Directory Context and Outbound Scope

`HostingContextProvider` is a read-only Plugin API. The deployed DWS provider
matches message `sender_id` to `contact user search.openDingTalkId`, never to a
display name alone. It supplies names, employee ID, department, position, labels
and available manager facts. Friends without employee records remain valid
contacts, but their organization relationships are unknown. Names and message
bodies are data, not instructions or evidence of membership.

Rule interpretation belongs to the generation LLM and the independent reply
reviewer. There is no rule regex, fixed sentence list, contact-count limit or
topic-substring gate. Both receive the complete frozen natural-language rule,
time-window settings, current batch, real dialogue and verified directory facts.
The generation decides reply/ignore/escalate; a reply still requires an independent
approve verdict. Unknown facts required by a rule must not be guessed.

The directory provider verifies the exact sender and owner regardless of rule
wording. `disposition=allow` means identity verified, NOT that the rule permits a
reply. `sender.name_resolution` distinguishes unique/confirmed aliases from
ambiguous, unknown or other-contact aliases. Operator-confirmed names only resolve
identity; they are never a hosting whitelist. Failed optional manager enrichment
keeps relations unknown without rejecting unrelated rules. Direct-manager facts
do not establish the whole leadership chain; missing HR labels do not prove
non-HR membership, and same manager does not prove equal job grade.

Directory reads remain account-scoped and bounded (100 rows, 128 cached queries,
five-minute cache TTL). Only relevant fields enter prompts; phone/email are not
included. The immutable identity snapshot is bound to binding, conversation,
sender, turn and rule hash and expires after five minutes. Outbox enqueue, claim
and pre-send still enforce that evidence plus enable/takeover/epoch guards.
Missing, expired or mismatched identity cannot authorize a send. Existing owner
reply suppression, strict JSON validation, deduplication and uncertain-send
handling are unchanged. New name-resolution metadata is additive to stored
context snapshots; no database migration, Backend or OpenClaw source change.

## Preproduction Recovery Follow-Up

The current implementation supersedes the earlier "format repair disabled"
phase notes: one plain candidate may use a separate, tool-free OpenClaw raw-model
run to recover a strict decision. Reply text must remain byte-for-byte identical;
ambiguous/multiple outputs, failed runs and unavailable repair remain manual.
No original agent replay, recipient inference, or bypass of the directory permit.
Formatting sessions are isolated but may persist; cleanup remains follow-up work.

A no-op reconciliation must preserve an already-completed intake checkpoint.
Startup/reconnect recovery and failures still close the global claim/send barrier. Runtime
failures log exception type and code locations, not exception text or message data.

## Reply Relevance Guard

A valid JSON reply is not evidence that it answers the current incoming batch.
The generation contract now explicitly treats history as background, not an
unfinished request. Before resolving any reply, the executor calls the DWS-only
`HostingEngine.review_reply` Plugin API in a separate session/run with
`modelRun=true`, `promptMode=none`, `deliver=false`. Tools and ordinary history
are excluded; the original frozen rules, current input and candidate are supplied.
Review input contains the confirmed rules, verified directory facts, candidate,
and one chronological copy of real dialogue with the current batch marked.
No context-count reduction or model change: duplicate generation input, internal
execution metadata and delivery-status history are omitted. The only response
field is `verdict`: `approve`, `skip_owner_replied`, or `reject`. Approval keeps
the original candidate unchanged. A semantic owner-already-replied verdict also
requires a complete real owner message later than this batch; it resolves as
`ignore` without a peer reply or owner notice. Unrelated or uncertain
content, invalid output, timeout or unsupported mode requires manual handling;
the original turn is never replayed. Ignore/escalate decisions skip review.

Impact: DWS executor, OpenClaw hosting plugin and their conformance fakes only.
No database migration, frontend, Backend, shared chat client, OpenClaw source
or other-engine change. One extra inference per proposed reply adds latency;
missing contextual evidence may cause conservative manual handling. This is a
model-based safety check, not proof of relevance. Review-session cleanup remains
unqualified, like formatting sessions.

## Incremental real-dialogue context (Adapter only)

Generation now receives first-use recent DingTalk history, then unseen message IDs
and delivery-state updates per stable session. Owner manual replies are included;
old assistant candidates and tools are not treated as delivered chat. The same
dispatch transaction freezes a bounded snapshot for independent review and records
attempted synchronization keys in the invocation ledger. Unknown dispatches are
not replayed. History is read-only context, never a new send trigger. See the DWS
core README for bounds, failure behavior and legacy-record compatibility.

## Live Intake and Revocation

- Live DM hints hydrate only their own conversation, independently of periodic
  full discovery/history reconciliation. Pending hints block that conversation's
  claim/reservation/send, not other conversations. Startup, reconnect and failed
  reconciliation retain the global barrier. Checkpoints never move on partial reads.
- After generation and immediately before dispatch, read fresh canonical history.
  A new owner message after the batch, absent from the frozen snapshot, silently
  supersedes the unsent candidate or owner notice. Canonical sender/time/AI
  metadata also covers media: a human image counts, an AI-marked send does not.
  Confirmed automatic send IDs are excluded. Missing/partial history cannot
  authorize sending. This protects
  human activity during generation/review; it does not remove the final small
  race between the last read and remote send. Previously seen owner messages
  remain subject to semantic review, not an unconditional skip.
- Explicit disable/takeover retires revoked, unsent executions and rotates only
  the affected unresolved DWS session. The old ledger/transcript stays for audit;
  late results cannot send or block a new control epoch. Rotation is isolation,
  not proof that an old engine run ended. Cancellation attempts a bounded,
  exact-run `chat.abort`; no session-wide or interactive-chat abort is allowed.
  Re-enable recovers real dialogue into the replacement session. Rule edits and
  normally completed sessions do not rotate. Uncertain remote sends remain
  uncertain and are never reset/retried by this mechanism.
- Delivery status facts already synced to the current session are not repeatedly
  injected. Generation plus independent review still cost two ordinary LLM calls;
  same-conversation execution remains serialized to preserve ordering.

These changes are Adapter-only, with no database schema migration, Backend,
ordinary chat, other-engine or OpenClaw-source change.
