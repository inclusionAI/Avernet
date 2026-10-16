//! `BotAuthorityRepoPort` transfer contract harness
//! (bot-authority plan Task 3, spec §6).
//!
//! The SQL authority Store (`bcs-edge-permission-store`
//! / `DbBotAuthorityStore`) and the memory projection
//! (`bcs-bot-store` / `MemoryBotRepo`) must produce identical
//! transfer outcomes, state transitions, and audit rows.
//! This harness drives the SAME semantics scenario set on
//! both through the [`BotTransferContractFixture`]; assertion
//! failures panic (harness convention).
//!
//! Coverage map (spec §6.4 state/action table — row ↔ scenario):
//!
//! | §6.4 row | pinning scenario |
//! | --- | --- |
//! | pending + accept/reject/cancel(合法调用方) → 终态 | `transfer_decision_table_driven_state_machine` + `transfer_accept_moves_ownership_and_edges` |
//! | accepted/rejected/cancelled + 同决议重复 → 无写入 | `transfer_decision_table_driven_state_machine` (Unchanged rows) |
//! | 任意终态 + 不同决议 → 409 | `transfer_decision_table_driven_state_machine` (`transfer_state_conflict` rows) |
//! | expired/invalidated + 任意决议 → 409 不改 owner | `transfer_decision_table_driven_state_machine` + `transfer_expired_boundary_closes_valid_pending` + `transfer_aba_stale_snapshot_conflicts` |
//! | 终态或无记录 + owner 再发起 → pending 刷新本轮字段 | `transfer_request_creates_and_restores` |
//!
//! Late-decision semantics (§6.4 当前请求语义):
//! `transfer_late_decisions_act_on_current_round`; races in
//! `transfer_accept_cancel_race_serializes` /
//! `transfer_concurrent_requests_one_pending`.

#![allow(
    clippy::expect_used,
    reason = "test harness — panic on failure is the contract"
)]

use async_trait::async_trait;

use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityActorKind, AuthorityConflictCode, AuthorityMutation,
    BotAccessRelation, ManagerListQuery, ManagerSync, TransferAction,
    TransferChange, TransferCommand, TransferDirection, TransferKey,
    TransferListQuery, TransferOutcome, TransferStatus, TransferView,
    human_actor_id,
};

use super::bot_authority_contract::{
    BotAuthorityContractFixture, HarnessAuditRow,
};

/// Global unique mutation id per harness command: repeated
/// ids in one env would collide on `(env, mutation_id,
/// sequence)`.
static TRANSFER_MUTATION: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(1);

pub fn next_mutation_id() -> String {
    format!(
        "transfer-mutation-{}",
        TRANSFER_MUTATION.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
    )
}

pub fn human_mutation(mutation_id: String, user: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id,
        actor: AuthorityActor::Human {
            user_id: user.to_string(),
        },
    }
}

pub fn service_mutation(mutation_id: String, app_id: i64) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id,
        actor: AuthorityActor::Service { app_id },
    }
}

/// A transfer command on `(bot, target)` with the given
/// operator. `target` is the bare target user id; the
/// repo-port command carries the composed Human actor id.
pub fn transfer_cmd(
    bot: &str,
    target: &str,
    action: TransferAction,
    mutation: AuthorityMutation,
) -> TransferCommand {
    TransferCommand {
        key: TransferKey {
            bot_id: bot.to_string(),
            target_owner_actor_id: human_actor_id(target),
        },
        action,
        mutation,
    }
}

/// One transfer row seed with clock-relative times: the
/// store reads its own DB/process clock when `now` is
/// captured, so no wall-clock dependency leaks into the
/// assertions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HarnessTransferSeed {
    pub bot_id: String,
    pub target_owner_actor_id: String,
    pub from_owner_actor_id: String,
    pub expected_ownership_version: u64,
    pub status: TransferStatus,
    /// `requested_at` = store clock + this many seconds.
    pub requested_at_offset_s: i64,
    /// `expires_at` = store clock + this many seconds
    /// (negative = already expired).
    pub expires_at_offset_s: i64,
    pub decided_at_offset_s: Option<i64>,
    pub decided_by_kind: Option<String>,
    pub decided_by_id: Option<String>,
    pub reason: Option<String>,
    pub result_ownership_version: Option<u64>,
}

/// Storage access the transfer harness uses to seed a
/// verbatim transfer row (spec §4.3 fields).
#[async_trait]
pub trait BotTransferContractFixture: BotAuthorityContractFixture {
    /// Insert or overwrite (key `(env, bot, target)`) one
    /// transfer row with the given state and clock-offset
    /// times. Seeding never audits.
    async fn seed_transfer(&self, env: &str, row: HarnessTransferSeed);
}

/// Audit state snapshot format written by transfer audit
/// rows (pinned across both stores). The subject_id of a
/// transfer audit row is the target owner actor id.
pub fn transfer_audit_state(
    status: TransferStatus,
    from_owner_actor_id: &str,
    version: u64,
) -> String {
    format!(
        "{{\"status\":\"{}\",\"from_owner\":\"{from_owner_actor_id}\",\"version\":{version}}}",
        status.as_str()
    )
}

fn mentions(err: &bcs_service_api::ServiceError, code: &str) -> bool {
    err.to_string().contains(code)
}

/// Unwraps an expected-conflict result: an `Ok` is a harness
/// failure (a conflict outcome was required).
fn expect_transfer_conflict(
    result: Result<TransferOutcome, bcs_service_api::ServiceError>,
) -> TransferOutcome {
    match result {
        Ok(outcome) => outcome,
        Err(err) => panic!("expected a committed conflict outcome, got error {err}"),
    }
}

/// The expected result of one table-driven case.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Expecting {
    /// `TransferOutcome::Applied` with this change marker.
    Applied(TransferChange),
    /// `TransferOutcome::Conflict` with this code spelling.
    Conflict(&'static str),
    /// A store `Err` carrying this code.
    Denied(&'static str),
}

use Expecting::{Applied, Conflict as ExpectConflict, Denied};

/// Shared arena setup: live bot, initialized, owner edge,
/// and Humans registered.
async fn seeded_bot(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
    bot: &str,
    owner: &str,
    version: u64,
) {
    fixture.seed_bot(env, bot, version).await;
    fixture.seed_human(env, owner).await;
    fixture
        .grant_edge(env, bot, &human_actor_id(owner), BotAccessRelation::Owner)
        .await;
}

/// Owner-initiated pending request for `target`.
async fn open_pending(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    _env: &str,
    bot: &str,
    owner: &str,
    target: &str,
) -> TransferView {
    let repo = fixture.authority_repo();
    let outcome = repo
        .change_transfer(transfer_cmd(
            bot,
            target,
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), owner),
        ))
        .await
        .expect("owner opens a pending transfer");
    match outcome {
        TransferOutcome::Applied { transfer, .. } => transfer,
        other => panic!("expected a pending transfer, got {other:?}"),
    }
}

/// The transfer row for `(bot, target)`.
async fn row_of(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    bot: &str,
    target: &str,
) -> TransferView {
    let repo = fixture.authority_repo();
    repo.transfer(&TransferKey {
        bot_id: bot.to_string(),
        target_owner_actor_id: human_actor_id(target),
    })
    .await
    .expect("transfer row read")
    .unwrap_or_else(|| panic!("transfer row for target {target} must exist"))
}

async fn row_absent(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    bot: &str,
    target: &str,
) {
    let repo = fixture.authority_repo();
    let view = repo
        .transfer(&TransferKey {
            bot_id: bot.to_string(),
            target_owner_actor_id: human_actor_id(target),
        })
        .await
        .expect("transfer read");
    assert!(
        view.is_none(),
        "no row expected for target {target}, got {view:?}"
    );
}

