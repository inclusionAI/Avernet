//! Shared deterministic SQLite/native-MySQL command interleavings. Every client
//! calls the application, plans against real SQL and executes the native plugin.
//! Notify gates hold no database lock; both plans exist before either commits.
use super::lifecycle::Driver;
#[path = "agent_invoke_race_db.rs"]
mod controlled;
use controlled::{ControlledDb, Pause};
use bcs_db_api::*;
use bcs_domain::edge_permission::{EdgeStatus, RequestStatus};
use bcs_edge_permission::agent_invoke::{AgentInvokeApplicationService, AgentInvokePolicy};
use bcs_edge_permission_store::agent_invoke::{DbAgentInvokeStore, DbAgentInvokeCommandStore};
use bcs_service_api::application::agent_invoke::{AgentInvocationService, AgentInvokeGrantManagementService};
use bcs_service_api::types::agent_invoke::*;
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
use std::num::NonZeroU64;
use std::sync::{Arc, atomic::Ordering};

#[derive(Clone, Copy, Debug)]
pub enum Quota { OutDegree, InDegree, PendingOut, PendingIn, PendingPair }
#[derive(Clone, Copy, Debug)]
pub enum Operation { Grant, Request, Approve, Reject, Cancel, Revoke }
#[derive(Clone, Copy, Debug)]
pub enum Case {
    ApproveCancel(bool), ApproveRevoke(bool), Quota(Quota, bool), OldKeyRegrant,
    UnknownRevoke, SameKeyUnknown, SameKeyPlans(bool), Rollback(Operation), Unknown(Operation),
}
pub fn cases() -> Vec<Case> {
    let mut cases = vec![Case::OldKeyRegrant, Case::UnknownRevoke, Case::SameKeyUnknown,
        Case::SameKeyPlans(false), Case::SameKeyPlans(true)];
    for first in [false, true] {
        cases.extend([Case::ApproveCancel(first), Case::ApproveRevoke(first)]);
        for quota in [Quota::OutDegree, Quota::InDegree, Quota::PendingOut, Quota::PendingIn, Quota::PendingPair] {
            cases.push(Case::Quota(quota, first));
        }
    }
    for op in [Operation::Grant, Operation::Request, Operation::Approve, Operation::Reject, Operation::Cancel, Operation::Revoke] {
        cases.extend([Case::Rollback(op), Case::Unknown(op)]);
    }
    cases
}
fn pair(from: &str, to: &str) -> AgentInvokePair {
    AgentInvokePair { from_bot_id: from.into(), to_bot_id: to.into() }
}
fn cmd(d: &Driver, actor: &str, key: &str, change: AgentInvokeChange) -> AgentInvokeCommand {
    AgentInvokeCommand { principal: AgentInvokePrincipal::Human(actor.into()), authenticated_scope: d.scope.clone(),
        pair: pair("race-a", "race-b"), change, idempotency_key: key.into(), reason: "deterministic SQL concurrency".into() }
}
fn application(d: &Driver, db: Arc<dyn DbPlugin>) -> AgentInvokeApplicationService {
    let one = std::num::NonZeroU32::MIN;
    let store = Arc::new(DbAgentInvokeStore::new(db, d.flavor, d.scope.clone(), AgentInvokeLimits {
        max_out_degree: one, max_in_degree: one, max_pending_out: one, max_pending_in: one,
    }, NonZeroU64::new(60_000).unwrap()).unwrap());
    let policy = Arc::new(AgentInvokePolicy);
    let commands = Arc::new(DbAgentInvokeCommandStore::new(store.clone(), policy.clone()));
    AgentInvokeApplicationService::new(d.scope.clone(), policy, store, commands).unwrap()
}
async fn setup(d: &Driver) {
    for bot in ["race-a", "race-b", "race-c", "race-d"] { d.register(bot).await; }
    for bot in ["race-a", "race-d"] { d.role("human_race_source", bot, Some(AgentInvokeRole::Owner)).await; }
    for bot in ["race-b", "race-c"] { d.role("human_race_target", bot, Some(AgentInvokeRole::Owner)).await; }
}
async fn request(d: &Driver) -> AgentInvokeRequestView {
    d.service.change(cmd(d, "human_race_source", "seed-request", AgentInvokeChange::Request)).await.unwrap().original_request.unwrap()
}
async fn grant(d: &Driver) -> AgentInvokeGrantView {
    d.service.change(cmd(d, "human_race_target", "seed-grant", AgentInvokeChange::Grant { expected_version: 0 }))
        .await.unwrap().original_grant.unwrap()
}
fn approve(d: &Driver, r: &AgentInvokeRequestView) -> AgentInvokeCommand {
    cmd(d, "human_race_target", "race-approve", AgentInvokeChange::Approve {
        request_id: r.request_id.clone(), expected_version: r.version })
}
fn revoke(d: &Driver, g: &AgentInvokeGrantView) -> AgentInvokeCommand {
    cmd(d, "human_race_source", "race-revoke", AgentInvokeChange::Revoke { edge_id: g.edge_id, expected_version: g.version })
}
fn invocation(d: &Driver) -> AgentInvocationInput {
    AgentInvocationInput { authentication: AgentInvocationAuthentication::TrustedInProcess,
        principal: AgentInvokePrincipal::Agent("race-a".into()), authenticated_scope: d.scope.clone(),
        pair: pair("race-a", "race-b"), originator: "race-a".into(), operation: AgentInvokeOperation::DirectInvocation }
}
async fn scalar(d: &Driver, sql: &str) -> u64 {
    let rows = d.db.query(DbStatement::with_params(sql, vec![d.scope.env.as_str().into()])).await.unwrap();
    db_get_column(&rows[0], "n").unwrap()
}
async fn receipts(d: &Driver) -> u64 {
    scalar(d, "SELECT COUNT(*) AS n FROM agent_invoke_receipts WHERE scope_id=(SELECT id FROM agent_invoke_scopes WHERE env=?)").await
}
async fn pending(d: &Driver) -> u64 {
    scalar(d, "SELECT COUNT(*) AS n FROM agent_invoke_pending_pairs WHERE scope_id=(SELECT id FROM agent_invoke_scopes WHERE env=?)").await
}
async fn active(d: &Driver) -> u64 {
    scalar(d, "SELECT COUNT(*) AS n FROM edge_grants WHERE env=? AND grant_kind='agent_invoke' AND status='approved'").await
}
// Whole durable rows, not just counts: a rolled-back/losing plan must not
// alter versions, pending metadata, original receipts or historical audit JSON.
async fn durable_rows(d: &Driver) -> Vec<Vec<DbRow>> {
    let mut rows = Vec::new();
    for (table, order) in [("agent_invoke_grants", "edge_id"), ("agent_invoke_requests", "request_id"),
        ("agent_invoke_pending_pairs", "request_id"), ("agent_invoke_receipts", "action_id"),
        ("agent_invoke_audit", "id"), ("agent_invoke_facts", "bot_id"),
        ("agent_invoke_governance", "principal_hash,from_id,to_id")] {
        rows.push(d.db.query(DbStatement::with_params(format!("SELECT * FROM {table} WHERE scope_id=(SELECT id FROM agent_invoke_scopes WHERE env=?) ORDER BY {order}"),
            vec![d.scope.env.as_str().into()])).await.unwrap());
    }
    for table in ["agent_invoke_scopes", "edge_grants", "permission_requests", "bcs_bots", "bot_authority_changes"] {
        rows.push(d.db.query(DbStatement::with_params(format!("SELECT * FROM {table} WHERE env=? ORDER BY id"),
            vec![d.scope.env.as_str().into()])).await.unwrap());
    }
    rows
}
async fn race(d: &Driver, peer: Arc<dyn DbPlugin>, winner: AgentInvokeCommand, loser: AgentInvokeCommand) -> AgentInvokeReceipt {
    let first = Arc::new(ControlledDb::new(d.db.clone(), Pause::BeforeTransaction, false, false));
    let second = Arc::new(ControlledDb::new(peer, Pause::BeforeTransaction, false, false));
    let wa = application(d, first.clone());
    let la = application(d, second.clone());
    let winning = tokio::spawn(async move { wa.change(winner).await });
    let losing = tokio::spawn(async move { la.change(loser).await });
    tokio::join!(first.wait(), second.wait());
    first.resume.notify_one();
    let receipt = winning.await.unwrap().expect("chosen first writer really committed");
    assert_eq!(first.mutations.load(Ordering::SeqCst), 1);
    let committed_rows = durable_rows(d).await;
    second.resume.notify_one();
    assert_eq!(losing.await.unwrap(), Err(AgentInvokeError::Conflict), "stale fully-built SQL plan cannot commit");
    assert_eq!(second.mutations.load(Ordering::SeqCst), 1, "no hidden retry");
    assert_eq!(second.rollbacks.load(Ordering::SeqCst), 1, "native plugin acknowledged loser rollback");
    assert_eq!(durable_rows(d).await, committed_rows, "loser leaves every durable row unchanged");
    receipt
}
async fn approve_cancel(d: &Driver, peer: Arc<dyn DbPlugin>, approve_first: bool) {
    let r = request(d).await;
    let a = approve(d, &r);
    let c = cmd(d, "human_race_source", "race-cancel", AgentInvokeChange::Cancel { request_id: r.request_id, expected_version: r.version });
    let (winner, loser) = if approve_first { (a, c) } else { (c, a) };
    let failed_key = loser.clone();
    let receipt = race(d, peer.clone(), winner, loser).await;
    let terminal = receipt.original_request.unwrap();
    assert_eq!(terminal.status, if approve_first { RequestStatus::Approved } else { RequestStatus::Cancelled });
    assert_eq!(terminal.version.get(), 2);
    assert_eq!(active(d).await, u64::from(approve_first));
    assert_eq!(pending(d).await, 0);
    assert_eq!(receipts(d).await, 2);
    assert_eq!(d.audit_count().await, 2);
    assert_eq!(application(d, peer).change(failed_key).await, Err(AgentInvokeError::Conflict));
    assert_eq!(d.service.authorize_invocation(invocation(d)).await.is_ok(), approve_first);
}
async fn approve_revoke(d: &Driver, peer: Arc<dyn DbPlugin>, approve_first: bool) {
    let g = grant(d).await;
    let r = request(d).await;
    let a = approve(d, &r);
    let rev = revoke(d, &g);
    let (winner, loser) = if approve_first { (a, rev) } else { (rev, a) };
    let failed_key = loser.clone();
    let receipt = race(d, peer.clone(), winner, loser).await;
    let current = receipt.current_grant.unwrap();
    assert_eq!(current.edge_id, g.edge_id);
    assert_eq!(current.version.get(), g.version.get() + 1);
    assert_eq!(current.status, if approve_first { EdgeStatus::Approved } else { EdgeStatus::Revoked });
    assert_eq!(pending(d).await, u64::from(!approve_first));
    assert_eq!(receipts(d).await, 3);
    assert_eq!(d.audit_count().await, 3);
    assert_eq!(application(d, peer).change(failed_key).await, Err(AgentInvokeError::Conflict));
    assert_eq!(d.service.authorize_invocation(invocation(d)).await.is_ok(), approve_first);
    if !approve_first {
        // Revocation invalidates this request's captured edge version forever.
        let regrant = cmd(d, "human_race_target", "new-approval", AgentInvokeChange::Grant { expected_version: current.version.get() });
        d.service.change(regrant).await.unwrap();
        assert_eq!(d.service.change(approve(d, &r)).await, Err(AgentInvokeError::Conflict));
    }
}
async fn quota(d: &Driver, peer: Arc<dyn DbPlugin>, kind: Quota, reverse: bool) {
    let is_request = matches!(kind, Quota::PendingOut | Quota::PendingIn | Quota::PendingPair);
    let actor = if is_request { "human_race_source" } else { "human_race_target" };
    let change = if is_request { AgentInvokeChange::Request } else { AgentInvokeChange::Grant { expected_version: 0 } };
    let a = cmd(d, actor, "quota-a", change.clone());
    let mut b = cmd(d, actor, "quota-b", change);
    match kind {
        Quota::OutDegree | Quota::PendingOut => b.pair.to_bot_id = "race-c".into(),
        Quota::InDegree | Quota::PendingIn => b.pair.from_bot_id = "race-d".into(),
        Quota::PendingPair => (),
    }
    let (winner, loser) = if reverse { (b, a) } else { (a, b) };
    let failed = loser.clone();
    let receipt = race(d, peer.clone(), winner.clone(), loser).await;
    assert_eq!(receipts(d).await, 1);
    assert_eq!(d.audit_count().await, 1);
    assert_eq!(active(d).await, u64::from(!is_request));
    assert_eq!(pending(d).await, u64::from(is_request));
    let fresh = application(d, peer);
    assert_eq!(fresh.change(failed.clone()).await, Err(if matches!(kind, Quota::PendingPair) {
        AgentInvokeError::Conflict
    } else { AgentInvokeError::LimitExceeded }));
    // The losing key was not consumed. A real cancel/revoke frees capacity,
    // then the SAME failed key can commit with exactly one new receipt/audit.
    let mut release = winner;
    release.idempotency_key = "free-capacity".into();
    release.principal = AgentInvokePrincipal::Human("human_race_source".into());
    release.change = if is_request {
        let r = receipt.current_request.unwrap();
        AgentInvokeChange::Cancel { request_id: r.request_id, expected_version: r.version }
    } else {
        let g = receipt.current_grant.unwrap();
        AgentInvokeChange::Revoke { edge_id: g.edge_id, expected_version: g.version }
    };
    // For inbound competition source owns both physical sources.
    fresh.change(release).await.unwrap();
    let granted = fresh.change(failed).await.unwrap();
    assert!(!granted.replayed);
    assert_eq!(receipts(d).await, 3);
    assert_eq!(d.audit_count().await, 3);
    assert_eq!(active(d).await, u64::from(!is_request));
    assert_eq!(pending(d).await, u64::from(is_request));
}
async fn old_key_regrant(d: &Driver, peer: Arc<dyn DbPlugin>) {
    let first = cmd(d, "human_race_target", "old-approval", AgentInvokeChange::Grant { expected_version: 0 });
    let original = d.service.change(first.clone()).await.unwrap();
    let g = original.original_grant.clone().unwrap();
    let revoked = d.service.change(revoke(d, &g)).await.unwrap().current_grant.unwrap();
    let new = cmd(d, "human_race_target", "fresh-approval", AgentInvokeChange::Grant { expected_version: revoked.version.get() });
    let gate = Arc::new(ControlledDb::new(peer, Pause::BeforeTransaction, false, false));
    let service = application(d, gate.clone());
    let task = tokio::spawn(async move { service.change(new).await });
    gate.wait().await;
    let replay = d.service.change(first.clone()).await.unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.action_id, original.action_id);
    assert_eq!(replay.original_grant, original.original_grant);
    assert_eq!(replay.current_grant, Some(revoked));
    assert_eq!(d.service.authorize_invocation(invocation(d)).await, Err(AgentInvokeError::Forbidden));
    assert_eq!(receipts(d).await, 2);
    gate.resume.notify_one();
    let new = task.await.unwrap().unwrap().current_grant.unwrap();
    let replay = d.service.change(first).await.unwrap();
    assert_eq!(replay.current_grant, Some(new.clone()));
    assert_eq!(replay.original_grant, original.original_grant);
    assert_eq!(new.edge_id, g.edge_id);
    assert!(new.version > g.version);
    assert_eq!(gate.mutations.load(Ordering::SeqCst), 1);
    assert_eq!(receipts(d).await, 3);
    assert_eq!(d.audit_count().await, 3);
}
async fn unknown_revoke(d: &Driver, peer: Arc<dyn DbPlugin>, same_key: bool) {
    let command = cmd(d, "human_race_target", "lost-result", AgentInvokeChange::Grant { expected_version: 0 });
    let gate = Arc::new(ControlledDb::new(d.db.clone(), Pause::AfterCommit, true, false));
    let service = application(d, gate.clone());
    let c = command.clone();
    let task = tokio::spawn(async move { service.change(c).await });
    gate.wait().await; // Native COMMIT returned; only the caller's ack is paused.
    assert_eq!(receipts(d).await, 1);
    let second = application(d, peer);
    let recovered = second.change(command.clone()).await.unwrap();
    assert!(recovered.replayed);
    let approved = recovered.original_grant.clone().unwrap();
    let mut wrong = command.clone(); wrong.reason.push_str(" different payload");
    assert_eq!(second.change(wrong).await, Err(AgentInvokeError::Conflict));
    let current = if same_key { approved.clone() } else {
        second.change(revoke(d, &approved)).await.unwrap().current_grant.unwrap()
    };
    let committed_rows = durable_rows(d).await;
    gate.resume.notify_one();
    assert_eq!(task.await.unwrap(), Err(AgentInvokeError::CommitUnknown));
    let replay = application(d, gate.clone()).change(command).await.unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.action_id, recovered.action_id);
    assert_eq!(replay.original_grant, recovered.original_grant);
    assert_eq!(replay.current_grant, Some(current));
    assert_eq!(gate.mutations.load(Ordering::SeqCst), 1, "recovery cannot execute another mutation");
    let effects = if same_key { 1 } else { 2 };
    assert_eq!(receipts(d).await, effects);
    assert_eq!(d.audit_count().await as u64, effects);
    assert_eq!(d.service.authorize_invocation(invocation(d)).await.is_ok(), same_key);
    assert_eq!(durable_rows(d).await, committed_rows);
}
async fn same_key_plans(d: &Driver, peer: Arc<dyn DbPlugin>, different_payload: bool) {
    let command = cmd(d, "human_race_target", "same-key-plans", AgentInvokeChange::Grant { expected_version: 0 });
    let mut losing = command.clone();
    if different_payload { losing.reason.push_str(" different payload"); }
    let receipt = race(d, peer.clone(), command.clone(), losing.clone()).await;
    let committed = durable_rows(d).await;
    let replay = application(d, peer).change(losing).await;
    if different_payload { assert_eq!(replay, Err(AgentInvokeError::Conflict)); }
    else {
        let replay = replay.unwrap();
        assert!(replay.replayed);
        assert_eq!(replay.action_id, receipt.action_id);
        assert_eq!(replay.original_grant, receipt.original_grant);
    }
    assert_eq!(receipts(d).await, 1);
    assert_eq!(d.audit_count().await, 1);
    assert_eq!(durable_rows(d).await, committed);
}
async fn operation(d: &Driver, op: Operation) -> AgentInvokeCommand {
    match op {
        Operation::Grant => cmd(d, "human_race_target", "operation-grant", AgentInvokeChange::Grant { expected_version: 0 }),
        Operation::Request => cmd(d, "human_race_source", "operation-request", AgentInvokeChange::Request),
        Operation::Revoke => { let g = grant(d).await; revoke(d, &g) },
        Operation::Approve | Operation::Reject | Operation::Cancel => {
            let r = request(d).await;
            match op {
                Operation::Approve => approve(d, &r),
                Operation::Reject => cmd(d, "human_race_target", "operation-reject", AgentInvokeChange::Reject { request_id: r.request_id, expected_version: r.version }),
                _ => cmd(d, "human_race_source", "operation-cancel", AgentInvokeChange::Cancel { request_id: r.request_id, expected_version: r.version }),
            }
        }
    }
}
async fn outcome(d: &Driver, peer: Arc<dyn DbPlugin>, op: Operation, lost: bool) {
    let command = operation(d, op).await;
    let before = (d.store.scope_state().await.unwrap(), receipts(d).await, d.audit_count().await, active(d).await, pending(d).await);
    let before_rows = durable_rows(d).await;
    let gate = Arc::new(ControlledDb::new(d.db.clone(), Pause::None, lost, !lost));
    let result = application(d, gate.clone()).change(command.clone()).await;
    assert_eq!(result, Err(if lost { AgentInvokeError::CommitUnknown } else { AgentInvokeError::Unavailable }));
    assert_eq!(gate.mutations.load(Ordering::SeqCst), 1);
    if lost {
        assert_eq!(receipts(d).await, before.1 + 1);
        assert_eq!(d.audit_count().await, before.2 + 1);
        let committed = durable_rows(d).await;
        let recovered = application(d, peer).change(command.clone()).await.unwrap();
        assert!(recovered.replayed);
        let again = application(d, gate.clone()).change(command).await.unwrap();
        assert_eq!(again, recovered);
        assert_eq!(gate.mutations.load(Ordering::SeqCst), 1);
        assert_eq!(gate.rollbacks.load(Ordering::SeqCst), 0);
        assert_eq!(durable_rows(d).await, committed);
    } else {
        assert_eq!(gate.rollbacks.load(Ordering::SeqCst), 1, "native audit SQL error acknowledged rollback");
        assert_eq!((d.store.scope_state().await.unwrap(), receipts(d).await, d.audit_count().await, active(d).await, pending(d).await), before);
        assert_eq!(durable_rows(d).await, before_rows);
        let committed = application(d, peer).change(command).await.unwrap();
        assert!(!committed.replayed, "rollback must not reserve the key");
        assert_eq!(receipts(d).await, before.1 + 1);
        assert_eq!(d.audit_count().await, before.2 + 1);
    }
}
pub async fn run_case(db: Arc<dyn DbPlugin>, peer: Arc<dyn DbPlugin>, flavor: DbSqlFlavor, env: &str, case: Case) {
    let d = Driver::new_on(db, flavor, env).await;
    setup(&d).await;
    match case {
        Case::ApproveCancel(first) => approve_cancel(&d, peer, first).await,
        Case::ApproveRevoke(first) => approve_revoke(&d, peer, first).await,
        Case::Quota(kind, reverse) => quota(&d, peer, kind, reverse).await,
        Case::OldKeyRegrant => old_key_regrant(&d, peer).await,
        Case::UnknownRevoke => unknown_revoke(&d, peer, false).await,
        Case::SameKeyUnknown => unknown_revoke(&d, peer, true).await,
        Case::SameKeyPlans(different) => same_key_plans(&d, peer, different).await,
        Case::Rollback(op) => outcome(&d, peer, op, false).await,
        Case::Unknown(op) => outcome(&d, peer, op, true).await,
    }
}
