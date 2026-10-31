> 2026-10-31 发布范围说明：本次 PR 只提交 Avernet。Harness SDK 单独保留本地改动，已改为官方内存 TaskStore/QueueManager 加宿主注入点，移除 H2 和持久化提交去重。下文 Harness 持久化/重启验收为历史证据，不代表默认内存版本。同步最新 dev 后的 A2A 迁移尾号为两种方言的 039（发布前合并未提交的草稿）；当前发布验证另见 pr-validation.json。

# Implementation evidence

2026-10-31 storage/registration update: the current design accepts inline JSON
Cards and keeps only Card, nullable source URL and revision on the Bot. Interfaces
and Provider credentials are derived, and refresh uses bounded process-local
rounds. Older chronological entries below describe their tested historical builds;
their selected-interface/credential/refresh columns and failure-state design are
superseded. This cleanup phase has passed its scoped validation; current scope and evidence are in
`../a2a/minimal-card-registration-v1.md` and `../a2a/minimal-card-validation.json`.
Current trial services have not been redeployed for this storage change.

Updated: 2026-10-30. HTTP phase and the standard gRPC / real MOSN / SOFARPC Triple main path now pass. See `grpc-validation-results.json` and `grpc-mosn-runbook.md` for the new phase. Existing CI baseline failures and the restart limitations remain; this is not an all-CI-green claim.

Current milestone: production HTTP/SSE delivery, durable Task/context correlation,
Card management/periodic refresh, negotiated tools and submission dedup are wired.
Real Ling-3.1-flash text, multi-turn and tool-history acceptance have passed.
Full-Card detail/discovery/search and CLI wire preservation now pass their focused
checks and live Harness Card acceptance. Atomic Card-aware scoped registration
also passes its transactional and listening-HTTP checks. Structured Input exec,
paused budgets and real Workbench approve/deny now pass their main-path checks.
The latest chronological entries below identify exact builds
and remaining acceptance checks; earlier foundation notes are historical.

## Completed foundation

- Added outbound adapter crate `bcs-a2a-http` to the BCS workspace.
- Added per-Artifact append-only text projection with final snapshot calibration.
- Conversion errors leave projection state unchanged. Callers must commit cloned
  state only after successful BCS ingestion; transport wiring is not present yet.
- Five contract tests cover text/tool/text boundaries, Unicode and whitespace,
  legitimate repeated deltas, unsupported intermediate replacement, final suffix,
  recovery revision errors, independent Artifacts and empty final output.
- Direct rustc compilation and execution: 5 passed. This checks this dependency-free
  module only, not workspace compilation or end-to-end functionality.
- `git diff --check` passed. Added Rust source files are below 1,000 lines.

## Historical initial progress (superseded by dated evidence below)

Cargo workspace test dependency resolution hit proxy/network failures; an
escalated Cargo test process was started and must be polled before restarting.
No SDK compatibility, Java build, real model request or E2E has passed yet.

Continue with fixed SDK versions and contract fixtures, Card registration/storage,
Bot-scoped routing and credential references, composition-root delivery wiring,
tool/Structured Input exec mapping, execution/wait budgets, Harness idempotence,
then real cross-process and real-model E2E plus affected module gates.

Harness checkout: sibling `harness-bcs-demo`, clean at initial
inspection, SOFABoot 4.2 / Java 17, includes `mvnw`. Runtime supports THETA_MODEL
and THETA_API_KEY environment configuration. Never put model credentials in source
or evidence. Ask_user remains conditional on actual engine capability.

## Continuation evidence

- Harness baseline `./mvnw test -q` completed successfully under Java 17 /
  Maven 3.9.9: Surefire reports contain 32 tests, zero failures and errors.
  These are existing webhook tests, not A2A acceptance.
- crates.io API returned 403; the sparse registry endpoint succeeded.
  Inspected published a2a-client-lf 0.2.9 source and pinned it with a2a-lf
  0.4.2. SDK requires reqwest 0.13, separate from the workspace client;
  integration must explicitly adapt outbound restrictions rather than reuse
  an incompatible reqwest type.
- Added SDK REST client binding with injected HTTP client and selected tenant;
  compilation remains unverified while workspace dependency resolution runs.
- Cargo process/session 39937 remained live at last observation, reporting
  slow downloads and partial-transfer retries. Do not infer it completed.
- Downloaded SDK reference source under /tmp only; no vendored private runtime
  or model key added. Real model and end-to-end verification remain pending.

## Card validation implementation

- Added SDK-backed Card declaration parsing with exact REST/1.0 selection,
  streaming/text requirements, mandatory extension rejection and static Bearer
  requirement checks. Caller supplies the outbound URL policy.
- Retains raw Card JSON independently of SDK DTOs so unknown skill and top-level
  fields are not lost. Four additional contract cases were added; not yet run.
- Stopped the original Cargo PID 41917 after repeated timeout/partial-transfer
  failures kept the package-cache lock held. Original session 39937 is obsolete.
- A focused temporary Cargo manifest points directly at production lib.rs and
  test files with workspace dependency versions resolved explicitly. Live session
  31882 is resolving dependencies with HTTP multiplexing disabled. This does not
  replace the eventual workspace check.
- Harness Java SDK 1.4.0.Final dependency resolution is live in session 78762.
- Source line counts checked (largest new source: 65 lines); diff check passed.
- Still pending: registration/storage/application/composition integration,
  Java A2A runtime, exec/wait/idempotence, and all real A2A E2E requirements.

## Java SDK encoder foundation

- Maven SDK resolution session 78762 succeeded. Official Java SDK 1.4.0.Final
  provides AgentExecutor and AgentEmitter; inspected actual bytecode signatures.
- Added Harness module bcs-alipay-harness-a2a to its reactor, pinned server-common
  1.4.0.Final. HarnessA2aEvents converts TextDeltaEvent and ModelCallCompletedEvent
  to SDK Artifact updates and tool callbacks to negotiated extension DataParts.
- Added versioned tool observation contract under docs/specs/a2a/extensions/tools/v1.md.
- `./mvnw -q -pl bcs-alipay-harness-a2a -am test` passed after correcting the SDK
  enum spelling and replacing unsupported final-class mocks with real Harness
  TextDeltaEvent fixtures. New encoder test suite: 2 tests, zero failures/errors.
- This is event encoding only: no HTTP controller, Task runtime, execution bridge,
  exec workflow, registration or full E2E is claimed complete.
- Focused Rust Cargo session 31882 is still live; latest poll had no output.

## Harness execution bridge

- Added Bot-scoped HarnessA2aExecutor using the official AgentExecutor API,
  real AgentEngine.streamEvents and existing definition/session contracts.
- Exposed a small observer completion API so the A2A execution path waits for
  actual engine cleanup before reporting completion or cancellation.
- Missing terminal Harness advance fails rather than synthesizing success.
  Approval currently fails explicitly until Structured Input is wired; it is
  not auto-approved. Tools are not yet negotiated by this executor.
- `./mvnw -q -pl bcs-alipay-harness-a2a -am test` passed: 3 new A2A tests.
  Execution test uses a mocked Engine with real Harness events and observation
  lifecycle. This is not a model or HTTP end-to-end test.
- Rust focused build session 31882 remains live; it reported a registry timeout
  and retry at the latest poll. Do not restart it based only on quiet output.

## Spring MVC HTTP binding

- Added Bot-scoped Spring MVC controller delegating protocol parsing and wire
  serialization to official RestHandler (pinned transport-rest 1.4.0.Final).
- Routes cover Card, send/stream, get/list, subscribe and cancel; business routes
  check static Bearer and protocol version before dispatch. Endpoint instances
  must be backed by isolated Bot task runtimes at composition time.
- SSE requests one event at a time; disconnect cancels subscription/callback,
  not the engine Task. Corrected Spring MVC wildcard return-type serialization
  failure by declaring ResponseEntity<SseEmitter> for streaming methods.
- Added MockMvc tests for Bot routing/auth, SDK JSON/header preservation, and
  actual SSE framing/async dispatch. Six A2A module tests passed before the
  final tool negotiation edit; latest verification process is session 46869.
- Tool encoding now checks/activates the requested URI from ServerCallContext.
- Runtime factory, persistent TaskStore, bootstrap bean configuration, Structured
  Input exec, idempotence and BCS registration/delivery remain incomplete.
- Rust focused session 31882 continues downloading dependencies; no Rust
  compilation result is claimed. Full model/HTTP E2E is still pending.

## Persistent SDK runtime integration

- Added per-Bot H2A2aTaskStore with official protobuf snapshots; startup marks
  unfinished Tasks failed, preserves terminal Tasks, and rebuilds the SDK list
  projection. DB failures propagate before updating memory.
- Added bounded per-Bot execution/consumer/transport pools, event-bus processing,
  isolated SDK queues and RestHandler composition. Runtime is not yet registered
  in demo bootstrap; endpoint map/configuration remains to connect.
- Registered actual Spring MVC binding via TransportMetadata SPI, keeping SDK
  transport validation enabled. REST SDK requires empty-string tenant for the
  non-tenant path; corrected null tenant after integration test exposed NPE.
- Aligned protobuf-java and protobuf-java-util to SDK 1.4.0.Final's 4.35.0 to
  resolve generated-class incompatibility with the SOFABoot dependency baseline.
- Full Harness reactor `./mvnw -q test` passed: 42 tests, zero failures/errors
  (32 baseline plus 10 A2A tests). Includes real SDK runtime through MockMvc SSE,
  H2 reopen/Bot isolation/deletion, and failed-write memory consistency.
- Not yet proven: listening HTTP server, real Harness model through A2A,
  Structured Input exec, deadline pause, submit dedup, BCS storage/routing and E2E.
- Rust session 31882 was observed still downloading dependencies; remains live
  until its tool handle confirms completion.

## Listening SOFABoot + real-model text smoke

- Added demo composition for order-demo (ASK unchanged) and independent
  order-text-demo using the pre-existing ALLOW definition for text smoke.
- Added listening-HTTP verification script `scripts/verify_a2a_text.py` in demo.
- Scoped existing Referer machine-to-machine exemption to /a2a/bots/; business
  routes retain Bearer authentication. Corrected AgentInterface constructor use
  so protocolVersion is 1.0 and no erroneous tenant is declared.
- Real stream exposed missing first Task. Runtime now persists and emits initial
  Task before invoking the executor; added initial-Task assertion to integration.