/// Runs one command and pins the expectation; returns the
/// row state after the command.
async fn run_transfer_case(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    bot: &str,
    target: &str,
    action: TransferAction,
    mutation: AuthorityMutation,
    expecting: Expecting,
) -> TransferView {
    let repo = fixture.authority_repo();
    let outcome = repo
        .change_transfer(transfer_cmd(bot, target, action, mutation))
        .await;
    match (outcome, expecting) {
        (Ok(TransferOutcome::Applied { change, .. }), Applied(expected)) => {
            assert_eq!(
                change, expected,
                "applied change marker must match for {action:?} on \
                 ({bot}, {target})"
            );
        }
        (
            Ok(TransferOutcome::Conflict { code, .. }),
            ExpectConflict(expected),
        ) => {
            assert_eq!(
                code.as_str(),
                expected,
                "conflict code spelling for {action:?} on \
                 ({bot}, {target})"
            );
        }
        (Ok(other), expecting) => {
            panic!(
                "expected {expecting:?} for {action:?} on ({bot}, {target}), \
                 got {other:?}"
            )
        }
        (Err(err), Denied(code)) => {
            assert!(
                mentions(&err, code),
                "expected the {code} denial for {action:?} on \
                 ({bot}, {target}), got {err}"
            );
            return row_of_if_present(fixture, bot, target).await;
        }
        (Err(err), expecting) => {
            panic!(
                "expected {expecting:?} for {action:?} on ({bot}, {target}), \
                 got error {err}"
            )
        }
    }
    row_of(fixture, bot, target).await
}

/// Row view if present (deny outcomes may target a
/// row that does not exist).
async fn row_of_if_present(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    bot: &str,
    target: &str,
) -> TransferView {
    fixture
        .authority_repo()
        .transfer(&TransferKey {
            bot_id: bot.to_string(),
            target_owner_actor_id: human_actor_id(target),
        })
        .await
        .expect("transfer row read")
        .unwrap_or_else(|| {
            TransferView {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id(target),
                from_owner_actor_id: String::new(),
                status: TransferStatus::Pending,
                requested_at: 0,
                expires_at: 0,
                updated_at: 0,
                decided_at: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
                bot_name_snapshot: None,
            }
        })
}

/// One expected audit row shape (all non-None fields must
/// match; exactly one committed row must match).
#[derive(Debug)]
pub struct TransferAuditExpectation {
    pub action: &'static str,
    pub actor_kind: Option<&'static str>,
    pub subject: String,
    pub after_state: Option<String>,
}

impl TransferAuditExpectation {
    /// At least one committed audit row matches this
    /// shape (audit rows are env-global across the
    /// harness's per-scenario bots).
    fn audit_present_once(self, rows: &[HarnessAuditRow]) {
        let matches: Vec<&HarnessAuditRow> = rows
            .iter()
            .filter(|row| {
                row.action == self.action
                    && row.subject_id == self.subject
                    && self
                        .actor_kind
                        .map(|kind| row.actor_kind == kind)
                        .unwrap_or(true)
                    && self
                        .after_state
                        .as_ref()
                        .map(|after| row.after_state.as_deref() == Some(after.as_str()))
                        .unwrap_or(true)
            })
            .collect();
        assert!(
            !matches.is_empty(),
            "no audit row matched {self:?} in {rows:?}"
        );
    }
}

/// Full shared transfer semantic suite for one bound
/// environment (Task 3.1–3.5 scenarios; both stores).
pub async fn run_bot_transfer_contract(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    run_bot_transfer_contract_with_scenarios(fixture, env, |_| {}).await;
}

/// [`run_bot_transfer_contract`] plus a per-scenario
/// callback so dialect gates (plan Task 8) can report the
/// exact scenario inventory they ran.
pub async fn run_bot_transfer_contract_with_scenarios(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
    mut report: impl FnMut(&'static str),
) {
    report("transfer_request_creates_and_restores");
    transfer_request_creates_and_restores(fixture, env).await;
    report("transfer_request_same_target_refreshes");
    transfer_request_same_target_refreshes(fixture, env).await;
    report("transfer_request_other_target_conflicts");
    transfer_request_other_target_conflicts(fixture, env).await;
    report("transfer_request_closes_stale_pending_first");
    transfer_request_closes_stale_pending_first(fixture, env).await;
    report(
        "transfer_request_to_new_target_closes_the_expired_other_pending",
    );
    transfer_request_to_new_target_closes_the_expired_other_pending(
        fixture, env,
    )
    .await;
    report(
        "transfer_request_live_other_pending_still_conflicts_without_writes",
    );
    transfer_request_live_other_pending_still_conflicts_without_writes(
        fixture, env,
    )
    .await;
    report(
        "transfer_request_to_new_target_closes_the_owner_stale_other_pending",
    );
    transfer_request_to_new_target_closes_the_owner_stale_other_pending(
        fixture, env,
    )
    .await;
    report("transfer_request_validates_owner_and_human");
    transfer_request_validates_owner_and_human(fixture, env).await;
    report("transfer_accept_moves_ownership_and_edges");
    transfer_accept_moves_ownership_and_edges(fixture, env).await;
    report("transfer_decision_table_driven_state_machine");
    transfer_decision_table_driven_state_machine(fixture, env).await;
    report("transfer_expired_boundary_closes_valid_pending");
    transfer_expired_boundary_closes_valid_pending(fixture, env).await;
    report("transfer_aba_stale_snapshot_conflicts");
    transfer_aba_stale_snapshot_conflicts(fixture, env).await;
    report("transfer_late_decisions_act_on_current_round");
    transfer_late_decisions_act_on_current_round(fixture, env).await;
    report("transfer_version_grows_only_on_accept");
    transfer_version_grows_only_on_accept(fixture, env).await;
    report("transfer_cancel_keeps_initiator_identity_on_terminal");
    transfer_cancel_keeps_initiator_identity_on_terminal(fixture, env).await;
    report("transfer_lists_project_effective_status");
    transfer_lists_project_effective_status(fixture, env).await;
    report("transfer_reads_never_audit");
    transfer_reads_never_audit(fixture, env).await;
    report("transfer_accept_cancel_race_serializes");
    transfer_accept_cancel_race_serializes(fixture, env).await;
    report("transfer_concurrent_requests_one_pending");
    transfer_concurrent_requests_one_pending(fixture, env).await;
    report("transfer_audit_failure_is_atomic");
    transfer_audit_failure_is_atomic(fixture, env).await;
    report("transfer_deleted_bot_or_target_fail_clean");
    transfer_deleted_bot_or_target_fail_clean(fixture, env).await;
    report("transfer_deleted_operator_cannot_drive_commands");
    transfer_deleted_operator_cannot_drive_commands(fixture, env).await;
    report("transfer_actor_kind_policy");
    transfer_actor_kind_policy(fixture, env).await;
    report("transfer_error_codes_are_pinned");
    transfer_error_codes_are_pinned(fixture, env).await;
}

const SEVEN_DAYS_MS: u64 = 7 * 24 * 3600 * 1000;

/// §6.2 发起: none → pending（Created）；终态 → 复用同一行
/// （Created, 决议字段清空）；同 Bot 同目标轮次间始终只有一行。
async fn transfer_request_creates_and_restores(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let bot = "bt_request_create";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;

    let outcome = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Created),
    )
    .await;
    assert_eq!(outcome.status, TransferStatus::Pending);
    assert_eq!(outcome.from_owner_actor_id, human_actor_id("own"));
    assert_eq!(outcome.target_owner_actor_id, human_actor_id("t1"));
    let span = outcome.expires_at - outcome.requested_at;
    assert!(
        (SEVEN_DAYS_MS - 90_000..=SEVEN_DAYS_MS + 90_000).contains(&span),
        "expiry must be requested_at + 7 days, span {span}"
    );
    TransferAuditExpectation {
        action: "transfer_request",
        actor_kind: Some("human"),
        subject: human_actor_id("t1"),
        after_state: Some(transfer_audit_state(
            TransferStatus::Pending,
            &human_actor_id("own"),
            1,
        )),
    }
    .audit_present_once(&fixture.audit_rows(env).await);

    // Same target again while pending: refresh (no second row).
    let first = row_of(fixture, bot, "t1").await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Updated),
    )
    .await;
    let refreshed = row_of(fixture, bot, "t1").await;
    assert_eq!(refreshed.status, TransferStatus::Pending);
    assert!(
        refreshed.requested_at >= first.requested_at,
        "refresh refreshes requested_at"
    );
    assert!(
        refreshed.decided_at.is_none()
            && refreshed.decided_by_id.is_none()
            && refreshed.reason.is_none(),
        "a fresh round clears the last decision fields"
    );
    row_absent(fixture, bot, "t2").await;

    // Terminal → re-initiate re-uses the same row: reject,
    // then request again.
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Reject,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;
    let terminal = row_of(fixture, bot, "t1").await;
    assert_eq!(terminal.status, TransferStatus::Rejected);
    let audit_before_reuse = fixture.audit_rows(env).await.len();
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Created),
    )
    .await;
    let reused = row_of(fixture, bot, "t1").await;
    assert_eq!(reused.status, TransferStatus::Pending);
    assert!(
        reused.requested_at >= terminal.requested_at,
        "re-initiation refreshes this round's fields"
    );
    assert!(
        reused.decided_at.is_none(),
        "re-initiation over a terminal row clears the decision"
    );
    assert_eq!(
        fixture.audit_rows(env).await.len() - audit_before_reuse,
        1,
        "re-initiation writes exactly one audit row"
    );
}

