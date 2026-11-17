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