- Found/fixed duplicate event-bus consumers: DefaultRequestHandler already starts
  its processor. ManagedProcessor now provides one start and a shutdown handle.
- Full Harness `mvnw package` passed after fixes (42 tests). Rust focused build
  session 31882 completed: all 9 Card/text contracts passed with official SDK.
- Real Ling-3.1-flash through listening SOFABoot HTTP/SSE passed: task
  07419ff8-4a86-44b6-8ef8-88cc05c23616, 6 events, 3 Artifact updates,
  first Task 105 ms, total 1463 ms, COMPLETED, answer A2A连通成功。
  GetTask and unauthorized-read checks passed. Sanitized evidence saved in demo
  evidence/a2a-text-http.json. No model credential written to source/evidence.
- Current owned demo JVM PID 56549, listening 127.0.0.1:28081; private launch
  metadata at demo .runtime/a2a-e2e-runtime.json. Verify PID identity before stops.
- Full objective remains incomplete: BCS Card storage/registration/delivery,
  tool contract decoding, Structured Input exec and budgets, submission dedup,
  real cross-language BCS E2E and module/architecture checks are still required.

## Rust SDK event projection and cross-language model smoke

- Added transactional staging of SDK StreamResponse into identity/delta/status
  outputs. Checks Task/context identity, rejects pre-Task artifacts and nontext
  artifacts, tracks Artifacts independently, and does not treat lastChunk as Task
  completion. Caller commits state only after successful event ingestion.
- Added 3 projection contracts; all 12 focused Rust contracts now pass.
- Added `bcs-a2a-http/examples/harness_text.rs`, with environment-only credentials,
  explicit loopback scope and no redirects/proxy. It fetches/validates the Card,
  sends with official Rust SDK, and projects real stream updates.
- Listening Java Harness + Ling-3.1-flash cross-language smoke passed with 9
  events and answer Rust和Java的A2A已连通。. Evidence saved in demo
  evidence/a2a-rust-java-text.json. This is not production BCS API E2E.
- Normal workspace Cargo test is live in session 22377 (registry resolution).
  Focused tests used the same source through the temporary manifest, not a
  substitute implementation.
- Still required: production BCS registration/storage/delivery/run/ingestion
  wiring, exec Structured Input and wait budgets, submit dedup, full acceptance
  and architecture gates. These requirements have not been reduced.

## Bot Card persistence and management foundation

- Normal workspace SDK tests completed successfully (12 Card/text/projection
  contracts), replacing the earlier dependency-resolution uncertainty.
- Added SQLite 038 / MySQL 037 additive Bot-row Card columns: raw JSON, source,
  selected interface, credential reference and refresh/CAS metadata. No token
  copied into Card/configuration rows. Existing migrations remain unchanged.
- DbA2aBotStore uses one explicit-column read, one scoped CAS write, or an indexed
  scan returning at most 100 IDs. Writes fence environment, Provider/ref, active
  Gateway mode and revision. Driver errors propagate. Six SQLite storage tests
  pass, including shared conformance, lossless unknown fields, failed writes,
  stale refreshes, tombstones, environment separation and bounded indexed scans.
- Added guarded public Card fetch: complete 10-second timeout, 1 MiB limit,
  JSON media type, no redirects/proxy/credentials, per-attempt approved DNS/IP
  pinning and TLS outside policy-approved loopback. No-cache/no-store is rejected
  explicitly. Four listening-HTTP fetch tests pass, including shared conformance.
- Added A2aBotCore and A2aBotManagementService with authorization before network
  access, local downlink credential readiness, fixed 300-second refresh metadata,
  route/credential change rejection during refresh and CAS-fenced failure status.
  Six application/core tests pass against actual SQLite persistence and real
  Provider authentication; the Card port is a recording fixture in these tests.
- Exposed Provider-admin PUT /providers/{provider_id}/bots/{bot_uuid}/a2a and
  POST .../a2a/card:refresh. Two HTTP route tests passed before the final boundary
  cleanup; verification after cleanup is in progress. Memory-only test servers
  omit durable management and return explicit 503.
- Boundary cleanup: outbound SDK client is now at
  crates/external-clients/bcs-a2a-http (package name unchanged); Card use cases
  are in crates/application/bcs-app-a2a-bot. Shared configuration DTOs are in
  service-api types. Inbound HTTP routes consume only application contracts.
  Added four centralized conformance harnesses and concrete entry tests.
- BCS/HTTP production cargo check passed before this final directory cleanup.
  Fresh check, HTTP route rerun and full SQLite migration-chain rerun are live;
  do not infer those final runs passed. Three migration test expectations were
  updated for SQLite version 38 / MySQL version 37 after detecting old fixed
  version assertions. The first migration run was 39 passed / 3 failed solely
  on these version expectations; the new full-chain A2A test passed.
- Full architecture gate is not clean. Existing checks report dependency-script
  shell errors, historical import violations and missing conformance entries;
  its test discovery also compiles the full workspace. A fresh import scan after
  cleanup has no A2A findings, but existing failures remain. Gates were not
  weakened. No live MySQL verification: Docker is unavailable on this host.
- Still pending: atomic A2A-aware Bot registration, discovery/search integration,
  scheduled refresh execution, production delivery target/stream ingestion/run
  associations and cancellation, tool decoder, Structured Input exec and paused
  budgets, durable submission dedup, real BCS-through-Harness E2E and full gates.
  Management endpoints alone do not activate A2A delivery in BCS.

### Verification after boundary cleanup

- Shared Card fetch/application/core/storage suites rerun from their final
  locations: 16 tests passed.
- `cargo test -p bcs --lib migrations::`: 42 passed after adding the new migration
  to expected version lists. Includes fresh full chain, replay/idempotence,
  upgrade history preservation and the A2A columns test. MySQL checks in this
  suite are static assertions, not live MySQL execution.
- `cargo check -p bcs-http -p bcs`: passed after final directory cleanup.
- Changed Rust source file sizes checked: 43 files, all at or below 1,000 lines.
  `git diff --check` passed. The original oversized providers.rs is unchanged;
  new route error extraction is local to its own small module.
- HTTP route final rerun and closest Provider/container regressions remain live.
  Architecture runner remains active in workspace conformance discovery; known
  existing gate failures are recorded above. Do not claim global gates clean.

- Final HTTP Card routes rerun passed (2 tests). Final BCS compile and all
  42 migration checks are confirmed successful, not merely started.
- Continuation handles: Provider HTTP/Bot storage regression session 81161;
  service-container unit suite session 26783; original full architecture runner
  session 51335. Poll these before starting overlapping verification. The latter
  has known pre-existing gate failures and full-workspace discovery still running.
- Next implementation step is production A2A delivery/registration integration;
  current management configuration does not select an A2A delivery target yet.

- Provider regressions completed: 8 Bot storage tests and 52 HTTP Provider route
  tests passed (session 81161 completed). Container `--lib` check completed with
  zero discovered tests; this is compilation evidence, not unit-test coverage
  (session 26783 completed). Only architecture runner 51335 remains live.


## Production delivery and real-model acceptance (2026-10-29 continuation)

Earlier “pending delivery wiring” entries above are chronological evidence, not
current status. The following implementation and checks supersede them.

- Added the A2A delivery target, transport/source ownership and production mux
  selection without webhook/WS fallback. Protocol, HTTP binding and update mode
  remain separate fields. A2A uses V3 local Session identity.
- Bot availability supports bounded metadata-only batch reads, without loading
  every full Card. Invalid Card configuration cannot fall back to webhook.
- Added durable SQLite/MySQL Run/context tables in the new phase migrations.
  Correlation stores local scope, frozen interface/credential reference,
  message identity, remote Task/context and terminal state. Context mappings
  survive Redis Run TTL. No per-token database cursor is claimed.
- The adapter persists the initial remote Task before accepting delivery,
  consumes the original SSE stream, emits append-only BCS deltas and calibrates
  final Artifacts via GetTask. Cancellation resolves and verifies trusted local
  scope, calls the remote Task ID and requires confirmed remote cancellation.
- Fixed pinned Rust SDK incompatibilities at its edge: canonical POST subscribe,
  opaque Task-ID path encoding and bounded SSE parsing. SDK models are retained.
- Added negotiated tool decoding, staged dedup/conflict checks, canonical BCS
  agent/stream=tool payloads and frozen negotiation per Run. Remote routing and
  local identity claims are discarded. Harness demo Cards declare this extension.
- Card management now includes a Provider-admin full snapshot read, plus a
  lifecycle-owned due scanner. Saved Card refresh intervals remain 300 seconds;
  the scanner checks every 10 seconds, with at most 100 due Cards per batch.
  Fetch or persistence failures propagate, and failed fetches retain the last
  snapshot and save the next retry time using CAS.
- Split previously oversized Provider transport, Bot delivery resolution and
  interaction test modules by responsibility. Existing Provider transport unit
  suite: 38 passed; interaction unit suite: 32 passed.
- Run store conformance/reopen/transaction rollback/scope isolation: 4 passed.
  Card store including batch availability: 7 passed. Complete SQLite migration
  chain rerun: 42 passed. MySQL remains statically checked only.
- Canonical POST subscribe listening-HTTP suite: 3 passed. Tool/projection/
  listening-HTTP delivery suites: 11 passed before the final canonical tool
  payload adjustment; final rerun is tracked below. They include exact Task
  cancellation, first-Task loss without unsafe retry, duplicate observations and
  transient query recovery with final suffix calibration.
- Added Harness scripts/a2a_local.py for owned isolated stack lifecycle and BCS
  public-API acceptance. Credentials remain in ignored mode-0600 runtime files;
  model credentials are process-environment only and never evidence fields.
- Real BCS -> HTTP/SSE -> SOFABoot Harness -> Ling-3.1-flash text acceptance
  PASSED: queued direct chat, group history and multi-turn context. Evidence:
  harness-bcs-demo/evidence/a2a-bcs-text.json. The first test assertion assumed
  chronological history; actual API is newest-first. Fixed it to identify new
  assistant messages by ID, then reran successfully. Both remote Tasks reused
  the same remote context, and the second answer recalled the supplied name.
  This evidence identifies the exact BCS binary hash under test; it does not
  yet prove later tool/scheduler changes in a live stack.
- Java reactor tests through demo-service passed after adding the optional tool
  Card extension. Production cargo check passed with the scheduler integration.