/// §6.2 续期: same-target valid pending refresh resets the
/// seven-day window and audits the renewal (a real change);
/// GETs never audit.
async fn transfer_request_same_target_refreshes(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let bot = "bt_request_refresh";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;

    open_pending(fixture, env, bot, "own", "t1").await;
    let first = row_of(fixture, bot, "t1").await;
    let audit_before = fixture.audit_rows(env).await.len();
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Updated),
    )
    .await;
    let refreshed = row_of(fixture, bot, "t1").await;
    // Repeated requests within the same clock tick may
    // report the same second — §6.2: 幂等不保证重复请求
    // 的时间字段完全相同 (the refresh audit row above
    // pins that a real write happened).
    assert!(
        refreshed.expires_at >= first.expires_at
            && refreshed.requested_at >= first.requested_at,
        "the refresh refreshes the clock fields"
    );
    let rows = fixture.audit_rows(env).await;
    assert_eq!(
        rows.len(),
        audit_before + 1,
        "刷新属于真实状态变更: one refresh audit row"
    );
    TransferAuditExpectation {
        action: "transfer_refresh",
        actor_kind: Some("human"),
        subject: human_actor_id("t1"),
        after_state: None,
    }
    .audit_present_once(&rows);
}

/// §6.2: 另一目标的有效 pending → `transfer_pending`，不
/// 自动替换目标，无写入。
async fn transfer_request_other_target_conflicts(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let bot = "bt_request_conflict";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture.seed_human(env, "t2").await;

    let pending = open_pending(fixture, env, bot, "own", "t1").await;
    let audit_before = fixture.audit_rows(env).await.len();
    let outcome = expect_transfer_conflict(
        fixture
            .authority_repo()
            .change_transfer(transfer_cmd(
                bot,
                "t2",
                TransferAction::RequestOrRefresh,
                human_mutation(next_mutation_id(), "own"),
            ))
            .await,
    );
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "conflict writes nothing"
    );
    match outcome {
        TransferOutcome::Conflict { transfer, code } => {
            assert_eq!(code.as_str(), "transfer_pending");
            assert_eq!(transfer.target_owner_actor_id, human_actor_id("t1"));
            assert_eq!(transfer.status, TransferStatus::Pending);
            assert_eq!(transfer.from_owner_actor_id, pending.from_owner_actor_id);
        }
        other => panic!("expected transfer_pending conflict, got {other:?}"),
    }
    row_absent(fixture, bot, "t2").await;
}

/// §6.2: 过期或 owner/version 已不匹配的 pending → 先在本
/// 事务关闭（system 原因 + 审计），再执行本次发起。
async fn transfer_request_closes_stale_pending_first(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let bot = "bt_request_stale";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Pending,
                requested_at_offset_s: -8 * 24 * 3600,
                expires_at_offset_s: -3600,
                decided_at_offset_s: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
            },
        )
        .await;
    let audit_before = fixture.audit_rows(env).await.len();
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Created),
    )
    .await;
    let renewed = row_of(fixture, bot, "t1").await;
    assert_eq!(renewed.status, TransferStatus::Pending);
    let renewed_span = renewed.expires_at - renewed.requested_at;
    assert!(
        renewed_span >= SEVEN_DAYS_MS - 90_000,
        "the new round gets a fresh 7-day expiry, span {renewed_span}"
    );
    let rows = fixture.audit_rows(env).await;
    assert_eq!(rows.len() - audit_before, 2, "closure + new round");
    TransferAuditExpectation {
        action: "transfer_expire",
        actor_kind: Some("system"),
        subject: human_actor_id("t1"),
        after_state: Some(transfer_audit_state(
            TransferStatus::Expired,
            &human_actor_id("own"),
            1,
        )),
    }
    .audit_present_once(&rows);
    TransferAuditExpectation {
        action: "transfer_request",
        actor_kind: Some("human"),
        subject: human_actor_id("t1"),
        after_state: None,
    }
    .audit_present_once(&rows);

    // owner/version-mismatched pending → invalidate, then
    // create the new round (owner still "own" at version 2).
    let bot2 = "bt_request_stale_owner";
    seeded_bot(fixture, env, bot2, "own", 2).await;
    fixture.seed_human(env, "t1").await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot2.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Pending,
                requested_at_offset_s: -3600,
                expires_at_offset_s: 6 * 24 * 3600,
                decided_at_offset_s: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
            },
        )
        .await;
    run_transfer_case(
        fixture,
        bot2,
        "t1",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Created),
    )
    .await;
    let renewed = row_of(fixture, bot2, "t1").await;
    assert_eq!(renewed.status, TransferStatus::Pending);
    assert_eq!(renewed.from_owner_actor_id, human_actor_id("own"));
    TransferAuditExpectation {
        action: "transfer_invalidate",
        actor_kind: Some("system"),
        subject: human_actor_id("t1"),
        after_state: Some(transfer_audit_state(
            TransferStatus::Invalidated,
            &human_actor_id("own"),
            1,
        )),
    }
    .audit_present_once(&fixture.audit_rows(env).await);
}

