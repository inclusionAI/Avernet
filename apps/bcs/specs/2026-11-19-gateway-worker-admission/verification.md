# Gateway / Worker integration evidence

Owner: distributed BCS admission, output and public test assembly. This record
covers only A2A conversion and A03/A04/A05. It does not certify deployment.

## Baseline and preservation

- Gateway #4132 `dd6deb7bb39abdf5d9960e05b3ddf0e9a2f84d77` is merged.
- Worker SPEC #4144 `8811efbc9751004747451d00c5ea1e6a30a6b224` is merged.
- Worker #4245 was open at `00b5cb053a93fb126b9f2fe439d5d35b64185eeb`.
- Authoritative `github/dev` was `a2433cbf2bafa4c38fbff78b226d61701adcd5a2`.
  The ten PR commits replayed to `66976a7f2a77ff9257b7c77a5fed8d4ceac934b6`.
  `origin` is an internal fetch mirror; no push is made there.
- Work is isolated on `codex/worker-gateway-admission`. Earlier draft changes
  remain in their original worktree. OCB and its gitlink are unchanged.

## Changes and owning contracts

| Item | Implementation | Contract |
| --- | --- | --- |
| A03 | Worker admission restores canonical input before generating initialization; compares caller body, identity and target, ignoring later directory/time changes | Worker SPEC section 4.1; `core::dist_admission`; Inbox lookup/admit |
| A04 | Authenticated scope passed explicitly; Worker initializes task/time/display and physical profile; Accepted/Duplicate share one Gateway receipt | Worker SPEC sections 3.3 and 4.1; distributed port README |
| A05 | Lookup miss is not a reservation; atomic Inbox winner is reread after conflict, compared and confirmed/repaired | Worker SPEC section 4.1 |
| Output | Neutral DisplaySnapshot and artifact operations remain persisted; injected Worker output facade resolves/verifies blobs and delegates to a pure format adapter | Worker SPEC sections 3.2, 3.3, 5 and 6; `port::dist_output` |
| A2A Q2 | Stable artifacts for snapshot/Upsert/AppendText; strings become TextPart, JSON DataPart; no invented history/status message | Gateway plan integration amendment; output adapter README |
| Byte policy | Removed unit/cumulative/snapshot byte quotas and hidden archive/read caps; retained physical frames, metadata/count and IO budgets | Worker SPEC section 3.3 and acceptance matrix |

## Joint validation

The opt-in `bcs-worker-e2e` companion is built from this checkout. It uses the
explicit in-memory Store mock and existing SQLite Result/Blob provider. Python
issues actual loopback A2A JSON-RPC and SSE requests to an axum listener, while
the companion drives real Inbox, Worker and TaskFeed services. These are process
and network tests, not production storage-durability tests.

Command (from `apps/bcs`; binary path may be supplied by the caller):

```sh
cargo build --locked --offline -p bcs --features worker-runtime-e2e --bin bcs-worker-e2e
python3 scripts/e2e-test/worker_runtime_story.py --binary target/debug/bcs-worker-e2e --report /tmp/worker-gateway-process.json
```

Result: 15 scenarios passed, including three new Gateway scenarios:

- Discard first HTTP response, same-ID retry returns original task, changed body
  conflicts, Runner executes once; retry after completion also succeeds.
- Two concurrent first HTTP requests converge to one task and one Runner call.
- Actual SSE delivers Upsert plus 70,000-byte AppendText; stable artifact ID and
  folded text agree with GetTask and the final SQLite-backed view.

The prior twelve scenarios still pass: document CAS, archive/late cancel, HumanReply,
Unknown supervision, authorization/isolation, follow/trim/Gap, takeover and drain.
The in-process `gateway_chain` target independently covers the same shared HTTP
adapter chain and a changed directory revision. `admission_replay` forces both
initial lookups to miss with different clocks, checks unequal concurrent bodies,
and tests sixteen lost-write points.

## Access cost and limits

