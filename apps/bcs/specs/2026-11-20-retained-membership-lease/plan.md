# Retained membership lease implementation plan

> Execute in the paired isolated worktree with test-first changes and a fresh final review.

**Goal:** Replace permanent strict expiry with CAS-confirmed termination and a 30-day retention window.

**Architecture:** A retained lease has a short heartbeat deadline and a separate 30-day storage retention period. Reading a timed-out active record conditionally terminates that exact version. A heartbeat may win before termination; a confirmed terminal record cannot be revived within retention. Missing records remain unknown and returning workers use fresh incarnations. Owner epochs and session fences are unchanged.

**Tech Stack:** Rust, public Store contracts and deterministic model; native document CAS in independently owned host providers.

## Constraints
- No local Singlebox execution. Run focused Rust tests locally; GitHub CI owns full unit/coverage and Singlebox checks after publication.
- Preserve other worktrees and existing Gateway/Worker changes.
- No production deployment, live load test or automatic migration. Fresh namespace and coordinated consumer/provider rebuild.
- No indefinite replay guarantee beyond 30 days of inactivity. Separate TTL refresh may fail; expose uncertainty and repair incomplete retention on subsequent observation.

## Task 1: Public contract and model
Files: plugin-api/bcs-store-api/{src/lease.rs,src/common.rs,README.md}; test-support/bcs-test-support/{src/store_model/coordination.rs,src/contract/plugin/store/lease.rs,tests/coordination_primitives.rs}; coordination consumers and owning specs.
1. Add tests for renewal winning after timeout before terminalization, terminal retention and expiry, heartbeat retention refresh, and fresh identity after cleanup. Run to observe RED.
2. Rename capability to RetainedLease; document sampled remaining time, CAS-confirmed termination, bounded retention and unsupported old replay after cleanup.
3. Implement deterministic model retention; keep existing conformance tests for versions, retries, unknown writes and terminal evidence inside the window. Reserve a final version for termination.
4. Run affected store/model/worker tests. Expected: pass without Singlebox.

## Task 2: Host native provider
Files: host lease implementation, tests and provider README.
1. Add command tests showing JSON.SET NX / JSON.MERGE CAS EQ / PEXPIRE with no EVAL. Exercise conflict, concurrent heartbeat versus termination, lost TTL replies, malformed records and retention repair. Observe RED.
2. Replace the Lua path with bounded native CAS operations. Synchronized client wall time supplies sampled deadlines; no TIME command. Local duration budgets remain monotonic. Prepare 30-day TTL before existing-record CAS and assign it after first creation; check/repair missing TTL when reading. Never swallow partial-success errors.
3. Update opt-in real-provider tests to exercise native commands and retention; do not run unconfigured live infrastructure tests.
4. Run provider tests and compile the paired Worker hosts. Expected: pass; preserve production-provider validation gaps explicitly.

## Task 3: Publication and verification
1. Update contracts, adopted coordination/runtime specs, navigation and verification evidence. Record command counts and migration impact.
2. Fresh independent review of both diffs; fix substantive findings with regression tests.
3. Commit and push to the existing public and host PR branches with gates enabled and precise leases for rebased history. Keep feature publication distinct from gitlink integration.
4. Update both PR descriptions; await GitHub unit and Singlebox coverage checks, fix current-change failures and rerun remotely as needed.

## Progress
- Baselines: public 051f08bd69; host 664800500f rebased onto current dev 1a8f4a8b3f.
- Approved user design overrides previous permanent StrictLease guarantees.

- Task 1: complete locally: 14 primitive + 3 negative tests pass; Store/Worker/test-support regression passes.
- Task 2: complete locally: native provider conformance 93 passed; all-target Clippy and enterprise architecture pass.
- Final review: retention preparation order corrected with a RED→GREEN no-observer test; early payload-size guard restored. No extra command or protocol state added.
- Ruling: use synchronized client wall clocks following user feedback; clock error can shift timeout decisions, while CAS and owner/session fencing remain required.
- Ruling: the finite 30-day inactivity horizon is intentional; do not treat arbitrarily late requests or a month without observers as production blockers.
- Ruling: preserve dev gitlink until the public feature is merged and independently reachable on the internal release mirror; paired-checkout validation does not claim release integration.
- Final: minor (deferred): stale/exhausted update returns do not repair orphan TTL; ordinary observation repairs it.
- Final: minor (deferred): native sub-microsecond TTL rejection differs from model precision; production uses seconds.
