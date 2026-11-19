# Worker coordination implementation

Status: coordination implementation ready for review; production enablement
remains blocked on provider and host integration requirements below.

The accepted SPEC is implemented across two ownership boundaries. Public models,
repo ports, coordination persistence, Worker runtime, application control and
HTTP peer protocol belong to Avernet. Backend command mappings, credentials,
transport clients and enterprise assembly belong to the host repository.

## Delivered changes

- Independent persistent DocumentCas and StrictLease plugin contracts. Existing
  AtomicJson/history requirements are unchanged.
- Membership discovery and retirement, exact-version shard claims/handoffs,
  per-session sealing/transfer, cached checkpoint commits and unknown-result
  resolution. Private versions are independent of epoch and commit_seq.
- Admission spec/task creation moves from Newer to persistent create-if-absent
  through DocumentCas; Worker reads the same logical/physical document contract.
- A bounded attempt ledger makes recovery attempts idempotent within a business
  head; the next successful business commit resets it with the failure streak.
- Worker coordination and peer control implementation, with explicit hooks for
  the embedding session processor's current-round/outbox lifecycle.

The runtime coordinates existing business ports. It does not introduce a Runner
algorithm, implement a complete Gateway/Connector, or claim that an absent
production input/scheduling provider is ready. Full business-loop wiring and
production startup remain gated on valid providers and exact-instance peer
authentication. There is no ordinary-TTL fallback for StrictLease.

## Compatibility

Existing ownership/membership callers and their conformance fixtures migrate to
ShardRecord and addressable MemberRecord. No public revision is added. Both new
store capabilities are independent; enabling one cannot imply the other. Startup
checks reject missing requirements. Native document merge behavior must preserve
logical replacement and null exactly; it cannot silently widen AtomicJson.

Initial deployment must use a fresh isolated namespace or a separately reviewed
migration after excluding old writers. Existing coordination data is never
rewritten in-place by application bootstrap. Scheme A still relies on no deletion,
expiry, eviction or rollback of fencing records.

## Verification record

The implementation uses deterministic lease time and explicit delayed-write
schedules. The tests distinguish a new seal winning before an old commit from an
old commit winning before a delayed seal. They also exercise lost replies,
competing claims, graceful protection, source transfer, receiver failure, missing
records, unknown formats, recovery-attempt deduplication and stable retirement.
Normal checkpoint commit access cost is asserted as one document write/no reads.
Capacity tests cover thousands of rejected/deferred observations; repeated
checkpoint-loss errors cannot accidentally reinitialize state. Runtime tests
exercise continued recovery when unknown acquisitions fill all available slots.

Focused public tests and host provider validation are recorded separately in the
implementation PRs. Passing the in-memory fixture does not certify a backend's
lease clock, persistence or failover behavior. Full-stack business processing,
SDK transport, production failover and deployment are not inferred from these
tests.


Reproducible focused public checks from the repository root:

```bash
cargo test --manifest-path apps/bcs/Cargo.toml \
  -p bcs-dist-store -p bcs-store-api -p bcs-service-api -p bcs-test-support \
  -p bcs-dist-worker -p bcs-app-dist-runtime \
  -p bcs-dist-worker-http -p bcs-dist-worker-client
cargo +1.94.1 clippy --manifest-path apps/bcs/Cargo.toml \
  -p bcs-dist-store -p bcs-store-api -p bcs-dist-worker \
  -p bcs-app-dist-runtime -p bcs-dist-worker-http -p bcs-dist-worker-client \
  --all-targets --no-deps -- -D warnings
```

The existing import-rule baseline is unchanged. The complete architecture gate
has pre-existing dependency/import findings; it is not recorded as passing.
Validation uses dedicated build directories, disables incremental/debug artifacts,
and does not require a native SDK or container.


## Review follow-up (PR #4145)

The review fixes distinguish preparation failure, uncertain acquisition, confirmed
ownership awaiting scheduling, and local cleanup. Retry and new-discovery budgets
are shared fairly; failed sessions no longer abort recovery of the rest of a page.
Membership iteration keeps its cursor between rounds. Only confirmed terminal
instances are removed from discovery; absent/unknown registrations remain safe
across index-before-register races.

