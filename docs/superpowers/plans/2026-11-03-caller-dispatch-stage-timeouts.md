# Caller 派发分段超时 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single 30s outer window on the protected caller dispatch with per-stage liveness bounds and stage-accurate evidence convergence, matching the ordinary delivery path.

**Architecture:** The runtime-level `tokio::time::timeout(io_timeout, attempt_caller_protected_dispatch)` wrapper is deleted; instead every pipeline stage (handoff claim, container ensure, establish/issue, digest commit, protected send) gets its own injected stage budget inside `CallerDispatchPipeline`, and the runtime-side in-process lookups (`resolve_caller_dispatch`, `bounded_contexts`) get their own `io_timeout` wraps with the existing failure branches. Stage timeouts converge by stage evidence: pre-send stages → conservative `Failed` (terminal, lane released) via a new `StageTimeout { stage }` error variant; the send stage → `OutcomeUnknown` → `Unknown` (lane paused, late-callback/cancel arbitration), identical to the ordinary path's `deliver` timeout semantics.

**Tech Stack:** Rust (BCS workspace, `apps/bcs`), tokio (`tokio::time::timeout`), `cargo nextest`.

**Spec:** `docs/superpowers/specs/2026-11-03-caller-dispatch-stage-timeouts-design.md` (committed as `d9f64aa74`).

**Plan-time amendment to the spec (derived from the watchdog contract, applies to spec §3.3):** the send stage DOES get a pipeline-side stage bound (timeout → `OutcomeUnknown`), and the ensure stage gets one (timeout → `StageTimeout`). Reason: the ordinary path itself wraps its network send (`delivery_runtime.rs:660` wraps `io.deliver` in `io_timeout`; timeout → `TransportUnknown`), and the existing watchdog contract test `protected_send_timeout_converges_unknown_without_retry` (`tests/caller_protected_dispatch_runtime_contract/watchdog.rs:44`) pins exactly this convergence for a hanging send port. "和 non-caller 一致" therefore means a bounded send stage, not an unbounded one. Everything else in the spec is unchanged — no outer window, no retry, one-shot identity preserved.

**Critical context for every task:**

- **No global `cargo fmt`.** Format only the lines you change.
- **1,000-line CI limit.** `caller_dispatch_pipeline.rs` is at 974 lines — Task 2 extracts the `CallerDispatchError` enum into a new `errors.rs` submodule so the added wraps keep the file under 1,000. Check `wc -l` before committing.
- Read `apps/bcs/AGENTS.md` (root CLAUDE.md instruction) before the first edit.
- Tests: `cargo nextest run -j 2` inside `apps/bcs` (workspace lock contention at high parallelism).
- Branch discipline: all work on a fresh worktree branched from `origin/dev`, isolated from the user's dirty `feat/caller-sse-alignment`.

**Timeout/evidence decision table (the contract this plan implements):**

| Stage (wrap site) | File:line (pre-change) | Timeout converges as |
|---|---|---|
| `resolve_caller_dispatch` | `caller_dispatch.rs:21` | existing resolve-failure branch → `PreparationFailed` → row `Failed`, attempt 0 |
| `bounded_contexts` (history header) | `caller_dispatch.rs:194` | existing `Err` branch → `PreparationFailed` |
| handoff claim | `caller_dispatch_pipeline.rs:552` | `Err(StageTimeout{stage:"handoff_claim"})` → runtime `other` arm → `Event::Failed` |
| container ensure | `caller_dispatch_pipeline.rs:597` | `Err(StageTimeout{stage:"container_ensure"})` → `Event::Failed` |
| establish subject | `caller_dispatch_pipeline.rs:647` | `Err(StageTimeout{stage:"establish_subject"})` → `Event::Failed` |
| issue execution (+ agent lookup) | `caller_dispatch_pipeline.rs:701` | `Err(StageTimeout{stage:"issue_execution"})` → `Event::Failed` |
| digest commit | `caller_dispatch_pipeline.rs:763` | `Err(StageTimeout{stage:"digest_commit"})` → `Event::Failed` |
| protected send | `caller_dispatch_pipeline.rs:840` | `Ok(OutcomeUnknown)` → `Event::TransportUnknown` → `Unknown`, lane paused |
| barrier park (HISTORY_NOT_READY arm) | `caller_dispatch_pipeline.rs:849` | best-effort only; still returns `Rejected{HISTORY_NOT_READY,retryable}` (runtime sets the durable marker itself) |
| delegate ensure / issue / commit / history-read / send | `caller_dispatch_pipeline/delegate.rs:208 / 282+315 / 369 / 390 / 485` | same as root equivalents (`DelegateDispatchError::StageTimeout` / `OutcomeUnknown`) |

Claim timeout must NOT reuse `IdentityUnavailable` (that variant asserts a provable claim refusal → `DefinitelyNotSent`); a hang cannot prove refusal, so it must take the conservative `Failed` path.

---

### Task 1: Worktree, spec, baseline

**Files:** none modified (setup + one spec amendment in the worktree).

- [ ] **Step 1: Create the isolated worktree**

Use the EnterWorktree tool with name `caller-stage-timeouts` (branches from `origin/dev` per `worktree.baseRef=fresh`). Do NOT work in the main checkout — it carries the user's uncommitted `feat/caller-sse-alignment` changes.

- [ ] **Step 2: Read the binding contracts**

Read: `apps/bcs/AGENTS.md`, `docs/superpowers/specs/2026-11-03-caller-dispatch-stage-timeouts-design.md` (cherry-pick `d9f64aa74` into the worktree branch first: `git cherry-pick d9f64aa74` — doc-only commit, no conflicts).

- [ ] **Step 3: Amend spec §3.3 with the send/ensure wrap amendment**