- The old architecture runner was intentionally stopped after recording its
  existing shell/import/conformance failures; it is no longer a live process.
  Architecture gates were not weakened. Full gate acceptance is not claimed.

Still required: atomic Card-aware Bot registration, discovery/search using the
full Card, Structured Input exec approve/deny and paused wait budgets, durable
Harness submission dedup and negotiated retry, async SendMessage/subscribe/cancel
acceptance, true cancellation/restart E2E, public conformance completion and final
affected module/architecture gates. ask_user remains capability-dependent.


### Tool, refresh and submission continuation

- Final canonical tool projection and HTTP transport tests passed. Added a
  nonempty shared A2A delivery-port conformance harness and a concrete entry
  under tests/conformance_a2a_delivery.rs. The older generic empty delivery
  harness is not used as evidence for this implementation.
- Full outbound adapter suite before submission retry: 28 passed. Latest
  delivery suite including negotiated first-Task-loss retry: 7 passed; full
  adapter rerun after that addition is in progress.
- Card application suite including fixed-period scheduling: 7 passed. Card HTTP
  route suite including authorized full Card reads: 3 passed. Run persistence
  suite after frozen extension metadata addition: 4 passed.
- Rebuilt and restarted only the owned isolated BCS/Harness stack, preserving
  its durable stores. The older standalone smoke JVM/BCS were not stopped.
- Real text/context acceptance passed again on binary SHA256
  7bd1cffea89ae62f6a3e5be237903ad0eebc313eb0e19c77aa33c1b37ca1e539.
  Real tool acceptance also passed: Ling-3.1-flash called query_order with
  orderId=DEMO-1001, exactly one tool result was persisted, arguments/name/ID/
  result were retained, and assistant text before/after the tool stayed separate.
  Sanitized evidence: harness-bcs-demo/evidence/a2a-bcs-tools.json.
- Implemented the explicit message-dedup/v1 contract. Harness reserves messageId
  and the initial Task in one H2 transaction before execution, fingerprints the
  SDK request deterministically, rejects conflicting reuse and retains admission
  across restarts/deletion. Duplicate send returns the current Task; duplicate
  stream subscribes or returns a final snapshot. This is at-most-once admission,
  including the documented reserve-before-execute crash tradeoff.
- BCS freezes this negotiation and retries an uncertain initial submission at
  most once, with exactly the same messageId and request. Without negotiation
  it still does not resubmit. Public HTTP fixture verifies two identical requests
  converge on one remote Task after first-response loss.
- Java reactor tests through demo-service after dedup: 47 tests, zero failures
  and errors. Includes concurrent reservation, restart/deletion isolation,
  fingerprint order/context/negotiation and an actual SDK runtime test sharing
  one Task and one execution across asynchronous SendMessage and streaming replay.
  Invalid input uses the SDK HTTP binding's 422 response, not an invented 400.
- Live model acceptance after enabling dedup is not yet recorded; package/build
  and final integration are in progress. Do not equate earlier binary evidence
  with this later change.
- Pinned Structured Input source to upstream commit
  ed844541803ad096dc2e12e084a29d761d91b44e. Clarified canonical DataPart metadata
  envelopes and one pending request per Task in the plan. Approval bridge is
  still pending and is not advertised by the runtime.


### Latest verified build and cancellation finding

- Complete outbound adapter rerun after dedup: 29 tests passed. Card management
  final rerun passed 7 tests. Java final reactor test reports: 47 passed, zero
  failures/errors; production BCS build and Java package succeeded.
- Sequential real-model text/context and real-tool/history acceptance both passed
  with dedup enabled on BCS binary SHA256
  f177fc6d7a893c62bb116dd3218c22f3cfed7f9e356e72a1c36870c28c3eab81.
  The initial parallel acceptance attempt exceeded wait limits while sharing one
  Bot execution slot. Those attempts eventually completed; only the successful
  sequential reruns update the sanitized passed evidence.
- Added local lifecycle/verification instructions to Harness README, explicitly
  distinguishing A2A development status from existing webhook exec approval.
- Real BCS cancellation acceptance found a genuine queue integration gap:
  QueuedGroupPreparation.prepare_abort did not handle A2A transport ownership.
  Added an owner-fenced branch returning the canonical Run and original session,
  without Provider scope abort or WebSocket connection requirements. It compiles;
  closest queue preparation regression and live cancellation rerun are pending.
  Previous transport-only cancel tests remain valid but did not cover this path.
- Cancellation script now uses an order-domain long-output prompt, saves its
  attempt identity privately and labels each waiting stage. The earlier generic
  story prompt could complete before polling observed WORKING; the later test
  reached remote-cancel confirmation and exposed the preparation gap above.
- Source sizes checked in both repositories: no changed Rust/Java/Python source
  exceeds 1,000 lines. Whitespace checks passed.

Outstanding remains: full Card discovery/search and atomic Bot registration,
Structured Input exec with paused budgets, remaining wire/error/restart gates,
and completion of real cancellation verification after the queue fix. The goal
is not complete; prior passed E2E evidence does not claim approval or cancellation.


### Cancellation boundary diagnosis and fixes

- Queue abort preparation regression: 6 tests passed, including exact A2A Run/
  session and rejection when Provider/Bot ownership changes. The initial fixture
  used a timestamp before admission; corrected it to the current admission clock.
- Added safe abort-stage diagnostics: only error variants and a fixed local reason
  vocabulary are logged, never request bodies, target Debug values or secrets.
- Live cancellation then exposed an independent Java binding defect: SDK
  RestHandler.cancelTask takes (context, tenant, body, taskId), while the controller
  had passed taskId before body. Corrected the order. A real SDK HTTP runtime test
  now verifies cancellation reaches the actual Task ID.
- Native Harness cancellation with a mocked emitter passed, but combining the
  real Harness engine, real SDK emitter/runtime and HTTP cancel reproduced a 500.
  The engine worker's interrupt flag survived cleanup and caused SDK queue
  publication to fail. Added publishCanceled: clear interruption only around
  terminal publication, restore it afterwards, and mark cancellation confirmed
  only after enqueue succeeds. The combined regression went from failed to passed.
- The combined test uses the actual Harness engine and SDK, with a controlled
  never-ending model (no network or credentials). It complements, not replaces,
  the required real Ling model cancellation acceptance.
- Added Servlet API only as a demo test dependency for this integration boundary.
- Current Java package/test rerun and final live cancellation rerun are pending.
- Static port-purity gate passed. Import gate still fails on existing findings;
  its fresh scan has no A2A findings. Shared delivery conformance rename rerun
  passed. No gate was weakened and no full architecture success is claimed.


### Confirmed real cancellation acceptance

- Final Java reactor package/test passed: 50 tests, zero failures/errors.
- Rebuilt and restarted the owned isolated stack with corrected HTTP argument
  ordering and interruption-safe SDK terminal publication.
- Real Ling-3.1-flash cancellation PASSED on BCS binary SHA256
  971a6b679b601c4d5ff118cb477a43f08c2568867cd8e4657d5001cb9ce528d9:
  active Task observed, remote Task ID differs from local Run ID, BCS managed
  cancellation reaches that Task, remote state becomes TASK_STATE_CANCELED,
  and BCS reports terminal cancelled. Sanitized evidence:
  harness-bcs-demo/evidence/a2a-bcs-cancel.json.
- Lifecycle script port probing now permits released TCP TIME_WAIT sockets while
  still testing bind/listen ownership, avoiding false occupied-port failures.
  No unrelated process was stopped. Requests expose BCS chat version 3 so queue
  delivery metadata is available during acceptance diagnosis.
- The final combined native-engine/SDK regression reproduced HTTP 500 before
  publishCanceled was fixed, then passed. This establishes why the two earlier
  separate tests alone missed the worker-interruption publication defect.

Current live owned stack: BCS 21082 and Harness 28082. It uses the corrected
build above. Earlier standalone smoke services remain untouched. The full goal
remains active: atomic registration, full Card discovery/search, Structured Input
exec and paused budgets, remaining wire/error/restart checks and final gates are
not complete. Do not mark the goal complete from these milestone successes.


### Full Card query and discovery acceptance

- Public authorized GET /bots/{id}/agent-card returns the complete stored Card.
  Detail, paged search, discovery and typed CLI JSON models preserve optional
  agent_card including skill descriptions and unknown fields. Existing Bot
  ownership/visibility rules still apply; Card claims never override them.
- Discovery/search predicates use Card names/descriptions and skill IDs/names/
  descriptions/tags before SQL count/pagination. Known nonmatching A2A Bots do
  not fall back to legacy capabilities. Literal search escaping is covered.
- Raw Card reads batch at most 256 identities; matching payloads only are loaded
  for selective discovery. No Card cache or N+1 payload query was introduced.
- Split oversized Bot query, Provider store and route/conformance test modules
  by responsibility; retained their regression suites. Final focused suites:
  application Card 4 passed, SQLite Card storage 8 passed, HTTP Card queries
  3 passed, protocol wire 2 passed, service-api contracts 10 passed. CLI
  all-target compilation passed. Provider/storage/HTTP regression suites also
  passed; MySQL has static SQL validation only, not live-dialect acceptance.
- Live SOFABoot source Card equals BCS raw/detail Card; Card-only skill text
  matches discovery/search; manual refresh succeeds; unauthenticated reads are
  denied consistently with existing Bot detail policy. Evidence:
  harness-bcs-demo/evidence/a2a-bcs-card.json; BCS SHA256
  4575529997916ec4708b51dba625027c61c996b71f2f589f5424dec0048fbbd7.
  This is real HTTP Card acceptance, not a model-generation test. Later stored
  Card object-shape validation and CLI DTO additions have focused test/check
  evidence but are not included in that live binary hash.
- Full objective remains incomplete: atomic registration, Structured Input exec
  and paused budgets, remaining error/restart tests and final architecture gates.


### Atomic Card-aware registration and REST error compatibility

- Existing legacy/OpenAPI registration accepts optional agent_card_url through
  an explicit RegisterService use case. It requires verified scoped v2 gateway
  registration without webhook_url and current Provider management authority;
  self-service alone cannot bind a remote Agent. The signed Human remains the
  ownership/audit initiator, and Core assigns the canonical Bot identity.
- Application fetches the guarded public Card outside database transactions.
  Core rechecks Provider/downlink/credential admission; SQL commits Bot, binding,
  Card, Human, ownership, profile and audit together. The Card adds one checked
  single-row statement to the existing transaction. No migration was rewritten.