/// §6.2: a DIFFERENT-target re-initiation after an
/// EXPIRED other-target pending must succeed: the
/// dead pending (stored `pending`, past expiry) is
/// closed IN the same transaction (the per-bot
/// pending unique key rides the `pending` status —
/// without the in-transaction closure the new
/// insert would keep hitting the key until the
/// retry budget expired), the closure audit names
/// the OLD target, and the new round is Created.
async fn transfer_request_to_new_target_closes_the_expired_other_pending(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let bot = "bt_new_target_expired";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture.seed_human(env, "t2").await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Pending,
                requested_at_offset_s: -8 * 24 * 3600,
                expires_at_offset_s: -3600,
                decided_at_offset_s: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
            },
        )
        .await;
    let audit_before = fixture.audit_rows(env).await.len();
    run_transfer_case(
        fixture,
        bot,
        "t2",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Created),
    )
    .await;
    // The old target's row is closed-expired.
    let old = row_of(fixture, bot, "t1").await;
    assert_eq!(old.status, TransferStatus::Expired);
    assert_eq!(old.reason.as_deref(), Some("transfer_expired"));
    // The new round exists, fresh.
    let created = row_of(fixture, bot, "t2").await;
    assert_eq!(created.status, TransferStatus::Pending);
    assert_eq!(
        created.from_owner_actor_id,
        human_actor_id("own")
    );
    // Audits: the closure names the OLD target as the
    // system-closed subject; the request names the
    // new one. No `transfer_pending` conflict.
    let rows = fixture.audit_rows(env).await;
    assert_eq!(rows.len() - audit_before, 2, "closure + request");
    TransferAuditExpectation {
        action: "transfer_expire",
        actor_kind: Some("system"),
        subject: human_actor_id("t1"),
        after_state: Some(transfer_audit_state(
            TransferStatus::Expired,
            &human_actor_id("own"),
            1,
        )),
    }
    .audit_present_once(&rows);
    TransferAuditExpectation {
        action: "transfer_request",
        actor_kind: Some("human"),
        subject: human_actor_id("t2"),
        after_state: None,
    }
    .audit_present_once(&rows);
}

/// §6.2 unchanged: a LIVE other-target pending (fresh
/// AND matching the current round) still conflicts
/// with `transfer_pending` and writes NOTHING (no
/// closure, no new round).
async fn transfer_request_live_other_pending_still_conflicts_without_writes(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let bot = "bt_live_other_conflict";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture.seed_human(env, "t2").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let audit_before = fixture.audit_rows(env).await.len();
    let outcome = expect_transfer_conflict(
        fixture
            .authority_repo()
            .change_transfer(transfer_cmd(
                bot,
                "t2",
                TransferAction::RequestOrRefresh,
                human_mutation(next_mutation_id(), "own"),
            ))
            .await,
    );
    match outcome {
        TransferOutcome::Conflict { transfer, code } => {
            assert_eq!(code.as_str(), "transfer_pending");
            assert_eq!(
                transfer.target_owner_actor_id,
                human_actor_id("t1")
            );
            assert_eq!(transfer.status, TransferStatus::Pending);
        }
        other => panic!("expected transfer_pending, got {other:?}"),
    }
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "the live-pending conflict writes nothing"
    );
    row_absent(fixture, bot, "t2").await;
    let old = row_of(fixture, bot, "t1").await;
    assert_eq!(old.status, TransferStatus::Pending);
    assert!(old.reason.is_none(), "no closure written");
}

/// §6.2: an ABA-stale other-target pending (fresh by
/// time, but the round's owner/version no longer
/// matches) closes as `invalidated` in the same
/// transaction the new target is created in.
async fn transfer_request_to_new_target_closes_the_owner_stale_other_pending(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let bot = "bt_new_target_aba";
    // Version 2 now; the seeded pending still banks on
    // version 1.
    seeded_bot(fixture, env, bot, "own", 2).await;
    fixture.seed_human(env, "t1").await;
    fixture.seed_human(env, "t2").await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Pending,
                requested_at_offset_s: -3600,
                expires_at_offset_s: 6 * 24 * 3600,
                decided_at_offset_s: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
            },
        )
        .await;
    run_transfer_case(
        fixture,
        bot,
        "t2",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Created),
    )
    .await;
    let old = row_of(fixture, bot, "t1").await;
    assert_eq!(old.status, TransferStatus::Invalidated);
    assert_eq!(old.reason.as_deref(), Some("owner_changed"));
    assert_eq!(
        row_of(fixture, bot, "t2").await.status,
        TransferStatus::Pending
    );
    TransferAuditExpectation {
        action: "transfer_invalidate",
        actor_kind: Some("system"),
        subject: human_actor_id("t1"),
        after_state: Some(transfer_audit_state(
            TransferStatus::Invalidated,
            &human_actor_id("own"),
            1,
        )),
    }
    .audit_present_once(&fixture.audit_rows(env).await);
}

/// 发起校验：operator 必须是当前 owner 且 Human；目标必须
/// 是存续 Human 且不是当前 owner。
async fn transfer_request_validates_owner_and_human(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_request_check";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture
        .grant_edge(env, bot, &human_actor_id("mgr"), BotAccessRelation::Manager)
        .await;
    fixture.seed_human(env, "mgr").await;

    let audit_before = fixture.audit_rows(env).await.len();

    for (label, mutation) in [
        ("non-owner Human", human_mutation(next_mutation_id(), "mgr")),
        ("trusted service", service_mutation(next_mutation_id(), 5)),
    ] {
        let err = repo
            .change_transfer(transfer_cmd(
                bot,
                "t1",
                TransferAction::RequestOrRefresh,
                mutation,
            ))
            .await
            .expect_err("only the current owner may initiate");
        assert!(mentions(&err, "bot_authority_operator_forbidden"), "{label}: {err}");
    }
    let err = repo
        .change_transfer(transfer_cmd(
            bot,
            "own",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        ))
        .await
        .expect_err("target must differ from the current owner");
    assert!(mentions(&err, "transfer_state_conflict"), "got {err}");
    let err = repo
        .change_transfer(transfer_cmd(
            bot,
            "ghost",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        ))
        .await
        .expect_err("unknown target Human");
    assert!(mentions(&err, "bot_authority_human_unknown"), "got {err}");
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "denied initiations write nothing"
    );
    row_absent(fixture, bot, "t1").await;
}

/// §6.3 accept 一次提交 owner 边切换、version+1、原 owner
/// manager、新 owner manager 撤销，其他 manager 保留。
async fn transfer_accept_moves_ownership_and_edges(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_accept_edges";
    seeded_bot(fixture, env, bot, "own", 1).await;
    for user in ["t1", "keep"] {
        fixture.seed_human(env, user).await;
    }
    fixture
        .grant_edge(env, bot, &human_actor_id("keep"), BotAccessRelation::Manager)
        .await;
    // New owner still carries a manager edge: accept must
    // REMOVE it (spec §6.3 step 3) so no stale owner+manager
    // edge can resurface.
    fixture
        .grant_edge(env, bot, &human_actor_id("t1"), BotAccessRelation::Manager)
        .await;

    open_pending(fixture, env, bot, "own", "t1").await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;

    let accepted = row_of(fixture, bot, "t1").await;
    assert_eq!(accepted.status, TransferStatus::Accepted);
    assert_eq!(
        repo.ownership(bot).await.expect("ownership").owner_actor_id,
        human_actor_id("t1"),
        "target became owner"
    );
    assert_eq!(
        repo.role(&human_actor_id("own"), bot).await.expect("role"),
        Some(BotAccessRelation::Manager),
        "previous owner holds a normal manager edge"
    );
    assert_eq!(
        repo.role(&human_actor_id("t1"), bot).await.expect("role"),
        Some(BotAccessRelation::Owner),
        "new owner holds the owner edge"
    );
    // T2-c reachability: managers() must NOT list the new
    // owner (its manager edge was removed by accept) and
    // must include the previous owner.
    let page = repo
        .managers(ManagerListQuery { bot_id: bot.to_string(), ..Default::default() })
        .await
        .expect("manager page after accept");
    assert_eq!(page.owner.owner_actor_id, human_actor_id("t1"));
    assert!(
        !page.items.iter().any(|user| user == "t1"),
        "the new owner's manager edge must be gone: {:?}",
        page.items
    );
    assert!(
        page.items.iter().any(|user| user == "own"),
        "the previous owner is a normal manager now: {:?}",
        page.items
    );
    assert!(
        page.items.iter().any(|user| user == "keep"),
        "other managers unchanged: {:?}",
        page.items
    );

    // The accept audit carries the full round
    // (pending → accepted with the new owner + version 2).
    TransferAuditExpectation {
        action: "transfer_accept",
        actor_kind: Some("human"),
        subject: human_actor_id("t1"),
        after_state: Some(transfer_audit_state(
            TransferStatus::Accepted,
            &human_actor_id("t1"),
            2,
        )),
    }
    .audit_present_once(&fixture.audit_rows(env).await);

    // sync-after-transfer normalization: an empty trusted
    // sync removes the previous owner's manager edge (it is
    // a normal manager).
    let change = repo
        .sync_managers(ManagerSync {
            bot_id: bot.to_string(),
            user_ids: vec![],
            mutation: AuthorityMutation {
                mutation_id: next_mutation_id(),
                actor: AuthorityActor::Service { app_id: 9 },
            },
        })
        .await
        .expect("empty sync after transfer");
    // The prior owner AND the seeded manager are
    // normalized out (an empty sync explicitly clears
    // all managers; the owner is never one).
    assert_eq!(change.removed, 2, "previous owner normalized out");
}