In the spec file, replace the table row block under §3.3 starting `**不另加包的 stage**：ensure 容器（自限 10s）、send（自限 30s）...` with:

```markdown
**同样加包的 stage（计划期修订 2026-11-03）**：ensure 容器与 send——普通路径自身就在
`delivery_runtime.rs:660` 给 `io.deliver` 包 `io_timeout`（超时 → TransportUnknown），watchdog
契约测试 `protected_send_timeout_converges_unknown_without_retry` 也正是以"挂起的 send 端口
在有界窗口内收敛 Unknown"为契约的。故二者在 pipeline 内同样适用 stage_timeout：
ensure 超时 → `StageTimeout{stage:"container_ensure"}`（保守 Failed）；send 超时 →
`OutcomeUnknown`（走 send adapter 自超时完全相同的证据路径）。adapter 自限保留不变。
```

Commit:

```bash
git add docs/superpowers/specs/2026-11-03-caller-dispatch-stage-timeouts-design.md
git commit -m "docs(bcs): amend the caller stage-timeout spec for the ensure/send wraps

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 4: Baseline test run** (in `apps/bcs` of the worktree)

```bash
cargo nextest run -p bcs-message-flow -j 2
```

Expected: all green. If red before any change, STOP and report — the baseline must be clean.

---

### Task 2: stage module, error enums, pipeline budget field

**Files:**
- Create: `apps/bcs/crates/services/bcs-message-flow/src/caller_dispatch_pipeline/stage.rs`
- Create: `apps/bcs/crates/services/bcs-message-flow/src/caller_dispatch_pipeline/errors.rs`
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/caller_dispatch_pipeline.rs` (extract enum at :177-196, add `mod`/`use`, add field+builder)

- [ ] **Step 1: Create `caller_dispatch_pipeline/stage.rs`**

```rust
//! Per-stage liveness bounds for the protected dispatch pipeline
//! (spec 2026-11-03-caller-dispatch-stage-timeouts): there is no outer
//! window — every stage is individually bounded and its timeout converges
//! with stage-accurate evidence.
use std::time::Duration;

/// Composition default, mirroring `DeliveryRuntimeConfig.io_timeout`
/// (30s in production wiring). Only tests shrink it.
pub(super) const DEFAULT_STAGE_TIMEOUT: Duration = Duration::from_secs(30);

/// Await `future` under the stage's liveness `budget`. `None` means the
/// stage exceeded its bound: nothing about success or failure is known,
/// the caller OWS the conservative convergence decision, and the WARN
/// names the stage for the on-call.
pub(super) async fn stage_bound<T>(
    budget: Duration,
    stage: &'static str,
    future: impl std::future::Future<Output = T>,
) -> Option<T> {
    match tokio::time::timeout(budget, future).await {
        Ok(value) => Some(value),
        Err(_) => {
            tracing::warn!(stage, "caller protected dispatch stage timed out");
            None
        }
    }
}
```

- [ ] **Step 2: create `caller_dispatch_pipeline/errors.rs`** — move the whole `CallerDispatchError` enum (currently `caller_dispatch_pipeline.rs:176-196`) verbatim, adding the new variant:

```rust
//! Error surface of the protected dispatch pipeline. Extracted so the
//! pipeline file stays under the 1,000-line CI limit while the stage
//! liveness wraps land (spec 2026-11-03).

/// The runtime-facing failure vocabulary of one protected dispatch.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum CallerDispatchError {
    /// No identity parked for this scope (admission never published, or
    /// already claimed/expired). Fail closed; never owner-dispatch.
    #[error("caller identity unavailable")]
    IdentityUnavailable,
    /// Container preparation refused (not ready, linkage corrupt, isolation
    /// below requirement). Identity already consumed by the claim.
    #[error("caller container preparation failed")]
    PreparationFailed,
    /// The identity plugin refused to issue (gate: bad input, expired
    /// subject, misconfigured keys).
    #[error("caller identity plugin refused to issue")]
    IssueFailed,
    /// Structural input invalid (attempt 0, empty facts, inverted clocks).
    #[error("invalid caller dispatch input")]
    InvalidInput,
    /// The identity plugin refused the subject establishment (the
    /// ingress→trusted-subject exchange failed — trusted ingress refused,
    /// a misconfigured profile, or a trust window that already ended).
    /// Identity already consumed by the claim — the human re-submits.
    #[error("caller subject establishment failed")]
    EstablishFailed,
    /// The typed `commit_caller_dispatch` CAS refused the canonical digest
    /// (association drift, stale version, status divergence). Network has
    /// already been touched at preparation, so callers must converge this
    /// conservatively; the one-shot identity is consumed.
    #[error("caller dispatch commit refused")]
    CommitFailed,
    /// A stage exceeded its per-stage liveness budget (spec
    /// 2026-11-03). The collaborator may hold partial state — the claim
    /// may be consumed, the ensure may be mid-flight — so callers must
    /// converge conservatively (Failed), never as a provable not-sent.
    /// Distinct from `IdentityUnavailable`, which asserts a provable
    /// claim refusal.
    #[error("caller dispatch stage timed out at {stage}")]
    StageTimeout { stage: &'static str },
}
```

NOTE for the implementer: the derive line in the original file may differ slightly (check line 176 of `caller_dispatch_pipeline.rs` for the exact existing derives and keep them); move the enum VERBATIM plus the new `StageTimeout` variant. Then in `caller_dispatch_pipeline.rs` delete the enum body and add near the other module declarations (the `pub use delegate::{DelegateDispatchError, DelegateDispatchFacts};` line, ~:953):

```rust
mod errors;
mod stage;
pub use errors::CallerDispatchError;
use stage::{stage_bound, DEFAULT_STAGE_TIMEOUT};
```