- Shared registration service conformance and real application/Core/SQLite tests:
  4 passed, including mid-transaction SQLite failures at Card and later profile
  writes, no half lifecycle rows, rejected Card, self-service denial before fetch,
  strict duplicate conflict and full unknown-field retention. OpenAPI route
  suite: 10 passed. Existing combined affected regression: 281 passed, 4 ignored.
  The final focused registration/route rerun passed after Core-owned ID cleanup.
- REST GetTask, CancelTask and POST subscribe now preserve nested ProtoJSON
  error.details protocol ErrorInfo reasons as well as the pinned SDK flat shape.
  Invalid untyped details are ignored individually, arbitrary text/HTTP status
  does not invent protocol reasons, bodies remain bounded and are excluded from
  error messages. Listening-HTTP decoder suite: 5 passed.
- Production build passed. Restarted only the owned BCS process; the live Harness
  JVM and older smoke services were untouched. Live registration acceptance passed
  on SHA256 d563fe7d04948b009de4cfee669e03b5b7c6f9ca9483c224b3b9a99f442d2c75:
  invalid Card rejected, same ref then succeeds, full Card is committed/visible at
  creation, no webhook endpoint selected and duplicate returns 409. Sanitized
  evidence: harness-bcs-demo/evidence/a2a-bcs-registration.json. This acceptance
  does not invoke the model. The isolated stack remains configured for Ling-3.1-flash.
- Updated registration OpenAPI contract, plan and module boundaries. Lifecycle
  script redacts query strings from error messages so register tokens cannot be
  included in script failure output. New credentials stay in private runtime files.
- Port-purity gate passed. Import gate still reports pre-existing contract imports;
  its current output has no A2A/register changes. MySQL live chain and full final
  architecture acceptance remain unverified; these gates were not weakened.
- Still required: Structured Input exec/paused budgets, frozen-target cancellation
  when current Card resolution fails, remaining restart/error/public conformance
  checks and final full-scope acceptance. ask_user remains capability-dependent.


### Latest acceptance and next boundary

- On the latest registered build d563fe7d04948b009de4cfee669e03b5b7c6f9ca9483c224b3b9a99f442d2c75,
  full Card detail/discovery/search/manual refresh acceptance passed again.
  Real Ling-3.1-flash BCS text and multi-turn context acceptance also passed.
  The corresponding Card/text evidence files now identify this exact binary.
- Captured a real Java SDK Task-not-found HTTP response (synthetic missing Task,
  no credentials or user data) as tests/fixtures/java-task-not-found.json in the
  outbound adapter; replay through the Rust client preserves TASK_NOT_FOUND.
  Final listening-HTTP error/subscription suite: 6 passed.
- Pinning work for the next Structured Input boundary verified upstream commit
  ed844541803ad096dc2e12e084a29d761d91b44e. The plan now records the fixed source,
  canonical wire URI, request-id envelope and one-pending-schema restriction.
  Multi-tool approvals must be aggregated under one request; they cannot be
  implemented as multiple simultaneous input_schema parts. Exec transport and
  paused-budget implementation are still pending, not claimed as delivered.
- Final diff whitespace check passed and all changed/added source files are
  below 1,000 physical lines. No commit or push was performed.


### Structured exec input codec foundation

- Confirmed Harness 0.1.1's native confirmation model: an advance finishes in
  WAITING_FOR_CONFIRMATION, its observer cleanup completes, then a new advance
  takes ConfirmationInput for the original interaction and exact tool-call IDs.
  No long-lived engine subscription is needed while waiting for a human.
- Added Harness StructuredExecInput using official SDK DataPart/Message models.
  One request schema covers a batch, permits schema-valid partial decisions,
  accumulates equal repeats idempotently and rejects conflicting/stale/expired
  input atomically. Accepted fields become const values in the updated schema;
  actual Harness input is unavailable until all tools have a decision.
- The codec snapshots tool arguments and returns independent schema trees, binds
  names/IDs/digests/expiry in a documented exec annotation, bounds payload size,
  and distinguishes correlation rejection from schema validation feedback.
- This is an input codec foundation only. The existing executor still rejects
  approvals until production negotiation/continuation is wired. No Card has
  been changed to advertise this capability; no exec E2E is claimed.
- The business profile is documented at docs/specs/a2a/structured-input-exec/v1.md.
  It reuses the upstream Structured Input URI/envelope rather than adding an RPC.
  Focused Java reactor test run passed: A2A module 19 tests, zero failures/errors,
  including 3 new StructuredExecInput tests. The run did not invoke a model.


### Harness native exec continuation and real-model acceptance

This supersedes the earlier codec-only status. Harness now implements the
Structured Input execution bridge; BCS exec interaction/budget integration is
still incomplete.

- Executor ends the actual Harness advance and waits for observer cleanup before
  INPUT_REQUIRED. It holds only pending confirmation/encoder state while waiting,
  reuses the same Task/context and resumes with the native ConfirmationInput only
  after all tool decisions are accepted. Artifact cursors and committed text span
  the two advances. Repeated equal decisions are no-ops; conflicting/stale input
  repeats the schema without running tools; expiration fails without execution.
- Added protocol-neutral execution completion to the shared session store. A2A
  lifecycle IDs are namespaced by Bot and Task; failed/canceled executions block
  their Harness session. Existing startup interruption also protects unfinished
  A2A session work. Missing records and failed writes cannot report completion.
- Native HTTP SSE test exposed the pinned Java SDK keeping interrupted consumers
  open. Spring MVC now uses official protobuf models to close/detach its consumer
  at INPUT_REQUIRED/AUTH_REQUIRED per fixed REST 1.0.1 section 11.7, without
  canceling the Task. Continuation publishes an initial WORKING snapshot so the
  previous input-required snapshot cannot immediately close the new stream.
- Decisions are persisted before engine continuation in Task.metadata at the exec
  profile key: version 1, requests keyed by input request ID, each with accepted
  tool-call decisions. The store reserves this key and fences stale SDK Task
  snapshots. Proofs survive completion/reopen and remain Bot-scoped; failed writes
  never publish an in-memory acceptance. Maximum 64 batches/240,000 encoded bytes.
  One snapshot write per accepted human patch; no extra per-token acceptance query
  or new acceptance table. Acceptance is not a claim of tool execution.
- Demo order-demo Card now advertises Structured Input and the business profile;
  order-text-demo remains the existing text test Bot. Unsupported/unnegotiated
  approval requests fail explicitly. No automatic approval was introduced.
- Full Java package/reactor: 62 tests, zero failures/errors. Native Harness plus
  actual SDK HTTP covers approve/deny, partial batch/repeat/conflict/stale replies,
  closed waiting SSE, same-Task continuation stream, wait cancellation, expiry,
  preserved pre/post-approval text and interrupted-session protection. Store tests
  cover persisted acceptance after stale snapshots/completion/reopen, isolation,
  conflict rejection and failed-write consistency. Existing cancellation and
  webhook tests remain passing.
- Direct listening SOFABoot -> real Ling-3.1-flash exec acceptance PASSED on Jar
  SHA256 8895ead0d2bbdd860c7de9c3e85531dd2034e8bda9a4f3960a08be4a768c89ad:
  allow_once observed exactly one query_order start/result; deny observed zero.
  INPUT_REQUIRED closes the stream, continuation retains Task/context, final
  GetTask retains the accepted decision proof and a nonempty final Artifact.
  Evidence: harness-bcs-demo/evidence/a2a-exec-http.json. This does not include
  BCS interaction.resolve; the script explicitly labels its scope.
- BCS real-model text/multi-turn and exact Task cancel acceptance both passed
  again with that Java Jar and BCS SHA256
  d563fe7d04948b009de4cfee669e03b5b7c6f9ca9483c224b3b9a99f442d2c75.
  Updated text/cancel evidence now records both binary hashes. Only the owned
  Harness JVM was restarted; BCS and older standalone smoke services were left
  running. Credentials remained private environment/runtime inputs.
- Captured/normalized a real native SDK HTTP Structured Input Message as a shared
  Java golden fixture and Rust conformance input. UTF-8 title preservation and
  ProtoJSON integer-valued double annotations (1.0) are checked. Rust SDK/projection
  wire test passed. This preserves the envelope, not yet BCS exec behavior.

Next required boundary: implement the BCS profile/schema decoder and trusted
interaction association; wire InteractionProviderPort to A2A using frozen Task
scope; use durable Task metadata proof for asynchronous decision acceptance;
stop SSE while waiting and preserve projection state for resubscription. Before
publishing cards, pause execution time and extend BOTH managed-delivery deadlines
and Run/Redis TTL under their existing Core/service boundaries. Add separate wait
and total caps. Resolve/cancel must bypass the ordinary Session send queue. Then
prove full BCS -> Harness -> real model approve/deny E2E. Remaining cancellation
Card-refresh handling, restart/public conformance/MySQL/final architecture checks
are still required. The overall goal is active, not complete.

### BCS budget foundation and native exec wire decoder

This is supporting implementation, not activation of BCS exec delivery. The
listening BCS/Harness processes have not been replaced for these changes.

- Added transport-independent RunBudgetCoreService and RunBudgetCore. Execution
  time is charged at pause; human wait uses its own deadline and a fixed total
  cap. Repeated observations cannot renew the wait. Resume preserves remaining
  execution time; expired/exhausted/backwards-clock/overflow cases fail.
- Managed delivery now reschedules only an exact canonical live Send Run through
  its existing CAS transaction. It preserves request/attempt/slot/transport and
  rejects queued, canceling, expired, foreign and terminal work. Identical
  updates are idempotent. Canonical lookup is bounded; stale CAS retries stop
  after three attempts, while storage errors propagate immediately.
- Memory/Redis Run-context stores reschedule context/active scope/transport and
  alias lifetimes under a per-Run local lifecycle gate. Terminal claims/removal
  serialize with updates. Tracker changes preserve existing admitted/independent
  output lineage and cannot mint retired producers. No multi-instance lease is
  claimed. Redis uses UpdateOnly and writes canonical context last, with eleven
  cache operations for two aliases/scope TTL extension and no full scope scan.
  Earlier TTL/index writes can remain after failure; this is not SQL/Redis atomic
  commit. Future orchestration must fail closed/cancel before publishing cards.