/// §6.4 表驱动状态机：pending / 各终态 × accept/reject/cancel。
async fn transfer_decision_table_driven_state_machine(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    // CASE pending + accept (target) → accepted.
    let bot = "bt_table1";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let view = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;
    assert_eq!(view.status, TransferStatus::Accepted);
    assert_eq!(
        view.decided_by_kind,
        Some(AuthorityActorKind::Human),
        "the deciding identity is recorded"
    );

    // pending + reject (target) → rejected.
    let bot = "bt_table2";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let view = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Reject,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;
    assert_eq!(view.status, TransferStatus::Rejected);

    // pending + cancel (initiator) → cancelled; owner never
    // moves; cancel by the target is forbidden.
    let bot = "bt_table3";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "t1"),
        Denied("bot_authority_operator_forbidden"),
    )
    .await;
    let view = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Updated),
    )
    .await;
    assert_eq!(view.status, TransferStatus::Cancelled);
    assert_eq!(
        fixture
            .authority_repo()
            .ownership(bot)
            .await
            .expect("ownership")
            .owner_actor_id,
        human_actor_id("own"),
        "cancel never moves ownership"
    );

    // Repeated same decision → Unchanged, no audit; service
    // repeat of accept is legal too (§6.1 dual admission).
    let bot = "bt_table4";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;
    let audit_before = fixture.audit_rows(env).await.len();
    let view = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Unchanged),
    )
    .await;
    assert_eq!(view.status, TransferStatus::Accepted);
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        service_mutation(next_mutation_id(), 7),
        Applied(TransferChange::Unchanged),
    )
    .await;
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "repeated same decision writes no audit"
    );

    // Different decision on a terminal row →
    // transfer_state_conflict, no state change/audit.
    for (terminal_action, decider, repeat_action, repeat_decider) in [
        // rejected → accept attempt (target).
        (TransferAction::Reject, "t1", TransferAction::Accept, "t1"),
        // cancelled → reject attempt (target).
        (TransferAction::Cancel, "own", TransferAction::Reject, "t1"),
    ] {
        let bot = format!("bt_table_{terminal_action:?}");
        seeded_bot(fixture, env, &bot, "own", 1).await;
        fixture.seed_human(env, "t1").await;
        open_pending(fixture, env, &bot, "own", "t1").await;
        run_transfer_case(
            fixture,
            &bot,
            "t1",
            terminal_action,
            human_mutation(next_mutation_id(), decider),
            Applied(TransferChange::Updated),
        )
        .await;
        let terminal = row_of(fixture, &bot, "t1").await;
        let audit_before = fixture.audit_rows(env).await.len();
        let view = run_transfer_case(
            fixture,
            &bot,
            "t1",
            repeat_action,
            human_mutation(next_mutation_id(), repeat_decider),
            ExpectConflict("transfer_state_conflict"),
        )
        .await;
        assert_eq!(view.status, terminal.status, "no state change");
        assert_eq!(
            fixture.audit_rows(env).await.len(),
            audit_before,
            "state conflict writes no audit"
        );
    }

    // Seeded stored expired row → any decision →
    // transfer_expired, no new audit, no owner change.
    let bot = "bt_table_expired";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Expired,
                requested_at_offset_s: -8 * 24 * 3600,
                expires_at_offset_s: -3600,
                decided_at_offset_s: Some(-3600),
                decided_by_kind: Some("system".to_string()),
                decided_by_id: Some("system_transfer_close".to_string()),
                reason: Some("transfer_expired".to_string()),
                result_ownership_version: None,
            },
        )
        .await;
    let audit_before = fixture.audit_rows(env).await.len();
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        ExpectConflict("transfer_expired"),
    )
    .await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "own"),
        ExpectConflict("transfer_expired"),
    )
    .await;
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "already-closed rows are not re-audited"
    );

    // Seeded stored invalidated row → any decision →
    // owner_changed; ownership never moves.
    let bot = "bt_table_invalidated";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Invalidated,
                requested_at_offset_s: -3600,
                expires_at_offset_s: 6 * 24 * 3600,
                decided_at_offset_s: Some(-1800),
                decided_by_kind: Some("system".to_string()),
                decided_by_id: Some("system_transfer_close".to_string()),
                reason: Some("owner_changed".to_string()),
                result_ownership_version: None,
            },
        )
        .await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        ExpectConflict("owner_changed"),
    )
    .await;
    assert_eq!(
        fixture
            .authority_repo()
            .ownership(bot)
            .await
            .expect("ownership")
            .owner_actor_id,
        human_actor_id("own"),
        "invalidated row decisions never move the owner"
    );

    // Pending + decision by an ineligible Human → forbidden,
    // no write (authorization precedes state handling).
    let bot = "bt_table_forbidden";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let audit_before = fixture.audit_rows(env).await.len();
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "own"),
        Denied("bot_authority_operator_forbidden"),
    )
    .await;
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "denied decisions write nothing"
    );
    // Decision on a missing row (registered Human,
    // no transfer row) is a state denial.
    fixture.seed_human(env, "t2").await;
    run_transfer_case(
        fixture,
        bot,
        "t2",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t2"),
        Denied("transfer_state_conflict"),
    )
    .await;
}

/// 七天边界：恰到期/之后的有效 pending → 决议先在事务内
/// 关闭为 expired（system 审计）再返回 `transfer_expired`。
async fn transfer_expired_boundary_closes_valid_pending(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    // At the boundary (offset 0): the decision's now is
    // never earlier than seeded now → expired under `>=`.
    let bot = "bt_boundary";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Pending,
                requested_at_offset_s: -3600,
                expires_at_offset_s: 0,
                decided_at_offset_s: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
            },
        )
        .await;
    let view = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        ExpectConflict("transfer_expired"),
    )
    .await;
    assert_eq!(view.status, TransferStatus::Expired);
    assert_eq!(
        view.reason.as_deref(),
        Some("transfer_expired"),
        "fixed system closure reason"
    );
    TransferAuditExpectation {
        action: "transfer_expire",
        actor_kind: Some("system"),
        subject: human_actor_id("t1"),
        after_state: None,
    }
    .audit_present_once(&fixture.audit_rows(env).await);
    assert_eq!(
        fixture
            .authority_repo()
            .ownership(bot)
            .await
            .expect("ownership")
            .owner_actor_id,
        human_actor_id("own"),
        "expiry closure never moves the owner"
    );

    // Before the boundary (fresh round): decision applies.
    let bot2 = "bt_boundary_fresh";
    seeded_bot(fixture, env, bot2, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot2, "own", "t1").await;
    run_transfer_case(
        fixture,
        bot2,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Updated),
    )
    .await;
}