(`mod errors;` visibility adjusts if `CallerDispatchError` is re-exported elsewhere — the crate-level `lib.rs` may also re-export it; run `cargo check -p bcs-message-flow` and follow the compiler.)

- [ ] **Step 3: Add the budget field + builder to `CallerDispatchPipeline`**

In the struct (after `container_ttl_ms` field at ~:237): add

```rust
    /// Per-stage liveness budget shared by every bounded stage (spec
    /// 2026-11-03). The composition root passes the same 30s the ordinary
    /// runtime uses as `io_timeout`; tests shrink it to assert the
    /// stage-timeout convergence.
    stage_timeout: std::time::Duration,
```

In `fn new(...)` initializer list add `stage_timeout: DEFAULT_STAGE_TIMEOUT,`; next to the other `with_*` builders add:

```rust
    /// Sets the per-stage liveness bound shared by every pipeline stage
    /// (spec 2026-11-03); production wiring mirrors the ordinary path's
    /// `io_timeout` (30s).
    pub fn with_stage_timeout(mut self, budget: std::time::Duration) -> Self {
        self.stage_timeout = budget;
        self
    }
```

- [ ] **Step 4: Compile check + line-count check + commit**

```bash
cargo check -p bcs-message-flow
wc -l crates/services/bcs-message-flow/src/caller_dispatch_pipeline.rs
```

Expected: compiles; file `< 800` lines after the extraction. Then:

```bash
git add crates/services/bcs-message-flow/src/caller_dispatch_pipeline.rs crates/services/bcs-message-flow/src/caller_dispatch_pipeline/
git commit -m "refactor(bcs): extract caller dispatch errors and add the stage liveness budget

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Root pipeline stage-timeout tests (RED)

**Files:**
- Create: `apps/bcs/crates/services/bcs-message-flow/tests/caller_dispatch_pipeline_stage_timeout.rs`

The fixtures mirror `tests/caller_dispatch_pipeline_contract.rs` (same imports; `facts()`, `seeded_handoff`, `delivery_target`, `now_clock`, `DIGEST` are copied from that file — replicate them here; `#[path]` cannot share them without a support module, and the contract file is already 741 lines).

- [ ] **Step 1: Write the test file**