Drain now bounds each request separately from the overall shutdown deadline,
continues heartbeat during work, and transfers quiescent sessions concurrently
within a global round budget. Per-session owner/handoff and post-write checks stay
in place. The redundant pre-write owner read within one store transfer is removed.
Confirmed rejection cools down that shard/instance for `candidate_backoff`; unknown
handoffs retain their original identity and target. Subscription and cache release
share one retryable quiescent path, and unneeded shard feeds close. Incoming
handoffs preserve historical uncertainty through later read-only rejection.
An in-flight session transfer reserves local state against concurrent release;
per-shard transfer cursors prevent a failing prefix from starving later sessions.

Confirmed stage changes now continue drain immediately after yielding. Otherwise
the loop waits `check_interval`, unless a bounded scan still has unvisited ready
shards. Retry timestamps for claims, incoming confirmations, outgoing handoffs,
and session transfer/cleanup are retained before I/O. One healthy shard cannot
accelerate a failing shard's requests. Cooling/busy entries do not consume the
ready-shard scan budget; accepted shards without due session work avoid owner
reads. Each round still has the existing global session-operation budget and
handoff concurrency limit. The ready-work view is one local pass over bounded
runtime state; it adds no storage calls and does not replace authorization.

Rejected candidates use full instance identity, prefer untried recipients, then
the oldest eligible rejection with HRW tie-breaking. Expiry permits another
attempt only after definite rejection; it never cancels an unknown request.
With no eligible recipient, drain normally waits until `grace - exit_margin` and
returns `DeadlineReached`. The host must account for that shutdown-hook duration.

Coordination-store session capacity (default 10,000) and handoff-timer capacity
(default 4,096) are independently configurable. Worker startup checks advertised
session capacity; its admission backpressure precedes the store's final bound.
Terminal handoffs reclaim exact matching timers. Known checkpoint-existence
history is not discarded to make space or to hide a missing-record incident.

Reusable DocumentCas tests cover missing replacement, concurrent create/replace,
rejected-write invariance, exact deletion/null replacement and version exhaustion
through fixture setup. Reusable StrictLease tests cover irreversible expiration,
retire-before-register, idempotent registration, unchanged deadlines on rejected
writes and persistent terminal evidence. Clock/wait callbacks allow reuse without
adding timer or private-version setters to production plugin interfaces.

Worker regressions include more failures than the per-round acquisition budget,
unknown-then-definite errors, orphan discovery pages, deferred recovery hints,
rejected/unknown handoff targets, failed unsubscribe cleanup, and 3000-session
batches with zero/20 ms simulated transfer latency. Mixed cases verify each failed
operation's retry interval and demand-driven owner reads while another shard
continues progressing, with shard scan budgets of both 1 and 32. These tests check
scheduler gaps and bounded retries, rather than promising fixed completion times.
These controlled tests are
not a production throughput measurement or certification of a real lease backend.

## Deferred work

- [ ] Keep a bounded instance-to-shards owner view and prioritize shards belonging
  to a newly confirmed dead instance. Retain periodic reconciliation and fresh
  claim authorization. Current per-tick liveness deduplication already exists;
  this follow-up reduces full-scan discovery latency at large shard counts.
- [ ] Track multiple outstanding handoff targets with explicit request history
  and reconciliation. Current behavior deliberately keeps one uncertain request
  per shard. Do not switch targets solely because the old owner remains visible,
  a retry is rejected, or the target's liveness is uncertain.

Neither deferred optimization relaxes checkpoint fencing, strict lease termination,
or the meaning of `DeadlineReached`. Production provider/host integration limits
listed above remain separate prerequisites.


### Local follow-up verification (2026-11-18)

- 520 focused nextest cases passed across the eight crates listed above, including
  reusable store/lease contracts and Worker shutdown/recovery regressions.
- Instrumented changed-line coverage against the PR merge-base: **2383/2547
  (93.56%)**. The latest drain follow-up alone covers **123/138 (89.13%)** changed
  instrumentable lines against `a46d2791aa`. This is a local changed-line
  measurement; full-workspace CI is reported separately in the PR.
- The Worker crate and all its test targets passed Clippy with `-D warnings`
  after this drain follow-up. The earlier broader test-support invocation encounters existing
  fixture-wide unwrap/expect findings; those rules were not relaxed.
- Relative documentation links, changed-file whitespace and changed Rust source
  size checks passed. Import-rule findings are confined to 56 untouched baseline
  files; the complete architecture gate is not claimed as passing.
- No native SDK, container image, production backend or deployment was used.