Worker admission adds one scoped canonical document read before first admission. A
race loser adds one reread and invokes the existing idempotent repair protocol;
there is no new retry loop or scan. Normal replay reads canonical content and
uses the original Inbox confirmation path. Existing retained-evidence budgets
and repair/backoff bounds remain. No cache was added.

Output restore resolves the full immutable snapshot once; follow resolves each
referenced event as required by its existing batch. List formats inline values
without extra Blob reads. No write or new result query is added by formatting.
Archive tests assert unchanged query counts for payloads beyond the old limits.
Removing quotas allows greater allocation/IO and potentially quadratic full-snapshot
write cost. These tests prove correctness at selected sizes, not capacity.

## Remaining evidence and deferred work

- The current head has no new TBase PROXY/OCB joint run. Historical #4245 PROXY
  results belong to their recorded heads and are not reused as current evidence.
  No SDK or OBProxy validation substitutes for TBase PROXY.
- Real OB-instance acceptance remains deferred. No local stress test was run;
  DEV duration is diagnostic only.
- Coverage thresholds/exclusions are unchanged. Local targeted tests and the
  process story are not a full workspace or coverage-gate result; current CI
  must report its own status.
- A01 control-state retry ordering, A02 interaction mapping, A06 HTTP wait boundary,
  opaque cursor redesign and task-list ordering remain deferred. No new seven-day
  task expiry or message-history store was introduced.
- The distributed prototype metadata shapes require a fresh namespace and
  coordinated rebuild of workspace consumers; automatic migration is absent.

## Final local checks

- Rust 1.94.0, offline affected-package regression: 627 passed, 0 failed;
  one pre-existing documentation example ignored. Ten packages: model, projection,
  Store, Worker, A2A output, Gateway core/Store/app/HTTP and test-support.
- Final admission target: 5 passed, including one additional simultaneous
  unequal-body regression added after the full run. Distinct passing tests across
  these runs: 628; the other four repeated admission tests are not added twice.
- Single-process HTTP adapter joint target: 2 passed (included in package count).
- External process loopback HTTP/SSE story: 15 passed; memory Store + SQLite,
  not TBase PROXY. Its report declares durable_store=false.
- Independent review found no actionable production defect in the selected
  scope. Reviewer separately observed 26 focused tests pass (overlapping counts).
- Singlebox artifact validation now requires all three Gateway process scenarios;
  missing any of them fails closed. Numeric coverage thresholds/exclusions are unchanged.
  Six verifier test functions were executed directly with isolated temporary
  fixtures and passed; the actual process report was also accepted. Pytest is
  absent and its temporary dependency download timed out. The shell gate
  regression could not start its checks because the existing backend virtualenv
  Python path is absent; no full local Singlebox coverage result is claimed.
- Port-purity check and 14 import-scope regression cases pass. Full import checker
  still reports pre-existing violations; none refer to a changed source file.
- Strict Clippy (`cargo +stable clippy --lib --no-deps -- -D warnings`) passes
  Worker, A2A output, Gateway core/app/HTTP and projection, plus strict checks on
  the new admission and HTTP-chain test targets. Stable is 1.99.1. The attempt
  including Store is blocked by an unchanged AtomicU64::fetch_update deprecation
  in inbox/consumer.rs:469 introduced by that newer compiler. Rust 1.94.0 Clippy
  is not installed; the component download did not complete and was cancelled.
  No source lint or gate was relaxed to hide the failure.
- Diff whitespace check and Python compilation pass. 155 local documentation
  links resolved and changed/new source files remain below 1,000 lines (the final
  link check is repeated after evidence updates).

Local logs/reports are ephemeral diagnostics, not committed raw evidence:
`/tmp/worker-gateway-all.log`, `/tmp/worker-gateway-admission-final.log`,
`/tmp/worker-gateway-process.json`, `/tmp/worker-gateway-clippy-core.log`,
`/tmp/worker-gateway-clippy-tests.log`, `/tmp/worker-gateway-review.md`.
Public reproducible commands, assertions and results are recorded here; no
credentials, internal endpoints or private provider output are published.