```rust
//! Per-stage liveness bounds of the protected dispatch pipeline (spec
//! 2026-11-03): no outer window exists, so each stage's exhausted budget
//! converges with stage-accurate evidence — pre-send stages time out into
//! the conservative `StageTimeout` error, the protected send into the
//! same OutcomeUnknown the adapter's own timeout produces.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_dispatch_pipeline::{
    CallerDispatchOutcome, CallerDispatchPipeline, CommittedDispatchFacts,
    DeliveryCommitOrigin,
};
use bcs_service_api::application::caller_container::CallerContainerPreparation;
use bcs_service_api::application::caller_handoff::{HumanCallerDeliveryHandoffService, HandoffScope};
use bcs_service_api::application::message_delivery::{CommitCallerDispatchCommand, ManagedMessageDeliveryService, ManagedDeliveryError};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::{
    CallerContainerError, CallerContainerGatewayPort, CallerDispatchAck,
    CallerExecutionSendPort, EnsureCallerContainer, SendCallerExecutionCommand,
};
use bcs_caller_identity_api::{CallerIdentityError, CallerIdentityPlugin, EstablishCallerSubject, IssueCallerExecution, VerifyCallerExecution};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_domain::message_delivery::PersistedMessageDelivery;
use bcs_service_api::port::repo::message_delivery::DeliveryLookup;

const NOW_TOLERANCE: Duration = Duration::from_millis(400);
const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn now_clock() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as i64
}

// —— fixtures copied from caller_dispatch_pipeline_contract.rs (read that
// file and copy `ingress`, `facts`, `delivery_target`, `seeded_handoff`
// verbatim; only the pipeline builder gains `.with_stage_timeout`). ——

// Hanging collaborators: each parks the exact one stage it is named for;
// the dispatch must fire that stage's budget, not wander.
struct HangingGateway;
#[async_trait]
impl CallerContainerGatewayPort for HangingGateway {
    async fn ensure_container(
        &self, _target: &BotDeliveryTarget, _cred: &RedactedToken,
        _request: EnsureCallerContainer,
    ) -> Result<bcs_service_api::port::CallerContainerDescriptor, CallerContainerError> {
        std::future::pending().await
    }
}

struct HangingIdentity;
#[async_trait]
impl CallerIdentityPlugin for HangingIdentity {
    async fn establish_subject(&self, _input: EstablishCallerSubject)
        -> Result<bcs_caller_identity_api::EstablishedCallerSubject, CallerIdentityError> {
        std::future::pending().await
    }
    async fn issue_execution<'a>(&self, _input: IssueCallerExecution<'a>)
        -> Result<bcs_caller_identity_api::CallerExecutionToken, CallerIdentityError> {
        std::future::pending().await
    }
    async fn verify_execution(&self, _input: VerifyCallerExecution)
        -> Result<bcs_caller_identity_api::VerifiedCallerExecution, CallerIdentityError> {
        unreachable!("the dispatch path never verifies")
    }
    fn proof_type(&self) -> &'static str { "jwt" }
}

struct HangingSender;
#[async_trait]
impl CallerExecutionSendPort for HangingSender {
    async fn send_caller_execution(
        &self, _target: &BotDeliveryTarget, _cred: &RedactedToken,
        _command: SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        std::future::pending().await
    }
}

struct HangingHandoff;
#[async_trait]
impl HumanCallerDeliveryHandoffService for HangingHandoff {
    async fn claim(&self, _scope: &HandoffScope, _claim: bcs_service_api::application::caller_handoff::HandoffClaim)
        -> Result<AuthenticatedHumanCallerIngress, bcs_service_api::application::caller_handoff::HandoffRefusal> {
        let _: Result<std::sync::Arc<AuthenticatedHumanCallerIngress>, ()> = std::future::pending().await;
        unreachable!()
    }
}

/// Wraps the REAL managed service and hangs only the typed commit CAS; the
/// other required trait methods forward. `commit_caller_dispatch` has a
/// default refusal, so only the methods without defaults need forwarding —
/// read the trait at
/// crates/service-api/bcs-service-api/src/application/message_delivery.rs:411
/// and forward every method that has no default body, exactly one line each,
/// in the same style:
struct HangingCommit(Arc<ManagedMessageDelivery>);
#[async_trait]
impl ManagedMessageDeliveryService for HangingCommit {
    async fn commit_caller_dispatch(&self, _command: CommitCallerDispatchCommand)
        -> Result<bool, ManagedDeliveryError> {
        std::future::pending().await
    }
    async fn bounded_contexts(&self, carrier: &str, limit: usize)
        -> Result<bcs_service_api::port::repo::message_delivery::BoundDeliveryContexts, ManagedDeliveryError> {
        self.0.bounded_contexts(carrier, limit).await
    }
    // …… forward lookup / queued_bots / active_count / lane_blocked /
    // queued_heads / work_batch / queue_statistics / unfinished /
    // update_wait_reasons / accept_run / admit / snapshot / transition /
    // recover identically (`self.0.<name>(…).await`) ……
}

fn base_pipeline(
    gateway: Arc<dyn CallerContainerGatewayPort>,
    identity: Arc<dyn CallerIdentityPlugin>,
    sender: Arc<dyn CallerExecutionSendPort>,
    handoff: Arc<dyn HumanCallerDeliveryHandoffService>,
) -> CallerDispatchPipeline {
    CallerDispatchPipeline::new(
        handoff,
        Arc::new(CallerContainerPreparation::new(gateway)),
        identity,
        sender,
        3_600_000,
    )
}

#[tokio::test]
async fn claim_stage_timeout_converges_conservatively() {
    let pipeline = base_pipeline(
        Arc::new(GatewayStubFromContract),
        Arc::new(IdentityStubFromContract),
        Arc::new(SenderStubFromContract),
        Arc::new(HangingHandoff),
    )
    .with_stage_timeout(Duration::from_millis(40));
    let error = tokio::time::timeout(
        NOW_TOLERANCE,
        pipeline.dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock()),
    )
    .await
    .expect("bounded stage must return")
    .unwrap_err();
    assert_eq!(error, CallerDispatchError::StageTimeout { stage: "handoff_claim" });
}

#[tokio::test]
async fn ensure_stage_timeout_converges_conservatively() {
    // handoff must be the real seeded one so claim succeeds and the
    // dispatch reaches the container ensure with a HangingGateway.
    let (handoff, _) = seeded_handoff("turn-1").await;
    let pipeline = base_pipeline(
        Arc::new(HangingGateway),
        Arc::new(IdentityStubFromContract),
        Arc::new(SenderStubFromContract),
        handoff,
    )
    .with_stage_timeout(Duration::from_millis(40));
    let error = …dispatch_once…unwrap_err();
    assert_eq!(error, CallerDispatchError::StageTimeout { stage: "container_ensure" });
}

#[tokio::test]
async fn establish_stage_timeout_converges_conservatively() { …HangingIdentity, stage "establish_subject"… }

#[tokio::test]
async fn issue_stage_timeout_converges_conservatively() { …HangingIdentity hangs only issue_execution… }

#[tokio::test]
async fn commit_stage_timeout_converges_conservatively() {
    // facts() with delivery_commit: Some(DeliveryCommitOrigin { delivery_id: "delivery-1".into(), expected_state_version: 1 })
    // and pipeline .with_managed_commit(Arc::new(HangingCommit(managed)))
    // expect StageTimeout { stage: "digest_commit" }
}

#[tokio::test]
async fn send_stage_timeout_converges_outcome_unknown() {
    // seeded handoff + recording gateway/identity + HangingSender
    // expect Ok(CallerDispatchOutcome::OutcomeUnknown)
}
```

IMPORTANT for the implementer: the sketch above deliberately names placeholder stubs (`GatewayStubFromContract` etc.) — replace them with the REAL recording stubs (`StubGateway`, `StubIdentity`, `RecordingSender`) and the real `facts()`/`delivery_target()`/`seeded_handoff()` fixtures **copied verbatim from `tests/caller_dispatch_pipeline_contract.rs`** (its fixtures are free functions; copy them, do not invent shapes). For `issue_stage_timeout` make a `HangingIssueIdentity` that hangs only `issue_execution` and a `HangingEstablishIdentity` that hangs only `establish_subject`. Finalize the `use` list against the real trait signatures you read (some sketch imports like `DeliveryLookup` may prove unused); trust the compiler, not the sketch.