- Shared Core, managed-delivery, repo and Run-context contracts cover budget
  changes. Focused SQLite tests exercise normal changes and injected transaction
  failure preserving the old deadline/slot. Cache tests exercise bounded work,
  failures, expiry during write, and forced terminal/deadline interleaving.
  The policy/tracker test run passed 78 library tests plus one budget conformance
  entry. Earlier focused storage/cache runs passed; a combined regression run
  is being checked separately below.
- Added BCS structured_exec decoder using the captured native Java SDK HTTP
  fixture. It validates Task/context, request/tool/argument bindings, profile,
  expiry, enum/const consistency, immutable accepted decisions and size limits.
  Unsupported schema constraints fail explicitly. Standard reply DataParts
  preserve the original Task/context; exact persisted Task receipts are required
  for acceptance, and do not claim execution. Six decoder tests and the shared
  native envelope test pass, including 64-tool partial decisions and malicious
  or incompatible inputs. No network/model calls are involved in these tests.
- Delivery targets now preserve extension required flags and full params, rather
  than losing them while projecting URI strings. Core does not interpret draft
  semantics. Required Structured Input declarations with an incompatible draft
  or business profile fail validation; optional incompatible profiles remain
  unnegotiated. Bootstrap still does not advertise Structured Input support.
- Plan now specifies that the last approval resumes budget charging BEFORE the
  continuation is transmitted, since Harness can execute before its RPC ACK.
  A rejected/unknown continuation must not reset the original input wait.

Remaining main path: connect the production waiting/resolution driver, managed
budget/context updates and interaction service; preserve projection state while
SSE is closed; confirm acceptance through Task receipts; then verify full BCS
approve/deny against native Harness and Ling. ask_user capability, refresh-error
cancellation, restart/public conformance, live MySQL and final architecture gates
remain part of the overall work. No commit/push was performed.

Validation for this increment:

- Combined budget foundation: 158 tests passed, zero failures (library,
  Core/application/port contracts, SQLite rollback and Redis race/TTL cases).
- A2A adapter, Bot and chat-run-store package executable tests: 308 passed,
  zero failed. The initial command's doc phase hit a crate-loading error;
  a separate three-package `--doc` rerun passed. It is not counted as an
  additional executable-test pass.
- Final declaration-parameter/decoder/listening-HTTP transport run: 19 tests
  passed, zero failures, including optional/null required flags and complete
  parameter retention. No test runner or gate was disabled.
- Port purity passed. Dependency gate still aborts in unchanged
  `scripts/ci/check-deps.sh:36` on an unbound variable; import-rules gate still
  reports repository-wide forbidden-import findings, including unchanged
  session_callback.rs, repo/bot.rs and types/bot_control_plane.rs. Architecture
  gates are not claimed green; their rules were not weakened.
- The old Provider target match was updated to reject the A2A variant where
  HTTP webhook was expected; its preexisting 1,255-line test file was split into
  a parent and delivery/auth child without changing other test assertions.
  All modified/untracked source files remain within 1,000 physical lines.
  `git diff --check` passed.
- `cargo check --locked -p bcs --all-targets` passed against the final typed
  extension declarations. The locally running binary is still the earlier
  verified build; this command does not deploy the new code.
- Final cache-specific rerun passed six tests after adding the missing-scope
  expiry failure case: a false expiry result cannot publish a new canonical
  deadline even after preceding cache writes.

### Production BCS exec waiting owner and real-model Workbench acceptance

This supersedes the preceding pending interaction/budget-wiring notes.

- Bootstrap wires the A2A InteractionProviderPort into the existing interaction
  mux/service and injects the generic RunBudgetCore plus managed delivery.
  The transport holds only a weak interaction-service reference. Each active
  Run owns a bounded 64-command resolution channel, its frozen target and
  projection. Waiting drops the actual SSE body and does not query/resubscribe
  until a reply, cancel or deadline. The existing Session slot remains held.
- The adapter persists a validated pending input message and budget in the
  existing Run JSON/CAS. Existing records without these fields decode unchanged;
  file reopen preserves the new fields. Frozen SQL migrations were not changed.
  No SDK, protocol schema or HTTP type was added to message-flow Core/store.
- Cards are published only after managed deadline, Run context/TTL and
  correlation persistence succeed. Partial decisions retain the original wait
  and remaining execution budget. Before the last decision is sent, execution
  resumes and both deadlines are adjusted; the network/engine interval is
  charged. Request/tool identity and accepted choices remain immutable.
- Replies use standard SendMessage(returnImmediately=true), original Task/context
  and a stable message ID. A successful RPC or WORKING state is insufficient:
  exact persisted Task metadata must acknowledge the request/tool/decision.
  Receipt validation checks at most four snapshots (response plus three queries).
  Missing/conflicting receipts fail closed and cancel, without claiming approval.
  Local resolved events explicitly carry receipt=accepted, not execution success.
  They precede terminal calibration so a fast completion cannot invalidate a
  decision already accepted by Harness. Existing interaction management handles
  the callback racing its own accepted-fingerprint commit.
- The original projection survives the wait. Query/subscription recovery after
  the final receipt is bounded by the existing three-attempt policy. A transient
  subscription error never resubmits the decision or repeats prior text.
- New `[a2a_input]` config: wait_timeout_ms defaults to 600000 and
  total_timeout_ms to 1800000; validation requires 0 < wait <= total <= 86400000.
  The public local config declares both. Existing execution timeout remains
  independent; Harness's default input expiry is also ten minutes. Earlier
  configured remote expiry wins. Config defaults/bounds/unknown-field test passes.
- Six listening-HTTP tests use real InteractionManagement, MemoryInteractionStore,
  ManagedMessageDelivery and Run-context stores: approve/deny and preserved text,
  multi-tool partial/duplicate/conflict plus authorization, successful RPC without
  receipt, wait expiry without polling, exact waiting cancellation, and transient
  post-approval subscribe recovery without resending. All pass. The combined
  final decoder/delivery/transport run has 19 passes, zero failures. Earlier
  adapter/chat-run-store/interaction executable regression had 113 passes; the
  new storage compatibility/reopen run has five passes. Port purity passes.
- Real BCS Workbench WS -> A2A HTTP async continuation -> native Harness ->
  Ling-3.1-flash approve/deny PASSED on final BCS SHA256
  c938e1fe3fe8e1c0d04ec2d05b298e503ec4b343b26a3521ce0c587d356a7c0e
  and Java Jar SHA256
  8895ead0d2bbdd860c7de9c3e85531dd2034e8bda9a4f3960a08be4a768c89ad.
  Evidence: harness-bcs-demo/evidence/a2a-bcs-exec.json. Approve executes
  query_order exactly once; deny executes zero times. Both retain Task/context,
  pause execution during human wait, persist the accepted decision and complete
  with final history. The script uses a legitimately managed Bot's full
  Workbench view, respecting existing approval visibility. A first attempt with
  the default limited view received no card; its task was canceled through BCS
  before rerunning with the explicit view. No permission rule was weakened.
- Added reproducible configure-exec and verify_a2a_bcs_exec.py scripts. The latter
  uses pinned public websocket-client 1.8.0 in an ignored virtualenv, reads private
  runtime credentials without printing them, records only synthetic results and
  build hashes, and attempts BCS cleanup on failed cases. Only owned BCS 21082
  was replaced; Java 28082 and unrelated smoke services remained running.

Overall goal remains active. Remaining acceptance includes cancellation when
current Card refresh/availability fails, explicit cross-process context/restart
checks, full public protocol/affected-module gates, live MySQL migration-chain
validation, and the final requirement audit. ask_user remains conditional on a
verified native Harness pause/resume capability; exec is not deferred.

Additional closing checks for this increment:

- The same c938e1fe BCS build and unchanged Java Jar passed real-model direct
  text, group multi-turn context, tool-history and exact remote Task cancel
  again. Their evidence files now record the matching build hashes.
- A subsequent source guard permits an already-completed approval snapshot to
  serve as historical immutable binding after its old expiry, while new requests
  and actionable replies still validate current expiry. This prevents a later
  confirmation batch from being rejected solely because an earlier completed
  batch aged out. It also rejects acceptance publication after execution expiry.
  The decoder/listening-delivery rerun passed 13 tests. This small post-E2E guard
  has not yet replaced the live c938e1fe binary and must be included in the next
  final-build acceptance, rather than silently attributing it to prior evidence.
- Python scripts compile; configure-exec succeeds against the listening BCS.
  `git diff --check` passes, and every modified/untracked source file remains
  within 1,000 lines. No commit/push or external publication was performed.

### Cancellation with failed Card refresh; live MySQL dialect gate

- Added the cancellation-only BotRegistryCoreService::resolve_abort_target
  contract. BotCore permits the last validated Card after refresh failure, while
  normal sends/runtime availability still reject that configuration. Current
  affiliation and enabled Provider/binding/downlink/credential checks are retained.
  Extension negotiation is skipped for cancellation. Queued and active-scope
  aborts use this resolver; exact Task/context and original endpoint still come
  from the adapter's durable canonical Run. This adds no lookup beyond the four
  existing control-plane reads and no HTTP Card probe/cache.
- Core regression passes nine Provider target tests including stale Card/new-send
  rejection and retained disabled/affiliation guards. Queue preparation regression
  passes six tests. Listening A2A transport conformance passes seven tests, now
  explicitly changing the current target endpoint to an unreachable address
  before exact cancellation, proving the frozen endpoint is used. Registry
  contract library test passes after extracting its tests from the nearly full
  source file; all modified source remains below 1,000 lines. Port purity passes.
- Real HTTP fault acceptance PASSED: a controlled Card server first published the
  Harness Card, then returned 503. BCS refresh persisted an error and retained
  the full old snapshot. With that error still present, Workbench chat.abort
  successfully canceled the original Task/context; only afterward did the script
  restore the original Card URL. Evidence:
  harness-bcs-demo/evidence/a2a-bcs-card-cancel.json. Reproducible script:
  scripts/verify_a2a_card_cancel.py (same pinned WebSocket test virtualenv).
- Both this fault test and real-model approve/deny now pass on BCS SHA256
  7e7fb17eca6d968ab3eef16ffcca1db082827cdf8be272ebbc74991429a6acd4,
  with unchanged Java Jar 8895ead0d2bbdd860c7de9c3e85531dd2034e8bda9a4f3960a08be4a768c89ad.
  This build includes the previously noted historical-input/expiry guards; those
  changes are no longer only unit-tested or absent from the listening binary.