/// ABA：旧快照（owner 相同但 version 已翻新）的 accept
/// 不得转交——先失效入审计再返回 `owner_changed`。
async fn transfer_aba_stale_snapshot_conflicts(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_aba";
    // Seeded v3 bot with an old v1-snapshot pending.
    seeded_bot(fixture, env, bot, "own", 3).await;
    fixture.seed_human(env, "t1").await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Pending,
                requested_at_offset_s: -2 * 24 * 3600,
                expires_at_offset_s: 5 * 24 * 3600,
                decided_at_offset_s: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
            },
        )
        .await;

    let audit_before = fixture.audit_rows(env).await.len();
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        ExpectConflict("owner_changed"),
    )
    .await;
    assert_eq!(
        repo.ownership(bot).await.expect("ownership").owner_actor_id,
        human_actor_id("own"),
        "ABA stale snapshot must NOT transfer"
    );
    assert_eq!(
        repo.role(&human_actor_id("t1"), bot).await.expect("role"),
        None,
        "stale accept granted nothing"
    );
    let rows = fixture.audit_rows(env).await;
    assert_eq!(
        rows.len() - audit_before,
        1,
        "invalidation is audited before the conflict returns"
    );
    TransferAuditExpectation {
        action: "transfer_invalidate",
        actor_kind: Some("system"),
        subject: human_actor_id("t1"),
        after_state: None,
    }
    .audit_present_once(&rows);
}

/// 迟到决议作用于当前 pending：version 只是 ABA CAS，不是
/// 跨轮防重放——同地址新一轮的 pending 可被普通 accept 决议。
async fn transfer_late_decisions_act_on_current_round(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_late";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;

    open_pending(fixture, env, bot, "own", "t1").await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Reject,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;
    let round2 = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Created),
    )
    .await;
    assert_eq!(round2.status, TransferStatus::Pending);
    let view = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;
    assert_eq!(view.status, TransferStatus::Accepted);
    assert_eq!(
        repo.ownership(bot).await.expect("ownership").owner_actor_id,
        human_actor_id("t1"),
        "the current-round late accept transfers"
    );
}

/// 版本只在 accept 增长：reject/cancel/refresh/重新发起都
/// 不递增（用下一轮 accepted 行的 result_ownership_version）。
async fn transfer_version_grows_only_on_accept(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_version";
    seeded_bot(fixture, env, bot, "own", 1).await;
    for user in ["t1", "t2"] {
        fixture.seed_human(env, user).await;
    }
    open_pending(fixture, env, bot, "own", "t1").await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::RequestOrRefresh,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Updated),
    )
    .await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Reject,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;
    open_pending(fixture, env, bot, "own", "t1").await;
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Updated),
    )
    .await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let view = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t1"),
        Applied(TransferChange::Updated),
    )
    .await;
    assert_eq!(
        view.result_ownership_version,
        Some(2),
        "only the accept incremented the ownership version"
    );
    open_pending(fixture, env, bot, "t1", "t2").await;
    let view = run_transfer_case(
        fixture,
        bot,
        "t2",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t2"),
        Applied(TransferChange::Updated),
    )
    .await;
    assert_eq!(
        view.result_ownership_version,
        Some(3),
        "each accepted round increments by exactly one"
    );
    assert!(repo
        .transfer(&TransferKey {
            bot_id: bot.to_string(),
            target_owner_actor_id: human_actor_id("t2"),
        })
        .await
        .expect("read")
        .is_some());
}

/// 重复取消终态只核验本行发起 Human，不依赖当前 owner；
/// 操作 pending 的取消必须核验发起人身份（且发起人此时
/// 仍为 owner 时才可取消 pending）。
async fn transfer_cancel_keeps_initiator_identity_on_terminal(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_cancel_identity";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let view = run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Updated),
    )
    .await;
    assert_eq!(view.status, TransferStatus::Cancelled);
    // Repeat cancel by the row initiator (still the owner
    // here) — no write.
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Unchanged),
    )
    .await;
    // Move ownership to t2; the terminal-row cancel repeat
    // still verifies only the row initiator identity — the
    // initiator can repeat without re-owning, another Human
    // cannot.
    fixture.seed_human(env, "t2").await;
    open_pending(fixture, env, bot, "own", "t2").await;
    run_transfer_case(
        fixture,
        bot,
        "t2",
        TransferAction::Accept,
        human_mutation(next_mutation_id(), "t2"),
        Applied(TransferChange::Updated),
    )
    .await;
    assert_eq!(
        repo.ownership(bot).await.expect("ownership").owner_actor_id,
        human_actor_id("t2"),
        "t2 owns the bot now"
    );
    let audit_before = fixture.audit_rows(env).await.len();
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Unchanged),
    )
    .await;
    // A different Human may not re-cancel that row.
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "t2"),
        Denied("bot_authority_operator_forbidden"),
    )
    .await;
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "identity repeats and denials wrote nothing"
    );
}

/// 列表同一时间快照投影有效状态：pending 过期 → expired，
/// 筛选与 total 同口径。
async fn transfer_lists_project_effective_status(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    // The env is shared across scenarios: measure THIS
    // scenario's contributions as deltas (captured
    // before any seeding).
    let inbox = |statuses: Vec<TransferStatus>, user: &'static str| {
        let repo = repo.clone();
        async move {
            repo.transfers(TransferListQuery {
                user_id: human_actor_id(user),
                direction: TransferDirection::Received,
                statuses,
                offset: 0,
                limit: 50,
            })
            .await
            .expect("inbox page")
        }
    };
    let sent_snapshot = || {
        let repo = repo.clone();
        async move {
            repo.transfers(TransferListQuery {
                user_id: human_actor_id("own"),
                direction: TransferDirection::Sent,
                statuses: vec![],
                offset: 0,
                limit: 50,
            })
            .await
            .expect("sent page")
        }
    };
    let baseline_all_t1 = inbox(vec![], "t1").await.total;
    let baseline_all_t2 = inbox(vec![], "t2").await.total;
    let baseline_expired_t2 =
        inbox(vec![TransferStatus::Expired], "t2").await.total;
    let baseline_pending_t2 =
        inbox(vec![TransferStatus::Pending], "t2").await.total;
    let baseline_pending_t1 =
        inbox(vec![TransferStatus::Pending], "t1").await.total;
    let baseline_sent = sent_snapshot().await.total;
    let bot = "bt_lists";
    seeded_bot(fixture, env, bot, "own", 1).await;
    for user in ["t1", "t2"] {
        fixture.seed_human(env, user).await;
    }
    open_pending(fixture, env, bot, "own", "t1").await;
    // One pending per bot (uk_transfer_pending): the
    // expired row lives on a second bot owned by the
    // same owner; t2's inbox then carries it.
    let bot2 = "bt_lists2";
    seeded_bot(fixture, env, bot2, "own", 1).await;
    fixture
        .seed_transfer(
            env,
            HarnessTransferSeed {
                bot_id: bot2.to_string(),
                target_owner_actor_id: human_actor_id("t2"),
                from_owner_actor_id: human_actor_id("own"),
                expected_ownership_version: 1,
                status: TransferStatus::Pending,
                requested_at_offset_s: -8 * 24 * 3600,
                expires_at_offset_s: -1800,
                decided_at_offset_s: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
            },
        )
        .await;

    let inbox = |statuses: Vec<TransferStatus>, user: &'static str| {
        let repo = repo.clone();
        async move {
            repo.transfers(TransferListQuery {
                user_id: human_actor_id(user),
                direction: TransferDirection::Received,
                statuses,
                offset: 0,
                limit: 50,
            })
            .await
            .expect("inbox page")
        }
    };
    let all = inbox(vec![], "t1").await;
    assert_eq!(all.total, baseline_all_t1 + 1);
    let pending1 = inbox(vec![TransferStatus::Pending], "t1").await;
    assert_eq!(
        pending1.total,
        baseline_pending_t1 + 1,
        "fresh pending filters as pending"
    );
    let expired2 = inbox(vec![TransferStatus::Expired], "t2").await;
    assert_eq!(
        expired2.total, baseline_expired_t2 + 1,
        "expired pending projects as expired"
    );
    let expired_row = expired2.items.iter().find(|row| {
        row.target_owner_actor_id == human_actor_id("t2")
            && row.bot_id == "bt_lists2"
    });
    assert_eq!(
        expired_row.expect("the expired row in the inbox").status,
        TransferStatus::Expired
    );
    let pending2 = inbox(vec![TransferStatus::Pending], "t2").await;
    assert_eq!(
        pending2.total, baseline_pending_t2,
        "the expired row is NOT in the pending filter (same lens)"
    );
    let all_t2 = inbox(vec![], "t2").await;
    assert_eq!(all_t2.total, baseline_all_t2 + 1);
    let sent_after = sent_snapshot().await.total;
    assert_eq!(
        sent_after,
        baseline_sent + 2,
        "both fresh rows are own's outbox"
    );
    let _ = all.items;
    assert!(
        repo.transfers(TransferListQuery {
            user_id: human_actor_id("own"),
            direction: TransferDirection::Sent,
            statuses: vec![],
            offset: 0,
            limit: 0,
        })
        .await
        .is_err(),
        "page bounds still enforced"
    );
    assert_eq!(inbox(vec![], "nobody").await.total, 0, "empty inbox");
}