- [ ] **Step 2: Run the new tests, expect compile failure** (variants don't exist yet)

```bash
cargo nextest run -p bcs-message-flow -E 'test(caller_dispatch_pipeline_stage_timeout)' -j 2
```

Expected: FAIL (no `StageTimeout` variant / no stage behavior). If a test accidentally PASSES, the wrap is missing somewhere it was assumed — investigate before proceeding.

- [ ] **Step 3: Commit the red tests**

```bash
git add crates/services/bcs-message-flow/tests/caller_dispatch_pipeline_stage_timeout.rs
git commit -m "test(bcs): pin the per-stage timeout contracts of the protected dispatch

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Implement the root wraps (GREEN)

**Files:**
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/caller_dispatch_pipeline.rs` (dispatch_once :552/:597/:647/:701/:763/:840/:849)

- [ ] **Step 1: claim wrap** — replace `caller_dispatch_pipeline.rs:552-558`:

```rust
// Original:
let ingress = match self.handoff.claim(&scope, claim).await {
    Ok(ingress) => ingress,
    Err(_) => return Err(CallerDispatchError::IdentityUnavailable),
};
```

with:

```rust
let Some(claim_result) =
    stage_bound(self.stage_timeout, "handoff_claim", self.handoff.claim(&scope, claim)).await
else {
    // The claim's consumption state is unverifiable after a hang — never
    // IdentityUnavailable (that asserts a provable refusal).
    return Err(CallerDispatchError::StageTimeout { stage: "handoff_claim" });
};
let ingress = match claim_result {
    Ok(ingress) => ingress,
    Err(_) => return Err(CallerDispatchError::IdentityUnavailable),
};
```

- [ ] **Step 2: ensure wrap** — replace `:597-610` (`let prepared = match self.preparation.prepare(...)`) with:

```rust
let Some(prepare_result) = stage_bound(
    self.stage_timeout,
    "container_ensure",
    self.preparation.prepare(target, service_credential, ensure, now_ms),
).await
else {
    // Ambiguous exactly like `Gateway { unknown_outcome: true }`: the
    // container request may be mid-flight. Conservative convergence is
    // the runtime's Failed arm.
    return Err(CallerDispatchError::StageTimeout { stage: "container_ensure" });
};
let prepared = match prepare_result {
    Ok(PrepareOutcome::Prepared { descriptor, effective_deadline_ms: _ }) => {
        (descriptor, effective_not_after)
    }
    Err(CallerPreparationError::Gateway { unknown_outcome: true, .. })
    | Err(_) => return Err(CallerDispatchError::PreparationFailed),
};
```

- [ ] **Step 3: establish wrap** — replace `:647-662` (`self.identity.establish_subject(...).await.map_err(...)`) with:

```rust
let Some(established) = stage_bound(
    self.stage_timeout,
    "establish_subject",
    self.identity.establish_subject(bcs_caller_identity_api::EstablishCallerSubject {
        ingress: (*ingress).clone(),
        now_ms,
    }),
).await
else {
    return Err(CallerDispatchError::StageTimeout { stage: "establish_subject" });
};
let established = match established {
    Ok(value) => value,
    Err(error) => {
        tracing::warn!(
            request_id = %bcs_observability::CurrentRequestId,
            dispatch_id = %facts.dispatch_id,
            error = ?error,
            "caller identity plugin refused to establish the subject"
        );
        return Err(CallerDispatchError::EstablishFailed);
    }
};
```

- [ ] **Step 4: issue wrap (hoists the agent lookup into the same bound block)** — replace `:669-712` so the `agent_code` lookup and issuance share one bound; the `IssueCallerExecution { ... }` literal moves INSIDE the `async` block verbatim (copy the existing literal at :669-699, changing only the `agent_code:` field from the inline `.await` to the hoisted variable):

```rust
let Some(issue_result) = stage_bound(self.stage_timeout, "issue_execution", async {
    let agent_code = self.target_agent_code(&facts.target_bot_id).await;
    let issue_input = IssueCallerExecution {
        staff_no: opaque_from(ingress.actor()),
        agent_code,
        sandbox_container_id: descriptor.sandbox_container_id.clone(),
        // …… the remaining fields copied VERBATIM from the existing
        // literal at :669-699 ……
    };
    self.identity.issue_execution(issue_input).await
}).await
else {
    return Err(CallerDispatchError::StageTimeout { stage: "issue_execution" });
};
let proof: ProofToken = match issue_result {
    Ok(token) => token,
    Err(error) => {
        tracing::warn!(
            request_id = %bcs_observability::CurrentRequestId,
            dispatch_id = %facts.dispatch_id,
            error = ?error,
            "caller identity plugin refused to issue an execution proof"
        );
        return Err(CallerDispatchError::IssueFailed);
    }
};
```

(The block borrows `&facts`/`&descriptor`/`&ingress` directly; keep `caller_subject_ref`, `identity_subject`, `identity_subject_type`, `identity_deadline` extraction AFTER the block as in the original.)

- [ ] **Step 5: commit wrap** — replace `:763-766` (`managed.commit_caller_dispatch(commit_command).await.map_err(|_| CallerDispatchError::CommitFailed)?;`):

```rust
let Some(commit_result) = stage_bound(
    self.stage_timeout,
    "digest_commit",
    managed.commit_caller_dispatch(commit_command),
).await
else {
    return Err(CallerDispatchError::StageTimeout { stage: "digest_commit" });
};
commit_result.map_err(|_| CallerDispatchError::CommitFailed)?;
```

- [ ] **Step 6: send wrap** — replace `:840` (`let send_result = self.sender.send_caller_execution(...).await;`):

```rust
let send_result = match stage_bound(
    self.stage_timeout,
    "send",
    self.sender.send_caller_execution(target, service_credential, command.clone()),
).await {
    Some(result) => result,
    None => {
        // Egress uncertainty — the SAME evidence as the transport's own
        // 30s callback self-timeout: OutcomeUnknown converges the row to
        // Unknown with the lane paused (the ordinary path's `deliver`
        // timeout semantics, delivery_runtime.rs:660).
        return Ok(CallerDispatchOutcome::OutcomeUnknown);
    }
};
```

- [ ] **Step 7: park wrap (best-effort)** — replace `:849-852` in the `HISTORY_NOT_READY` arm:

```rust
if error_code == "HISTORY_NOT_READY" && retryable {
    // Best-effort registration: the runtime sets the durable
    // HistoryBarrier marker itself (delivery_runtime/caller_dispatch.rs);
    // a hang here costs only the redrive registration, never convergence.
    let _ = stage_bound(
        self.stage_timeout,
        "barrier_park",
        self.park_root_barrier(target, service_credential, &command, &facts, accepted, at_term),
    ).await;
}
Ok(CallerDispatchOutcome::Rejected { error_code, retryable })
```

- [ ] **Step 8: run the Task-3 tests + the pipeline contract suite (green)**

```bash
cargo nextest run -p bcs-message-flow -E 'test(caller_dispatch_pipeline)' -j 2
```

Expected: all pass, including the pre-existing contract tests (stage default 30s doesn't disturb them).

- [ ] **Step 9: Commit**

```bash
git add crates/services/bcs-message-flow/src/caller_dispatch_pipeline.rs
git commit -m "feat(bcs): bound every caller dispatch stage with its own liveness window

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Delegate path wraps + runtime delegate arm (RED → GREEN)

**Files:**
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/caller_dispatch_pipeline/delegate.rs` (:70-76 enum, :207-220 ensure, :280-326 issue, :369-372 commit, :390-392 history read, ~:485 send, park arm)
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/delivery_runtime/caller_delegate.rs:260` (add `E::StageTimeout { .. }` to the Failed arm)
- Modify: `apps/bcs/crates/services/bcs-message-flow/tests/support/caller_delegate_e2e.rs` (new test)

- [ ] **Step 1: Write the failing delegate test** — append to `support/caller_delegate_e2e.rs`, modeled directly on `delegated_task_dispatch_commits_the_typed_digest_and_stays_settleable` (:4-135) — copy its whole fixture (admission marks, lineage registration, pipeline assembly), changing three things:

  1. `sender` is a hanging double (with a dropped flag):

```rust
struct HangingDelegateSender {
    dropped: Arc<std::sync::atomic::AtomicBool>,
}
struct DropProbe(Arc<std::sync::atomic::AtomicBool>);
impl Drop for DropProbe {
    fn drop(&mut self) { self.0.store(true, std::sync::atomic::Ordering::SeqCst); }
}
#[async_trait]
impl CallerExecutionSendPort for HangingDelegateSender {
    async fn send_caller_execution(
        &self, _target: &BotDeliveryTarget, _cred: &RedactedToken,
        _command: SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        let _guard = DropProbe(self.dropped.clone());
        std::future::pending().await
    }
}
```

  2. the pipeline chain adds `.with_stage_timeout(Duration::from_millis(40))`;
  3. the assertion tail replaces the commit assertions with:

```rust
let row = wait_row(&managed, message_id, |row| row.state.status == Status::Unknown).await;
stop.send(true).unwrap();
let _ = tokio::time::timeout(Duration::from_secs(2), task).await;
assert!(row.state.may_have_been_sent, "send egress uncertainty keeps the maybe-sent marker");
assert_eq!(row.attempt_no, 1);
assert!(sender.dropped.load(std::sync::atomic::Ordering::SeqCst));
```

  Name it `delegate_send_stage_timeout_converges_outcome_unknown`. Check `Status` is already `use`d in the parent (`caller_p2c_e2e_contract.rs`) — mirror whatever import path its existing tests use for row status checks (`wait_row` precedents assert `row.state.status == Status::Completed`).

- [ ] **Step 2: Run it RED**

```bash
cargo nextest run -p bcs-message-flow -E 'test(delegate_send_stage_timeout)' -j 2
```

Expect: hang/failure (no wrap: the send never returns; the test times out via `wait_row`'s internal bound).

- [ ] **Step 3: Add the delegate variant** — in `delegate.rs:63-76` enum append:

```rust
    #[error("delegate dispatch stage timed out at {stage}")]
    StageTimeout { stage: &'static str },
```

- [ ] **Step 4: Apply the same six wraps as the root** (mirror Task 4 exactly, with `DelegateDispatchError` variants; add `use super::stage::stage_bound;` at the top of `delegate.rs`):

  - ensure (:207-220) → `StageTimeout { stage: "container_ensure" }`;
  - issue (:280-326) → hoist `agent_code` into the bound block as in Task 4 Step 4 → `StageTimeout { stage: "issue_execution" }`;
  - commit (:369-372) → `StageTimeout { stage: "digest_commit" }`;
  - history read (:390-392) → wrap `managed.bounded_contexts(...)`; timeout → `StageTimeout { stage: "history_read" }` (same conservative arm as its existing `map_err(|_| PreparationFailed)`);
  - send (~:485) → `else return Ok(CallerDispatchOutcome::OutcomeUnknown)`;
  - `park_delegate_barrier` in the HISTORY_NOT_READY arm → best-effort `stage_bound(..., "barrier_park", ...)` as in Task 4 Step 7.

- [ ] **Step 5: Runtime delegate arm** — in `delivery_runtime/caller_delegate.rs:260` extend:

```rust
E::PreparationFailed | E::IssueFailed | E::CommitFailed | E::StageTimeout { .. } => {
```

(the existing arm body — warn + `Event::Failed` — is unchanged; `StageTimeout` joins the conservative list).

- [ ] **Step 6: Run delegate tests GREEN**

```bash
cargo nextest run -p bcs-message-flow -E 'test(caller_delegate_e2e) or test(caller_p2c)' -j 2
```

- [ ] **Step 7: Commit**

```bash
git add crates/services/bcs-message-flow/src/caller_dispatch_pipeline/delegate.rs crates/services/bcs-message-flow/src/delivery_runtime/caller_delegate.rs crates/services/bcs-message-flow/tests/support/caller_delegate_e2e.rs
git commit -m "feat(bcs): apply the per-stage liveness bounds to the delegate dispatch path

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Runtime-side wraps (RED → GREEN)

**Files:**
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/delivery_runtime/caller_dispatch.rs` (:21 resolve, :194 bounded_contexts)
- Modify: `apps/bcs/crates/services/bcs-message-flow/tests/caller_protected_dispatch_runtime_contract.rs` (one new test; the `RecordingIo::hang_caller_target` field at :60 already exists)

- [ ] **Step 1: Failing test** — add to `caller_protected_dispatch_runtime_contract.rs` (file is 812 lines — this adds ~20; stay alert on `wc -l`):

```rust
#[tokio::test]
async fn resolve_stage_timeout_converges_failed_like_a_refusal() {
    // The resolve is the ONLY pre-StartSend stage: a liveness-bound
    // timeout must converge through the same fail-closed branch as a
    // refusal (spec 2026-11-03) — Failed, attempt 0, nothing sent.
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let io = Arc::new(RecordingIo {
        hang_caller_target: true,
        ..Default::default()
    });
    let handoff = seeded_handoff("caller-ok").await;
    let pipeline = caller_pipeline(handoff, CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() });
    let mut worker = runtime(service.clone(), io.clone(), Some(pipeline));
    worker.config.io_timeout = Duration::from_millis(40);
    service.admit(admission("caller-ok", builder_projection(true, true, true))).await.unwrap();
    let (stop, task) = start(worker);
    let row = tokio::time::timeout(Duration::from_secs(2),
        run_until_status(&service, "caller-ok", |row| row.state.status == Status::Failed)).await
        .expect("resolve timeout must converge").expect("row present");
    worker_stop(stop, task).await;
    assert_eq!(row.attempt_no, 0, "liveness bound rejects before StartSend");
    assert!(io.sent.lock().await.is_empty());
}
```

- [ ] **Step 2: Run RED** — `cargo nextest run -p bcs-message-flow -E 'test(resolve_stage_timeout)' -j 2` → FAIL (currently a hang never converges until run budget).

- [ ] **Step 3: Implement the resolve wrap** — in `caller_dispatch.rs` replace :21-29:

```rust
let Some(context) = match tokio::time::timeout(
    self.config.io_timeout,
    self.preparation.resolve_caller_dispatch(row),
).await {
    Ok(Some(context)) => Some(context),
    // A refusal IS the existing fail-closed branch below.
    Ok(None) => None,
    Err(_) => {
        tracing::warn!(
            delivery_id = %row.delivery_id,
            bot_id = %row.target_bot_id,
            "CALLER_EXECUTION_UNAVAILABLE: resolve stage exceeded its liveness bound"
        );
        None
    }
} else {
    tracing::warn!(
        delivery_id = %row.delivery_id,
        bot_id = %row.target_bot_id,
        "CALLER_EXECUTION_UNAVAILABLE: caller dispatch target does not resolve to a Provider container"
    );
    self.transition_ok(event(row, Event::PreparationFailed)).await;
    return;
};
```

- [ ] **Step 4: bounded_contexts wrap** — in `caller_dispatch.rs` replace :194-200:

```rust
let contexts = tokio::time::timeout(
    self.config.io_timeout,
    self.service.bounded_contexts(&started.delivery_id, 25),
).await;
let Ok(Ok(contexts)) = contexts else {
    // Read failure OR liveness lapse: refuse the history header rather
    // than fabricating an empty manifest (fail closed, retryable later).
    self.transition_ok(event(&started, Event::PreparationFailed)).await;
    return;
};
```

- [ ] **Step 5: GREEN + commit**

```bash
cargo nextest run -p bcs-message-flow -E 'test(caller_protected_dispatch_runtime_contract)' -j 2
git add crates/services/bcs-message-flow/src/delivery_runtime/caller_dispatch.rs crates/services/bcs-message-flow/tests/caller_protected_dispatch_runtime_contract.rs
git commit -m "feat(bcs): bound the caller resolve and history reads by io_timeout

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Remove the outer window (GREEN via watchdog)

**Files:**
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/delivery_runtime.rs` (:60-67 enum, :504-511 spawn, :531-548 handler)
- Modify: `apps/bcs/crates/services/bcs-message-flow/tests/caller_protected_dispatch_runtime_contract/watchdog.rs` (fixture + call sites)

- [ ] **Step 1: parameterize the watchdog fixture** — in `watchdog.rs` change `hanging_fixture()` to take the pipeline stage budget:

```rust
async fn hanging_fixture(stage_timeout: Duration) -> (DeliveryRuntime, Arc<ManagedMessageDelivery>, Arc<tokio::sync::Notify>, Arc<std::sync::atomic::AtomicBool>) {
    …
    let pipeline = Arc::new(CallerDispatchPipeline::new(
        seeded_handoff("caller-ok").await,
        Arc::new(CallerContainerPreparation::new(Arc::new(StubGateway))),
        Arc::new(StubIdentity),
        Arc::new(PendingSender { entered: entered.clone(), dropped: dropped.clone() }),
        600_000,
    )
    .with_stage_timeout(stage_timeout));
    …
}
```

Update every call site:
- `protected_send_timeout_converges_unknown_without_retry` → `hanging_fixture(Duration::from_millis(40)).await`; DELETE the `worker.config.io_timeout = Duration::from_millis(40);` line (the pipeline budget now drives the convergence); the four existing assertions (Unknown, attempt 1, `may_have_been_sent`, dropped flag) stay unchanged.
- `hung_protected_send_does_not_block_queue_expiry_or_shutdown` and `hung_protected_send_does_not_block_run_deadline` → `hanging_fixture(Duration::from_secs(10)).await` (the hang must persist while expiry/shutdown/run-deadline play out); DROP the `worker.config.io_timeout = Duration::from_secs(10);` lines (io_timeout no longer bounds the dispatch task).
- Any other `hanging_fixture()` call site → `hanging_fixture(Duration::from_secs(10)).await`.

- [ ] **Step 2: green check before the removal** (pipeline budget already drives send)

```bash
cargo nextest run -p bcs-message-flow -E 'test(watchdog)' -j 2 2>&1 | tail -20
```

Note: the module is included via `#[path]` from `caller_protected_dispatch_runtime_contract.rs`; `-E 'test(protected_send_timeout)'` etc. select the tests.

- [ ] **Step 3: delete the outer window** — `delivery_runtime.rs:60` enum:

```rust
enum WorkResult {
    CallerFinished(String),
    Prepared(
        PersistedMessageDelivery,
        ServiceResult<PreparedManagedDelivery>,
    ),
    Sent(String, String, Option<ServiceResult<BotDeliveryResult>>),
}
```

`:504-511` spawn:

```rust
work.spawn(async move {
    // No outer window (spec 2026-11-03): every pipeline stage carries its
    // own liveness bound inside, exactly like the ordinary path's per-I/O
    // windows; the abort handle stays the cancel-intent interruption.
    let _ = futures::future::Abortable::new(
        runtime.attempt_caller_protected_dispatch(&row),
        registration,
    ).await;
    WorkResult::CallerFinished(delivery_id)
});
```

`:531-548` handler:

```rust
WorkResult::CallerFinished(id) => {
    preparing.remove(&id);
    caller_tasks.remove(&id);
}
```

The `"caller protected dispatch timed out; no automatic resend"` WARN and the `Queued→PreparationFailed / Dispatching|Running→TransportUnknown` blanket conversions are deleted with it.

- [ ] **Step 4: full package run (must be green)**

```bash
cargo nextest run -p bcs-message-flow -j 2
```

If `hung_protected_send_does_not_block_run_deadline` flakes: it now races the 40ms send budget against the 40ms run deadline on the SAME fixture — if so, use a two-parameter fixture (send-budget 10s, `run_timeout` stays 40ms) so the deadline is what converges the row; keep the death-of-the-future assertion via the abort handle.

- [ ] **Step 5: Commit**

```bash
git add crates/services/bcs-message-flow/src/delivery_runtime.rs crates/services/bcs-message-flow/tests/caller_protected_dispatch_runtime_contract/watchdog.rs
git commit -m "feat(bcs): drop the outer caller dispatch window for per-stage bounds

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Bootstrap wiring + final validation

**Files:**
- Modify: `apps/bcs/crates/bootstrap/bcs/src/message_delivery_wiring.rs` (:254-264 pipeline chain)

- [ ] **Step 1: wire the budget** — extend the pipeline chain at `:254-264` with the builder next to the other `with_*`:

```rust
.with_stage_timeout(Duration::from_secs(30))
```

(This mirrors `DeliveryRuntimeConfig.io_timeout` at `:369`; leave the 30s literal here with a one-line comment `// per-stage liveness bound mirrors io_timeout (spec 2026-11-03)` — `Duration` is already imported in that file.)

- [ ] **Step 2: affected bootstrap tests**

```bash
cargo nextest run -p bcs -j 2
```

(`bootstrap/bcs/tests/delivery_worker_load.rs` constructed `io_timeout: Duration::from_secs(10)` worker fixtures — they no longer drive the caller task; if a load test asserts the old blanket-Unknown behavior, update it to a pipeline-budget-driven expectation, mirroring Task 7.)

- [ ] **Step 3: hygiene checks**

```bash
wc -l crates/services/bcs-message-flow/src/caller_dispatch_pipeline.rs crates/services/bcs-message-flow/src/caller_dispatch_pipeline/delegate.rs crates/services/bcs-message-flow/src/delivery_runtime.rs crates/services/bcs-message-flow/src/delivery_runtime/caller_dispatch.rs crates/services/bcs-message-flow/tests/caller_protected_dispatch_runtime_contract.rs
cargo nextest run -p bcs-message-flow -j 2
cargo build --workspace
```

Expected: all files `< 1000` lines; all tests green; workspace builds.

- [ ] **Step 4: Commit**

```bash
git add crates/bootstrap/bcs/src/message_delivery_wiring.rs
git commit -m "feat(bcs): wire the caller pipeline stage budget in the delivery wiring

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 5: Final review pass**

Run the superpowers code-review (or `/code-review`) over the whole branch diff, and update the spec's §5 ops note if any final log wording drifted (`caller protected dispatch stage timed out` is the new grep handle).

---

## Self-review notes (already applied)

- Spec coverage: §3.1 → Task 7; §3.2 → Task 6; §3.3 root → Tasks 2-4, delegate → Task 5; §4 tests → Tasks 3/5/6/7; §6 branch → Task 1; wiring amendment → Task 8. The spec's original "ensure/send 不另加包" line was amended in Task 1 Step 3 (watchdog contract discovery, documented above).
- Placeholder discipline: every `…` in the plan marks a verbatim-copy instruction whose source line range is named inline (e.g. "the remaining fields copied VERBATIM from :669-699"); the test sketch's placeholder stub names are resolved by the IMPORTANT note above. No step invents undefined types — `CallerDispatchError::StageTimeout { stage: &'static str }`, `stage_bound(budget, stage, future) -> Option<T>` and `DEFAULT_STAGE_TIMEOUT` are fully defined in Task 2 before use.
- Type consistency: `CallerDispatchError::StageTimeout { stage: &'static str }` / `DelegateDispatchError::StageTimeout { stage: &'static str }` / `stage_bound(budget, stage, future) -> Option<T>` used identically in Tasks 2/4/5.
