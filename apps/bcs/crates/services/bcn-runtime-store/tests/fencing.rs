use std::sync::Arc;
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::OwnershipPort;
use bcn_runtime_storage_api::ServerClock;
use bcn_storage_memory::MemoryAtomicStore;
use bcn_runtime_store::RuntimeStore;
struct Clock;
impl ServerClock for Clock { fn now_ms(&self) -> u64 { 1000 } }
#[tokio::test]
async fn owner_transfer_compares_incarnation_and_never_resets_epoch() {
    let atomic = Arc::new(MemoryAtomicStore::new(RuntimeProfile::Test, Arc::new(Clock)).unwrap());
    let repo = RuntimeStore::new(atomic.clone());
    let scope = Scope::new("test", "tenant", "runtime");
    let shard = ShardId { scope: scope.clone(), number: 7 };
    let a = NodeIdentity { scope: scope.clone(), node_id: "worker".into(), incarnation: "a".into() };
    let b = NodeIdentity { incarnation: "b".into(), ..a.clone() };
    let first = repo.claim_shard(shard.clone(), a.clone(), None).await.unwrap();
    assert_eq!(first.epoch, 1);
    let second = repo.transfer_shard(first.clone(), b).await.unwrap();
    assert_eq!(second.epoch, 2);
    assert_eq!(repo.transfer_shard(first, a.clone()).await, Err(RuntimeError::OwnershipLost));
    let restarted = RuntimeStore::new(atomic);
    assert_eq!(restarted.read_owner(&shard).await.unwrap(), Some(second.clone()));
    assert_eq!(restarted.claim_shard(shard, a.clone(), None).await, Err(RuntimeError::OwnershipLost));
    assert_eq!(restarted.transfer_shard(second, a).await.unwrap().epoch, 3);
}

mod common;
use bcn_runtime_api::{core::SessionRunner, port::repo::{AdmissionStore, SessionStore, InboxPort}};
use bcn_session_runner::DirectA2aRunner;
#[tokio::test]
async fn checkpoint_input_and_outbox_commit_atomically_under_current_owner() {
 let (repo, atomic, req) = common::fixture();
 let cp = common::activated(&repo, &req).await;
 let next = repo.transfer_shard(cp.fence.clone(), common::node("two")).await.unwrap();
 let transition = DirectA2aRunner.reduce(&cp.state, &req.input).unwrap();
 let commit = SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence.clone(), expected_revision: cp.revision, consumed_input: 1, transition };
 assert!(matches!(repo.commit(commit.clone()).await, Err(RuntimeError::OwnershipLost)));
 assert_eq!(repo.load(&req.spec.session_id).await.unwrap().unwrap().consumed_input, 0);
 assert!(matches!(repo.activate(req.spec.clone(), cp.state.clone(), cp.fence).await, Err(RuntimeError::OwnershipLost)));
 let adopted = repo.activate(req.spec.clone(), cp.state, next.clone()).await.unwrap();
 let done = repo.commit(SessionCommit { fence: next.clone(), expected_revision: adopted.revision, ..commit }).await.unwrap();
 assert_eq!(done.consumed_input, 1);
 assert_eq!(done.pending_effects.len(), 1);
 assert!(!done.pending_events.is_empty());
 let restarted = RuntimeStore::new(atomic);
 assert_eq!(restarted.load(&req.spec.session_id).await.unwrap().unwrap().pending_effects, done.pending_effects);
 assert!(restarted.repair_pending(&req.caller.scope, None, 10).await.unwrap().items.is_empty());
 restarted.advance(&req.spec.session_id, &next, 1).await.unwrap();
 assert!(restarted.read_batch(&req.spec.session_id, 0, 10).await.unwrap().is_empty());
 assert!(restarted.advance(&req.spec.session_id, &next, 2).await.is_err());
}
#[tokio::test]
async fn deactivate_keeps_unconfirmed_admissions_and_removes_only_idle_wake() {
 use bcn_runtime_api::port::repo::CommandBus;
 let (repo, _, req) = common::fixture(); let cp = common::activated(&repo, &req).await;
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 1, transition: Transition { next_state: cp.state, effects: vec![], events: vec![], timer_changes: vec![] } }).await.unwrap();
 assert!(repo.deactivate(&req.spec.session_id, &cp.fence, cp.revision).await.is_err());
 let intent = ResultIntent { result_id: ResultId::new(common::scope(), "finished"), task_id: req.task.task_id.clone(), node_id: None, attempt: 0, status: TaskStatus::Cancelled, output: vec![], error: None };
 let effect = EffectIntent::SaveResult { intent: intent.clone() };
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 1, transition: Transition { next_state: cp.state, effects: vec![effect.clone()], events: vec![], timer_changes: vec![] } }).await.unwrap();
 repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: effect_command_id(&req.spec.session_id, &effect).unwrap() }).await.unwrap();
 let pos = repo.append_and_wake(&req.spec.session_id, SessionInput { event_id: EventId::new(common::scope(), "saved"), at_ms: 1000, kind: SessionInputKind::ResultSaved { result: SavedResult { intent, saved_at_ms: 1000 } } }).await.unwrap();
 let cp = repo.load(&req.spec.session_id).await.unwrap().unwrap();
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: pos, transition: Transition { next_state: cp.state, effects: vec![], events: vec![], timer_changes: vec![] } }).await.unwrap();
 repo.deactivate(&req.spec.session_id, &cp.fence, cp.revision).await.unwrap();
 assert!(repo.scan_active(&cp.fence.shard, None, 10).await.unwrap().items.is_empty());
 repo.append_and_wake(&req.spec.session_id, SessionInput { event_id: EventId::new(common::scope(), "cancel-again"), at_ms: 1001, kind: SessionInputKind::Cancel { command: CancelCommand { task_id: req.task.task_id, operation_id: OperationId::new(common::scope(), "cancel") } } }).await.unwrap();
 assert_eq!(repo.scan_active(&cp.fence.shard, None, 10).await.unwrap().items.len(), 1);
}
