mod common;
#[path = "common/observed.rs"] mod observed;
use common::*;
use observed::*;
use std::sync::atomic::Ordering;
use bcn_runtime_store::RuntimeStore;
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::*;
use bcn_runtime_storage_api::NodeLivenessPort;
#[tokio::test]
async fn failed_admission_has_no_partial_receipt_and_index_queries_are_bounded() {
 let (_, atomic, req) = fixture(); let observed = Observed::new(atomic); let repo = RuntimeStore::new(observed.clone());
 observed.fail_write.store(true, Ordering::SeqCst);
 assert!(repo.admit(req.clone()).await.is_err());
 assert!(repo.get_receipt(&req.admission_id).await.unwrap().is_none());
 assert!(repo.read_snapshot(&req.task.task_id).await.unwrap().is_none());
 assert!(SessionStore::scan_active(&repo, &session_shard(&req.spec.session_id), None, 1).await.unwrap().items.is_empty());
 observed.reset(); repo.admit(req.clone()).await.unwrap();
 { let count = observed.counts.lock().unwrap(); assert_eq!(count.reads, vec![3,2]); assert_eq!(count.writes, vec![(5,13)]); assert!(count.scans.is_empty()); }
 observed.reset();
 let tasks = TaskQueryStore::list_tasks(&repo, &req.caller, TaskQuery { target: None, session_id: None, cursor: None, limit: 100 }).await.unwrap();
 assert_eq!(tasks.items.len(), 1);
 { let count = observed.counts.lock().unwrap(); assert_eq!(count.scans, vec![100]); assert_eq!(count.reads, vec![1]); assert!(count.writes.is_empty()); }
 assert!(TaskQueryStore::list_tasks(&repo, &req.caller, TaskQuery { target: None, session_id: None, cursor: None, limit: 101 }).await.is_err());
}
#[tokio::test]
async fn failed_membership_reads_are_unknown_and_do_not_transfer_ownership() {
 let (_, atomic, _) = fixture(); let observed = Observed::new(atomic); let repo = RuntimeStore::new(observed.clone());
 let member = node("one"); repo.register(member.clone(), 3000).await.unwrap();
 observed.fail_read.store(true, Ordering::SeqCst);
 assert_eq!(repo.inspect_batch(&[member]).await.unwrap()[0].liveness, Liveness::Unknown);
}
#[tokio::test]
async fn failed_reset_transaction_preserves_snapshot_and_restart_can_publish_once() {
 let (_, atomic, req) = fixture(); let observed = Observed::new(atomic.clone()); let repo = RuntimeStore::new(observed.clone());
 let cp = submitted(&repo, &req).await;
 let id = effect_command_id(&req.spec.session_id, &cp.pending_effects[0]).unwrap();
 repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id.clone() }).await.unwrap();
 let dispatch = repo.claim(&id, node("connector")).await.unwrap(); let dispatch = repo.mark_may_have_sent(&dispatch.fence, dispatch.revision).await.unwrap();
 let projection = ConnectorProjection { task_id: req.task.task_id.clone(), output: vec![MessagePart::Text { text: "snapshot".into() }], expected_cursor: EventCursor { epoch: 0, sequence: 0 } };
 let event = EventId::new(scope(), "reset");
 observed.fail_write.store(true, Ordering::SeqCst);
 assert!(repo.publish_reset(&dispatch.fence, projection.clone(), event.clone()).await.is_err());
 assert_eq!(repo.read_snapshot(&req.task.task_id).await.unwrap().unwrap().cursor, EventCursor { epoch: 0, sequence: 0 });
 let restart = RuntimeStore::new(atomic);
 let cursor = restart.publish_reset(&dispatch.fence, projection.clone(), event.clone()).await.unwrap();
 assert_eq!(restart.publish_reset(&dispatch.fence, projection, event).await.unwrap(), cursor);
 assert_eq!(restart.read_after(&req.task.task_id, Some(EventCursor { epoch: 1, sequence: 0 }), 100).await.unwrap().events.len(), 1);
}
#[tokio::test]
async fn maximum_task_page_uses_one_batched_read_and_preserves_continuation() {
 // Exactly one more than the contract page limit; a boundary fixture, not a load test.
 let (_, atomic, template) = fixture(); let observed = Observed::new(atomic); let repo = RuntimeStore::new(observed.clone());
 for number in 0..101 {
  let value = format!("item-{number}"); let mut req = template.clone();
  req.admission_id.value = value.clone(); req.spec.session_id.value = value.clone();
  req.task.task_id.value = value.clone(); req.task.session_id = req.spec.session_id.clone(); req.input.event_id.value = value;
  if let SessionInputKind::Submit { task } = &mut req.input.kind { task.task_id = req.task.task_id.clone(); task.message.context_id = Some(req.spec.session_id.clone()); }
  repo.admit(req).await.unwrap();
 }
 observed.reset();
 let first = TaskQueryStore::list_tasks(&repo, &template.caller, TaskQuery { target: None, session_id: None, cursor: None, limit: 100 }).await.unwrap();
 assert_eq!(first.items.len(), 100); assert!(first.next_cursor.is_some());
 { let counts = observed.counts.lock().unwrap(); assert_eq!(counts.scans, vec![100]); assert_eq!(counts.reads, vec![100]); }
 let last = TaskQueryStore::list_tasks(&repo, &template.caller, TaskQuery { target: None, session_id: None, cursor: first.next_cursor, limit: 100 }).await.unwrap();
 assert_eq!(last.items.len(), 1); assert!(last.next_cursor.is_none());
 assert!(!first.items.iter().any(|t| t.task_id == last.items[0].task_id));
}