- Installed public Homebrew mysql@8.4 8.4.13_1 with post-install skipped, then
  initialized an isolated runtime data directory and disposable empty schema.
  The owned server binds loopback and is not a registered system service.
  Test credentials remain in ignored mode-0600 runtime data and are never logged.
- New a2a_mysql conformance entry applies ALL 37 frozen MySQL migrations through
  the existing shared migration-chain helper. Real MySQL 8.4.13 PASSED full Card
  and Run/context contracts, lossless UTF-8 Card storage, skill/description/tag
  JSON search, literal percent/underscore/tilde escaping, environment isolation,
  availability reads, Card-write failure propagation and transactional rollback
  when context insertion fails. No historical migration was rewritten. Evidence:
  harness-bcs-demo/evidence/a2a-mysql-storage.json records version, checks and
  SHA256 of every applied migration, excluding credentials/DSN.
- Reproduce the MySQL gate with BCS_TEST_MYSQL_URL set to a disposable EMPTY
  schema, then `cargo test --locked -p bcs-bot-store --test a2a_mysql --
  --ignored --nocapture` from apps/bcs. A dirty schema is explicitly rejected;
  this test does not clean or migrate a user's existing schema.

Remaining: explicit cross-process context/restart verification, finish the broad
affected-module/public/architecture checks and audit every plan requirement.
The broad affected-module run is currently in progress; its result is not yet
claimed. Overall goal remains active.

### Real BCS and Harness process restart acceptance

- Added two-phase verify_a2a_restart.py. Prepare creates a completed BCS group
  conversation plus a native asynchronous SendMessage(returnImmediately=true)
  Task with an explicit initial messageId. It stores only private verification
  state in the ignored runtime directory.
- Stopped/restarted ONLY the owned BCS 21082 and Harness 28082 processes after
  verifying PID/start time and launch paths. Retained SQLite, H2 and native
  LocalFileAgentSessionStore. Reused unchanged built binaries/Jar. Credentials
  were recovered only in process memory/private runtime inputs, never printed.
  The older independent smoke services and MySQL test process were untouched.
- Verify PASSED after both actual process identities changed: the completed
  asynchronous Task is queryable; resending the identical messageId/input returns
  the same completed Task/context and exact Artifact; ListTasks in that context
  still contains exactly one Task. A new BCS message in the pre-restart Session
  creates a new Task in the SAME remote context, and real Ling remembers the
  synthetic marker from the preceding turn. Evidence:
  harness-bcs-demo/evidence/a2a-bcs-restart.json.
- This proves completed-context continuity and durable initial-message dedup,
  not lossless in-flight event/approval recovery. The latter remains explicitly
  outside the single-instance first phase. No restart cursor/lease was added.

The broad affected-module test run remains live in this increment. Completion
still requires its actual result, the remaining public/architecture checks and
a requirement-by-requirement final audit. Prior MySQL/restart pending notes are
now superseded by the real evidence above. Overall goal remains active.

The first broad run passed the BCS library (354 passed, five existing ignored)
then stopped at an old integration assertion expecting 37 SQLite migrations.
Updated bot_authority_migration and provider_slug_migration to expect the actual
38-version chain, preserving the authority-037 assertion and explicitly checking
the appended bot_a2a-038 migration. Their focused rerun passed. No migration or
data-preservation assertion was removed. The broad run is now being repeated
with --no-fail-fast so every affected target is exercised even if another target
fails; the command still returns failure if any test fails.

Architecture follow-up found two new import-path violations in the outbound
adapter/test and the budget harness did not use the repository's required
run_budget_core_service_contract_tests name. Added public Service API facade
exports, changed those consumers and renamed the shared harness/caller. These
are source facade/test naming changes, not runtime policy changes. The focused
verification of this final naming/facade change is still required after the broad
run. No check was disabled or allowlist expanded.

Also repaired two check-script defects encountered while executing the required
architecture gates: check-deps.sh now braces ${d} before its adjacent non-ASCII
diagnostic so Bash does not interpret a different variable and abort; the R25
helper's file iterator is local so it cannot erase its caller's source path via
Bash dynamic scope. Checks still reject forbidden dependencies/missing harnesses.
The existing architecture smoke wrapper re-runs the full architecture gate;
it remains in progress and already reports existing repository dependency/import
findings. Architecture is not claimed green. The earlier unbound-variable abort
has been replaced by actual dependency diagnostics.

The isolated MySQL process has been cleanly stopped after its successful gate,
with PID/start/path checked before SIGTERM. Private test data/config remains
under the ignored runtime; no system MySQL service was registered.


### SSE lifecycle and uncertain cleanup continuation (2026-10-29)

- Java now sends a comment heartbeat every 10 seconds through a shared daemon
  scheduler. Each consumer detaches exactly once on disconnect/write failure,
  INPUT_REQUIRED/AUTH_REQUIRED or application shutdown. Disconnect does not call
  CancelTask. Official SDK child queues are bounded at 1,000 events and its
  publisher buffer at 256; a blocked-consumer test emits 2,500 events, verifies
  native Task completion and zero cancellations, then detaches the consumer.
- Full offline Java reactor package passed 67 tests, zero failures/errors/skips.
  Final heartbeat/slow-consumer focused tests also passed. The resulting Jar was
  deployed only to the owned Harness 28082 process, retaining H2 and sessions.
  SHA256: 8e7b413b593226e5a8b17af2bdeca140278399dcc8aa925a30230e02a20bff70.
  Real Ling text/multi-turn, tools and cancellation were rerun successfully with
  that Jar; their evidence files identify the exact BCS/Java builds used.
- Fixed a tracking-failure ordering defect: the adapter previously emitted a
  local Error before confirming remote cancellation. It now marks the canonical
  managed Send Unknown (CancelUnknown when already cancelling), invalidates input
  cards, attempts exact cancellation and, if necessary, queries the same Task.
  Only a confirmed remote terminal permits an Error terminal. Unconfirmed cleanup
  persists Unknown without releasing the execution slot or claiming completion.
- The lifecycle method rejects wrong scope, request aliases and unsent work;
  preserves attempts/cancellation intent; is idempotent for uncertain/terminal
  work; and uses bounded exact lookup/CAS (at most three conflict retries).
  Shared Memory/SQLite conformance covers these semantics. The focused budget/
  deadline gate passed 46 tests, including its existing full-chain migration tests.
  Listening-HTTP transport/interaction gates passed 14 tests, including a real
  managed-slot assertion when cancellation is rejected and Task remains active.
- Both repositories' changed/additional source sizes were checked: Avernet 187
  files and Harness 66 files, none over 1,000 lines. Avernet diff whitespace check
  passed. Counts describe this checkpoint, not a permanent exemption.
- Architecture import gate currently fails on 69 findings. Every reported source
  line was checked against HEAD and already exists verbatim there; the new A2A
  adapter has no remaining direct core/application import findings. Whole-archive
  architecture gates are still running their workspace test-discovery builds;
  earlier reports also expose existing dependency/naming/conformance failures.
  No gate or baseline was relaxed. Two gate diagnostic/scoping defects were
  repaired so findings identify the actual path/dependency.

Remaining: deploy the latest BCS uncertain-cleanup build and recheck live exec,
finish the broad affected-module/architecture runs, and complete the requirement
coverage audit. In particular, typed errors survive the bounded REST client but
end-to-end errorKind/errorCode propagation through delivery still needs review;
this checkpoint does not claim that every plan requirement is complete.


Latest live-build acceptance: the owned BCS was rebuilt/restarted with SHA256
18ece69d334e0415c2bd9da46bc871fb5ee2c833f216077bf68c53ae0cb57d57,
retaining its SQLite data and signing configuration. Real Workbench exec
approve/deny PASSED against the new Java Jar above: unchanged Task/context,
waiting budget pause, persistent acceptance, approve exactly once and deny zero
execution. Real Card HTTP failure followed by cancellation while refresh_error
still exists also PASSED on this pair. Updated evidence files contain these
hashes. The acceptance-checklist.md now maps plan requirements to evidence and
explicit remaining work; overall completion is still pending.


### Initial rejection, cancellation races and native output-profile checks

- Initial SendStreamingMessage and asynchronous SendMessage now use the same
  bounded ProtoJSON/SSE error binding as query/cancel/subscribe. Official SDK
  request/event DTOs remain authoritative; no HTTP framework enters Core.
  This closes the SDK decoder gap for nested Status/ErrorInfo on submission.
- A complete initial HTTP 400/401/403/404/405/406/415/422 rejection publishes
  a2a_protocol, a recognized reason or HTTP_<status>, and sanitized HTTP/type/
  reason details. Raw peer message/metadata/unknown details are excluded.
  408/409/429, 5xx, timeouts and incomplete responses remain Unknown; a typed
  reason alone never proves non-execution. No anonymous fallback is added.
- Real BCS queue/ingestion/Workbench fault acceptance PASSED using a controlled
  HTTP peer: typed 400 and plain 403, exactly one request per rejection, no raw
  diagnostics, durable Failed without invented Task ID, and another Send in the
  same Session. This is not a model test. Evidence a2a-bcs-rejection.json records
  the tested pair (BCS 1a4c57d4e017e0a404f07ffaf4e366fac8f284955b1e660880acdc43acfcbf67).
- Query/cancel/continuation request failures now retain safe HTTP/typed reasons
  without inventing Task terminals. An already-confirmed Canceled Run returns its
  exact result idempotently, after scope checks, with no second RPC. Completed/
  Failed work returns no aborted Run. TASK_NOT_CANCELABLE permits one exact
  GetTask within the same cancellation deadline; completion is not relabeled
  cancellation, and Final remains the text driver's responsibility.
- A2A full lib/tests gate PASSED 53 tests before the last cancellation refinement.
  Latest listening transport/interaction suites then PASSED 18 tests, covering
  that refinement. These include all Task states, all four response variants,
  standalone Message without fabricated taskId, safe initial rejection, retained
  Unknown for uncertain statuses, duplicate cancel and completion/cancel races.
- Native Engine + controlled ModelProvider tests exposed two omissions: default
  MVC String conversion could replace Chinese with question marks, and emitter
  fail(A2AError) publishes a stream error rather than the intended Task FAILED.
  SDK JSON SSE is now explicitly UTF-8 bytes. Confirmed-cleanup execution failures
  publish fail(Message), while unconfirmed cleanup still reports a protocol error
  and does not claim a stopped Task. Structured DataBlock output is explicitly
  unsupported by the text profile instead of becoming empty successful output.