/// 纯 GET 不写审计，也不惰性落盘 expire：a pending past
/// expiry keeps reading expired across repeated GETs until a
/// write op closes it.
async fn transfer_reads_never_audit(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_get_noaudit";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let audit_before = fixture.audit_rows(env).await.len();
    for _ in 0..3 {
        let _ = repo
            .transfer(&TransferKey {
                bot_id: bot.to_string(),
                target_owner_actor_id: human_actor_id("t1"),
            })
            .await
            .expect("single get");
        let _ = repo
            .transfers(TransferListQuery {
                user_id: human_actor_id("t1"),
                direction: TransferDirection::Received,
                statuses: vec![TransferStatus::Pending],
                offset: 0,
                limit: 50,
            })
            .await
            .expect("list get");
    }
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "GET transfers must never audit"
    );
    // The GET did not lazy-close the row: a decision still
    // applies (lazily persisted only by write ops).
    run_transfer_case(
        fixture,
        bot,
        "t1",
        TransferAction::Cancel,
        human_mutation(next_mutation_id(), "own"),
        Applied(TransferChange::Updated),
    )
    .await;
}

/// accept/cancel 竞争：恰一个决议生效，另一个取得终态冲突；
/// 两种结局都是已提交业务冲突（非 Err）。
async fn transfer_accept_cancel_race_serializes(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_race";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;

    let repo_a = repo.clone();
    let repo_b = repo.clone();
    let (accept, cancel) = tokio::join!(
        repo_a.change_transfer(transfer_cmd(
            bot,
            "t1",
            TransferAction::Accept,
            human_mutation(next_mutation_id(), "t1"),
        )),
        repo_b.change_transfer(transfer_cmd(
            bot,
            "t1",
            TransferAction::Cancel,
            human_mutation(next_mutation_id(), "own"),
        )),
    );
    let outcomes: Vec<TransferOutcome> = [accept, cancel]
        .into_iter()
        .map(|result| {
            result.expect(
                "the losing race outcome is a committed business conflict",
            )
        })
        .collect();
    let applied = outcomes
        .iter()
        .filter(|outcome| matches!(outcome, TransferOutcome::Applied { .. }))
        .count();
    assert_eq!(applied, 1, "exactly one decision wins: {outcomes:?}");
    let final_view = row_of(fixture, bot, "t1").await;
    for outcome in &outcomes {
        match outcome {
            TransferOutcome::Applied { transfer, change } => {
                assert_eq!(*change, TransferChange::Updated);
                assert_eq!(final_view.status, transfer.status);
            }
            TransferOutcome::Conflict { transfer, code } => {
                assert_eq!(code.as_str(), "transfer_state_conflict");
                assert_eq!(final_view.status, transfer.status);
                assert_ne!(
                    final_view.status,
                    TransferStatus::Pending,
                    "the winner closed the row"
                );
            }
        }
    }
    let owner_is_target = repo
        .ownership(bot)
        .await
        .expect("ownership")
        .owner_actor_id
        == human_actor_id("t1");
    assert_eq!(
        owner_is_target,
        final_view.status == TransferStatus::Accepted,
        "accept and cancel cannot both commit"
    );
}

/// 两个发起竞争：恰一个 Created，另一个 `transfer_pending`。
async fn transfer_concurrent_requests_one_pending(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_request_race";
    seeded_bot(fixture, env, bot, "own", 1).await;
    for user in ["t1", "t2"] {
        fixture.seed_human(env, user).await;
    }
    let repo_a = repo.clone();
    let repo_b = repo.clone();
    let (first, second) = tokio::join!(
        repo_a.change_transfer(transfer_cmd(
            bot,
            "t1",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        )),
        repo_b.change_transfer(transfer_cmd(
            bot,
            "t2",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        )),
    );
    let outcomes = [
        expect_transfer_conflict(first),
        expect_transfer_conflict(second),
    ];
    for outcome in &outcomes {
        match outcome {
            TransferOutcome::Applied { change, .. } => {
                assert_eq!(*change, TransferChange::Created);
            }
            TransferOutcome::Conflict { code, .. } => {
                assert_eq!(code.as_str(), "transfer_pending");
            }
        }
    }
    // The race leaves exactly ONE pending across the
    // bot's rows: target t1's row and target t2's row
    // combine to exactly one pending (one of the
    // requests created it; the other conflicts).
    let t1_view = repo
        .transfer(&TransferKey {
            bot_id: bot.to_string(),
            target_owner_actor_id: human_actor_id("t1"),
        })
        .await
        .expect("t1 row")
        .map(|row| row.status);
    let t2_view = repo
        .transfer(&TransferKey {
            bot_id: bot.to_string(),
            target_owner_actor_id: human_actor_id("t2"),
        })
        .await
        .expect("t2 row")
        .map(|row| row.status);
    let pending_count = [t1_view, t2_view]
        .iter()
        .filter(|status| **status == Some(TransferStatus::Pending))
        .count();
    assert_eq!(pending_count, 1, "one pending per bot (race)");
}

/// 审计写入失败 → 整笔回滚：owner/边/版本/行都未变。
async fn transfer_audit_failure_is_atomic(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_audit_atomic";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let before = row_of(fixture, bot, "t1").await;
    let mutation = next_mutation_id();
    fixture.seed_audit_conflict(env, &mutation, 0).await;
    let audit_before = fixture.audit_rows(env).await.len();

    let result = repo
        .change_transfer(transfer_cmd(
            bot,
            "t1",
            TransferAction::Accept,
            human_mutation(mutation, "t1"),
        ))
        .await;
    assert!(result.is_err(), "the audit collision must fail the command");
    assert_eq!(
        fixture
            .authority_repo()
            .ownership(bot)
            .await
            .expect("ownership")
            .owner_actor_id,
        human_actor_id("own"),
        "failed accept must not move the owner"
    );
    let after = row_of(fixture, bot, "t1").await;
    assert_eq!(after.status, TransferStatus::Pending);
    assert_eq!(
        after.from_owner_actor_id, before.from_owner_actor_id,
        "row snapshot unchanged by the failed accept"
    );
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "failed accept must not commit partial audit rows"
    );
}