- Native provider retry (two actual subscriptions, first failing after a prefix)
  now proves repeated prefixes are retained, streamed Chinese is intact and the
  authoritative persisted Artifact equals the emitted text. Structured-reference
  output proves both SSE and subsequent GetTask expose FAILED. The focused Java
  native text/interaction/executor/heartbeat gate PASSED; full reactor is running.
  Separate encoder tests preserve authoritative replacement on revision rather
  than rewriting SDK output to fit BCS's append-only limitation.

Remaining at this checkpoint: latest Java package, deploy/retest the final pair,
collect broad regression and architecture command completion, and finish the
requirement-by-requirement evidence audit. No goal completion is claimed.


### Final native build and live acceptance checkpoint

- Full offline Maven reactor package PASSED 71 tests, zero failures/errors/skips.
  This includes the native retry/structured-output tests and exact UTF-8 wire
  assertions using default MVC converters, plus existing webhook regressions.
- Deployed only the owned instances. Java Jar SHA256:
  6cbf2870440793ffe5e3f470f680d8049713c548949a77db92987e32f85a2440.
  BCS SHA256 at this acceptance checkpoint:
  4205e67c092d9aa9606c5d8b6c2d6ccab96a2cab00c59c0c8db05de0e774fa6f.
  On this exact pair all eight sequential steps PASSED: real Ling text/multiturn,
  real tools/history, cancellation, full Card/refresh, atomic Card registration,
  Workbench approve/deny, cancellation during actual Card fetch failure, and
  controlled initial HTTP rejection. Evidence files identify the tested build.
- WebSocket adapter regression PASSED 114 library tests on the current source.
  Source-size audit checked Avernet 188 and Harness 68 source files; all are
  below 1,000 lines. Whitespace check passed; no global formatter was run.
- The final state audit found unspecified Task status should query before
  cancellation. It now performs exact GetTask, commits already-ingested text
  before snapshot calibration, and retains a per-Run cap of three state queries.
  Repeated unspecified frames cannot cause unbounded queries or credential reads.
  Normal tokens add no queries/writes. Latest transport/interaction gate PASSED
  20 tests (13 listening transport + 7 structured delivery), including successful
  state recovery without duplicate prefix and query-cap exhaustion with exact
  cancellation. Final server rebuild is running for this narrow change.
- Restart preparation has completed: one completed BCS conversation and one
  completed asynchronous deduplicated native Task. A fixture quota was reached;
  only the owned, fully terminal controlled-rejection group was marked Completed
  through the normal authorized API. Its history was retained; no quota/config
  was weakened and no DB state was edited. The latest dual-process restart
  verification remains to be run after deploying the final rebuild.

The broad affected-module run has exceeded 2,200 passing tests with no failures
observed, but is still live. Architecture commands are also still live; their
static findings remain visible and are not an all-green result. Overall goal
remains active pending the final evidence audit and outstanding command results.


### Latest rebuild/restart result and remaining architecture correction

- The final unspecified-state recovery build deployed successfully. Current BCS
  SHA256: 2cdaab2601a1be471ce51aa23a29c779dfccea855da682eccdb659672f929f43.
  Java remains 6cbf2870440793ffe5e3f470f680d8049713c548949a77db92987e32f85a2440.
- Both actual owned process identities changed. Restart verification PASSED:
  completed Task retrieval, original messageId dedup, no duplicate Task, same
  context/new turn and real Ling memory of the preceding turn. Updated
  a2a-bcs-restart.json records this exact pair. No in-flight cursor guarantee is
  inferred from this completed-context test.
- Latest import findings were rechecked: all 69 reported lines exist verbatim in
  HEAD, with zero new direct-import findings. This is only static gate evidence.
  Semantic review found a remaining correction: InputRuntime still holds and
  calls RunBudgetCoreService through a root re-export. Changing the import path
  does not establish the intended application boundary. This must be corrected
  before claiming the new implementation meets architecture requirements.
- Next architecture change: introduce an actual application budget use case in
  message-flow, consuming the pure budget Core, managed-delivery service and
  BotRunContextPort. It should own pause/resume/initial-cap deadline orchestration,
  and return a budget change to the outbound adapter. The adapter may persist its
  A2A correlation snapshot, but must not call the Core policy or orchestrate
  managed/cache domain deadlines itself. Keep the existing order (both deadlines
  and correlation persisted before cards/continuation), canonical scope fencing,
  fail-closed cross-store failures and bounded lookup cost. Avoid a thin alias
  or another re-export workaround; add conformance for the application boundary.
  Update bootstrap and structured-delivery fixtures, then rerun affected budget/
  interaction contracts and real approval acceptance. No migration change needed.

Outstanding: this semantic budget-layer correction, broad regression/architecture
command completion and the final full requirement audit. The goal remains active.


### Budget application boundary corrected and verified

- Added real application RunBudgetService / RunBudgetManagement. Pure policy
  remains in Core; the application orchestrates managed deadline and guarded Run
  context writes. A2A transport persists only its correlation after the returned
  change, then publishes cards or sends continuation. Removed the outbound
  Core-policy dependency and its facade re-export. Bootstrap injects the same
  tracked Run context and managed service used by event ingestion/queue runtime.
- New application contract covers Memory and SQLite pause/repeat/resume, canonical
  scope, unchanged attempt/request/status/slot and initial total-cap shortening.
  SQL trigger failure proves no cache write follows a failed managed write;
  cache false/error proves partial writes propagate without fabricating terminal
  or rollback. Focused budget/application/interaction gate PASSED 57 tests,
  including 42 existing migration tests. No production SQL/migration changed.
- Added Redis and Tracked context drivers to the existing shared port conformance
  suite, including the new A2A deadline contract. The generic alias fixture now
  uses a finite valid future deadline rather than an unrepresentable cache TTL
  sentinel. Cache race/cost and context conformance PASSED 13 tests. The actual
  R25 entry matcher confirms the new application and both context drivers have
  conformance entries; no waiver or baseline exemption was added.
- Fixed text-only preflight disposition: unsupported attachments/non-text/missing
  text now return a definite rejection before submission instead of Err leading
  the managed runtime to Unknown. Latest listening transport gate PASSED 14
  tests, including zero RPC/correlation creation for these rejected inputs.
- Current deployed BCS SHA256:
  8c1efdb4f6fd3602c32e25565a1d9396b3a87fee2e3cb78705060eec2731bc58.
  Java remains 6cbf2870440793ffe5e3f470f680d8049713c548949a77db92987e32f85a2440.
  Real Workbench -> Harness -> Ling approve/deny PASSED on this pair; current
  a2a-bcs-exec.json records it. Previous completed exec fixture groups alone were
  marked Completed through the authorized API to retain history/free their test
  quota. No quota/config limit or DB row was bypassed.
- The 15 affected-package full lib/tests command COMPLETED successfully: 293
  suites, 3,573 passed, zero failures, 32 ignored. This run began before the final
  application-layer refactor; the focused gates above verify that later change.
  Counts overlap and must not be added as independent coverage.
- Ignored baseline cases: one inventory/llvm-cov limitation; four timeout-scanner
  fixtures requiring a GroupCore-backed fixture; three manual load/profiling
  tests; fifteen external Moltis E2Es; CLI health; no-mock-auth scenario; external
  history snapshot scenario; four external-DB integration tests; two opt-in
  MySQL suites. The A2A full-chain MySQL suite was separately run successfully on
  real MySQL 8.4.13 earlier, not inferred from this ignored default invocation.
- Current independent architecture components: 7 PASS (port purity, forbidden
  symbols, configuration validation, store boundaries, interceptor chain, protocol
  compatibility, waivers), 3 FAIL (existing dependencies/imports/trait naming),
  3 SKIP (no PR body, missing cargo-public-api, unresolved configured base ref).
  Dependency analysis also reports missing optional cargo-machete. All 69 import
  findings and all seven naming declarations were verified present in HEAD;
  the three dependency findings are in unchanged manifests. New budgeting Core
  calls are now actually behind the application boundary, not just renamed.

Remaining checks: full-workspace compile/discovery commands are still live;
finish their outcome audit and update final delivery evidence. Functional budget
implementation and real E2E are no longer pending. Overall goal remains active.


## 2026-10-29 final artifact and scope update

- User explicitly deferred both ask_user capability verification and implementation.
  Phase 1 delivers Structured Input exec only. The plan and acceptance checklist
  reflect that decision; it is not a claim that Harness lacks all native wait/resume
  primitives. The temporary capability test was removed and did not change the
  production Jar.
- Fixed first Task-identity persistence failure cleanup: an observed remote
  task/context remains available solely for exact cleanup when the previous
  durable record is still Submitting. The fallback requires matching immutable
  Run/scope/group/interface/message/digest/credential binding; it never claims
  the identity or terminal was persisted after a failed write. A SQLite trigger
  fixture verifies one exact remote cancellation, no local terminal/card, and
  retention of the managed execution slot while persistence remains failed.
- Initial streaming sends and asynchronous approval continuations now explicitly
  request acceptedOutputModes=[text/plain]. The preference does not forbid
  negotiated Structured Input status DataParts; the real exec roundtrip verifies
  this distinction. Latest listening transport and structured-delivery suites
  PASSED 22 tests (14 + 8).
- Whole-workspace test-target compilation COMPLETED successfully with
  `cargo test --locked --offline --manifest-path apps/bcs/Cargo.toml --workspace
  --no-run --message-format=json`: build-finished.success=true, 605 test
  executables. This is compilation evidence, not execution of every workspace test.
- Latest BCS binary build COMPLETED successfully and the owned 21082 runtime
  was replaced, retaining its SQLite state. BCS SHA256:
  fb7c9b091581e28a281a60ada68a788273582b343d997aa3de6b2a9c4f2571f8.
  Java remains 6cbf2870440793ffe5e3f470f680d8049713c548949a77db92987e32f85a2440.
  The real Workbench -> A2A -> native Harness -> Ling-3.1-flash approve/deny
  acceptance PASSED on this exact pair; evidence/a2a-bcs-exec.json records both
  completed tasks and the respective one/zero tool executions. Runtime/evidence
  hashes were checked for equality.