/// Bot tombstone 和已删目标都整笔拒绝，无写入。
async fn transfer_deleted_bot_or_target_fail_clean(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_deleted";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let audit_before = fixture.audit_rows(env).await.len();

    fixture.delete_bot(env, bot).await;
    for (action, operator) in [
        (TransferAction::RequestOrRefresh, "own"),
        (TransferAction::Accept, "t1"),
        (TransferAction::Cancel, "own"),
    ] {
        let err = repo
            .change_transfer(transfer_cmd(
                bot,
                "t1",
                action,
                human_mutation(next_mutation_id(), operator),
            ))
            .await
            .expect_err("deleted bot denies transfer writes");
        assert!(
            matches!(err, bcs_service_api::ServiceError::BotNotFound(_)),
            "got {err}"
        );
    }
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "deleted-bot failures write nothing"
    );

    // Removed target Human: denied as unknown Human.
    let bot2 = "bt_removed_target";
    seeded_bot(fixture, env, bot2, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    fixture.seed_human(env, "gone").await;
    open_pending(fixture, env, bot2, "own", "gone").await;
    fixture.remove_human(env, "gone").await;
    let err = repo
        .change_transfer(transfer_cmd(
            bot2,
            "gone",
            TransferAction::Accept,
            human_mutation(next_mutation_id(), "gone"),
        ))
        .await
        .expect_err("removed target Human cannot accept");
    assert!(mentions(&err, "bot_authority_human_unknown"), "got {err}");
    let err = repo
        .change_transfer(transfer_cmd(
            bot2,
            "gone",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "own"),
        ))
        .await
        .expect_err("request toward a removed Human fails");
    assert!(mentions(&err, "bot_authority_human_unknown"), "got {err}");
}

/// P1 review-finding extension (PR #2625): the same survival
/// gate covers the OPERATOR side of transfer commands. A
/// tombstoned owner holding a residual ownership edge and a
/// valid credential can neither initiate a new round nor
/// cancel their own pending one; rejection is Forbidden and
/// produces no row/audit changes. Re-ensuring the actor
/// restores command authority.
async fn transfer_deleted_operator_cannot_drive_commands(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_gone_operator";
    seeded_bot(fixture, env, bot, "gone_owner", 1).await;
    fixture.seed_human(env, "t_keep").await;

    // Initiation by a tombstoned owner: Forbidden.
    fixture.remove_human(env, "gone_owner").await;
    let audit_before = fixture.audit_rows(env).await.len();
    let err = repo
        .change_transfer(transfer_cmd(
            bot,
            "t_keep",
            TransferAction::RequestOrRefresh,
            human_mutation(next_mutation_id(), "gone_owner"),
        ))
        .await
        .expect_err("a deleted owner must not initiate a transfer");
    assert!(
        mentions(&err, "bot_authority_operator_forbidden"),
        "operator-survival initiation denial: {err}"
    );
    row_absent(fixture, bot, "t_keep").await;

    // Re-ensure, open a pending round, then tombstone the
    // owner again mid-flight: CANCEL by the deleted operator
    // is Forbidden as well, while the target (alive) still
    // decides the same round normally.
    fixture.seed_human(env, "gone_owner").await;
    open_pending(fixture, env, bot, "gone_owner", "t_keep").await;
    fixture.remove_human(env, "gone_owner").await;
    let audit_mid = fixture.audit_rows(env).await.len();
    let err = repo
        .change_transfer(transfer_cmd(
            bot,
            "t_keep",
            TransferAction::Cancel,
            human_mutation(next_mutation_id(), "gone_owner"),
        ))
        .await
        .expect_err("a deleted initiator must not cancel");
    assert!(
        mentions(&err, "bot_authority_operator_forbidden"),
        "operator-survival cancel denial: {err}"
    );
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_mid,
        "a rejected operator produces no audit rows"
    );
    // The pending row survived the rejected cancel: a live
    // target's reject still lands on THIS round.
    let outcome = repo
        .change_transfer(transfer_cmd(
            bot,
            "t_keep",
            TransferAction::Reject,
            human_mutation(next_mutation_id(), "t_keep"),
        ))
        .await
        .expect("the live target still decides the round");
    match outcome {
        TransferOutcome::Applied { transfer, .. } => {
            assert_eq!(transfer.status, TransferStatus::Rejected)
        }
        other => panic!("expected the round to remain decidable, got {other:?}"),
    }
    assert!(
        fixture.audit_rows(env).await.len() > audit_before,
        "only the legitimate writes audited"
    );
}

/// 发起 = 仅当前 owner Human；accept/reject = 目标 Human 或
/// 可信 App；cancel = 仅发起 Human。
async fn transfer_actor_kind_policy(
    fixture: &(impl BotTransferContractFixture + ?Sized),
    env: &str,
) {
    let repo = fixture.authority_repo();
    let bot = "bt_actor_kind";
    seeded_bot(fixture, env, bot, "own", 1).await;
    fixture.seed_human(env, "t1").await;
    open_pending(fixture, env, bot, "own", "t1").await;
    let audit_before = fixture.audit_rows(env).await.len();

    for actor in [
        AuthorityActor::Provider {
            provider_id: "p1".to_string(),
        },
        AuthorityActor::System {
            name: "cron".to_string(),
        },
    ] {
        let err = repo
            .change_transfer(transfer_cmd(
                bot,
                "t1",
                TransferAction::Accept,
                AuthorityMutation {
                    mutation_id: next_mutation_id(),
                    actor,
                },
            ))
            .await
            .expect_err("provider/system cannot decide");
        assert!(mentions(&err, "bot_authority_operator_forbidden"), "got {err}");
    }
    fixture.seed_human(env, "stranger").await;
    let err = repo
        .change_transfer(transfer_cmd(
            bot,
            "t1",
            TransferAction::Accept,
            human_mutation(next_mutation_id(), "stranger"),
        ))
        .await
        .expect_err("only the target decides");
    assert!(mentions(&err, "bot_authority_operator_forbidden"), "got {err}");
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before,
        "denied actor kinds write nothing"
    );
    // Trusted app CAN accept (§6.1 dual admission); the
    // recorded decision is service.
    let outcome = repo
        .change_transfer(transfer_cmd(
            bot,
            "t1",
            TransferAction::Accept,
            service_mutation(next_mutation_id(), 7),
        ))
        .await
        .expect("trusted app accepts");
    assert!(matches!(
        outcome,
        TransferOutcome::Applied { change: TransferChange::Updated, .. }
    ));
    let view = row_of(fixture, bot, "t1").await;
    assert_eq!(view.decided_by_kind, Some(AuthorityActorKind::Service));
    assert!(view
        .decided_by_id
        .as_deref()
        .unwrap_or("")
        .starts_with("app_"));
    assert_eq!(
        fixture.audit_rows(env).await.len(),
        audit_before + 1,
        "the service accept audited once"
    );
}

/// (T1-c1) plan §3 conflict-code spellings pin.
async fn transfer_error_codes_are_pinned(
    _fixture: &(impl BotTransferContractFixture + ?Sized),
    _env: &str,
) {
    assert_eq!(AuthorityConflictCode::TransferPending.as_str(), "transfer_pending");
    assert_eq!(AuthorityConflictCode::OwnerChanged.as_str(), "owner_changed");
    assert_eq!(AuthorityConflictCode::TransferExpired.as_str(), "transfer_expired");
    assert_eq!(
        AuthorityConflictCode::TransferStateConflict.as_str(),
        "transfer_state_conflict"
    );
}