- Source-size audit: 193 changed Avernet source files and 68 changed Harness
  source files; none exceeds 1,000 lines. Both repository diff whitespace checks
  pass. No commit or push was performed.
- Machine-readable evidence index: validation-results.json. Counts from broad
  and focused test runs overlap; they must not be summed. Full architecture
  wrappers remain separately tracked and must not be reported as passed while
  their workspace --list subprocesses are still running. The already completed
  independent architecture checks retain the previously documented baseline
  failures and tool/configuration skips.


## Final acceptance disposition

- The latest agreed phase-1 functional scope is implemented and verified.
  Ask_user verification/implementation is excluded by the user's explicit latest
  instruction. All other deferred items remain as listed in the plan.
- Current R25 static sections were executed to completion (exit 1). Their 161
  findings are existing: 77 missing trait harnesses, five non-centralized test
  entries, and 79 missing impl entries. Every corresponding trait/impl declaration
  and missing harness was checked against HEAD, including two impls moved from
  provider.rs to provider_db.rs by source splitting. No new missing entry remains
  in these results. The original entry matcher separately PASSED for the current
  RunBudgetManagement, RedisBotRunContextStore and TrackedBotRunContext drivers.
- The two early duplicate full architecture wrappers were interrupted with exit
  143, stopping only their owned wrappers/children. They were running redundant
  whole-workspace `--list` discovery; they are NOT counted as completed or passed.
  Current whole-workspace test-target compilation (605 executables) and actual
  affected-package execution (including 17 named conformance entries) are recorded
  separately. No CI script was weakened or made to return a synthetic pass.
- Architecture outcome: seven independent components pass, three fail on existing
  dependencies/imports/naming, and three skip for recorded environment conditions.
  R25 / its TEST-1 alias additionally fail on the existing static findings above.
  Full workspace test execution, missing public-API tooling, PR-body checks and
  unresolved-base comparisons are not claimed as verified.
- All 37 current MySQL migration hashes still match the actual MySQL acceptance
  evidence. No later production migration was edited after that run.
- Source, contracts, plan, startup/registration/acceptance scripts and sanitized
  evidence are available in the two worktrees. Changes remain uncommitted; no push
  or publication was performed. The owned BCS/Harness runtimes remain available.


## 2026-10-30 — gRPC/MOSN phase started (not complete)

- User selected standard A2A gRPC through MOSN, with Harness SOFARPC Triple;
  streaming and asynchronous Task operations remain required. Direct gRPC is
  only an interoperability check, not the final deployment acceptance.
- Added pinned `a2a-pb 0.3.2` / tonic gRPC client binding with five operations,
  tenant override, metadata/version propagation, bounded messages and sanitized
  typed ErrorInfo. `cargo check -p bcs-a2a-http --offline` passed; four focused
  gRPC error/metadata tests passed. Production Card selection/channel/delivery
  wiring is pending.
- Java official gRPC Handler now shares the REST runtime's RequestHandler,
  durable Task store, event processing, deduplication and engine. Added Bot
  tenant authentication/router and SOFARPC Triple export using 5.13.0.
- A listening native gRPC → SOFARPC Triple test passed: streaming Artifact and
  completion, asynchronous Task receipt, Task query, unknown Bot and missing
  Bearer rejection. This uses an in-process test executor, not a real model.
  Adapter-core 16 + A2A 29 tests passed in the focused module regression.
- Installed yuque CLI 1.0.24 using `acli install yuque --yes`, read the user-linked
  install guide through the authenticated CLI, and installed Native Mesh 2.35.0.
  Initial MOSN startup was observed, but later process termination and AntVIP
  discovery failure mean it is not yet a stable/verified mesh environment.
  A detached startup with inherited HTTP proxies removed is under diagnosis.
- Real MOSN service publication/subscription and BCS → Harness end-to-end,
  tools, exec approvals, async resubscription and cancellation are NOT passed.
  No gRPC completion is claimed from the historical HTTP acceptance evidence.


### Subsequent mesh diagnosis on 2026-10-30

Native MOSN now runs detached with the developer application identity set during
bootstrap and internal proxy variables removed. The owned PID and private config
are in the ignored Harness runtime directory. The private health-check config
points at the real Triple application port; it does not substitute an unrelated
Bolt process or disable health checking. Triple publication requires non-empty
provider properties, including application and deployment zone.

MOSN acknowledged publish/subscribe, and registry logs prove app metadata and
application endpoint registration plus a discovered service host. First service
mapping propagation took about 78 seconds. However, the explicit mesh integration
check still failed with no usable route: the router selected AntVIP fallback and
reported RESOLVE_ANTVIP_URL_FAILED. This is an unresolved deployment/routing issue,
not a successful end-to-end check. Native gRPC/Triple direct checks and focused
Java regression remain independently passing. Production BCS binding selection,
Card declaration and Harness demo bootstrap are still pending.

The internal MOSN source was inspected read-only at a pinned local checkout to
understand health-gated publication and service metadata. No internal source,
private endpoint default or runtime config was copied into public production code.


Further diagnosis identified a concrete Native Mesh package incompatibility:
its generated config uses `sofa_egress_triple_router` and
`sofa_ingress_triple_router`, while the installed MOSN binary and inspected
implementation use the corresponding `*_new_triple_router` names. Therefore,
service subscription populated a different route table than the listeners used.
Only the private config copy was corrected, and MOSN was restarted with owned
PID checks. A fresh real-mesh interoperability check is running; its outcome
must be inspected before marking any mesh operation passed.


The subsequent native stream-profile check still failed, with a different and
narrower symptom: outbound route selection now succeeds using the discovered
endpoint, but the connection to the peer MOSN ingress resets during HTTP/2 server
preface (`UNAVAILABLE`). The installed template mixed the old HTTP/2 proxy with
the newer native gRPC proxy. The private copy now follows the inspected
`configs/listeners/streamtriple` profile (native gRPC proxy on both sides), uses
matching new route names, real Triple health checks and an application identity
present before registry bootstrap. Ingress protocol/TLS/listener compatibility
remains unresolved. Do not replace this check with a static backend route or a
successful direct test. The public test takes an explicit MOSN opt-in and target
app property, uses bounded readiness queries (120 seconds after observing the
78-second initial mapping), and reports only status codes for probe failures.

Read-only internal MOSN reference: `27149d0274b7cb40c57199742721c9ab83a6494c`;
public SOFARPC reference v5.13.0: `2ccbe7c39a0bb90a16394d40f355512ff36aeb2e`.
No full wire-header tracing should be enabled in real-model acceptance: native
MOSN may append identity credentials. Capture only sanitized counters/outcomes.


## 2026-10-30 — real-model SDK/MOSN streaming and async passed

The original ingress port 50051 was observed to close non-loopback connections,
while the same process's 50002/11004 ports worked. A dedicated ingress on 51051
completed both loopback and LAN-address HTTP/2 handshakes. No OS firewall or
proxy was disabled. Native publisher metadata uses that port; outbound calls
continue through local MOSN 50002 and real registry discovery. The startup
profile also points health checks to the real Harness HTTP/Triple listeners,
and reports readiness only after verifying the Card and native gRPC service.

The Rust SDK smoke `examples/harness_grpc.rs` passed streaming and asynchronous
SendMessage + SubscribeToTask against the actual model, 7 events each, with
completed text. The exact sanitized outcomes and Java/config hashes are in
Harness `evidence/a2a-grpc-mosn-sdk.json`. This proves the SDK/mesh/Triple/engine
chain, not the full BCS runtime, tools, cancellation or exec approval acceptance.

Production source now adds the independent `grpc` binding and explicit optional
`a2a_grpc` mesh routes, shared driver dispatch, Card selection and SOFABoot
Triple composition. Only literal loopback MOSN endpoints can be dialed; selected
Card interfaces must match trusted deployment routes. Requests retain version,
Bearer, tenant, extensions, method and standard gRPC deadline. Frozen route
fingerprints partition contexts and reject retargeting old Tasks; absent HTTP
fingerprints preserve previous scope serialization, without migration rewrites.
The pinned SDK's removal of http:// from gRPC Card URLs is corrected at this
boundary using the authoritative raw Card string, with regression coverage.

Adapter/domain regression before the final Card/scope changes: 171 passed across
20 suites; current focused rerun is passing so far. Java full module tests:
16 adapter-core + 29 A2A + 11 starter + 16 demo = 72 passed. BCS full runtime
build/acceptance and current scope-store compatibility checks remain pending.


## 2026-10-30 — current artifacts and full BCS acceptance passed

The current Harness package includes the shared official gRPC handler, native
SOFARPC Triple bootstrap, ListTasks delegation and a deterministic asynchronous
receipt-before-completion test. The deployed hashes are recorded in
`grpc-validation-results.json`; all gRPC evidence was rerun against those builds.
The Rust SDK streaming and asynchronous paths passed the real mesh/model chain.
Full BCS text, multi-turn, real tools/history, exact Task cancellation and
Workbench exec allow_once/deny passed. An invalid Bearer traversed real MOSN and
was rejected with gRPC UNAUTHENTICATED. Current BCS also passed legacy HTTP text,
tools, cancellation and exec regression against the preserved HTTP Harness.

Bootstrap config validation passed 147 tests; scope-store conformance passed 6,
including a literal pre-gRPC scope/hash fixture. Current Card/gRPC focused suites
passed 20 tests; Java full reactor passed 72. Dependency checking still reports
existing contract dependency constraints; forbidden-symbol checking passed.
Neither full workspace nor Singlebox CI was rerun for this gRPC phase. All
changed source files checked remain below 1000 lines, and both diffs are clean.

The original user trial at 21082 now runs the verified BCS build with its original
database and both Harness Cards configured for gRPC. Its real-model request and
the open-claw 8000 proxied WebSocket connect passed. Existing frontend/SSH
forwarding ports are unchanged; no new browser-facing gRPC port is needed.

Restart caveat: after the final Harness upgrade the dedicated MOSN ingress had
stopped listening despite health UP. An initial submission became uncertain
before receiving a Task and retained a queue slot; exact cancellation could not
confirm a remote outcome. Its isolated database was preserved, then the fixture
was reset without changing user data. Restarting the owned sidecar and explicitly
reinitializing/publishing/subscribing restored actual ingress and registry state.
Current acceptance passed afterward. This version does not claim automatic
lossless restart recovery; deployments must verify real listeners and publication
before admitting calls. Model proxy access stayed enabled throughout.
