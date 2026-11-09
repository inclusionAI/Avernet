mod common;
use common::*;
use bcn_runtime_api::port::repo::*;
use bcn_runtime_model::*;
#[tokio::test]
async fn admission_is_idempotent_readable_authorized_and_discoverable_before_activation() {
 let (repo, _, req) = fixture();
 let receipt = repo.admit(req.clone()).await.unwrap();
 assert_eq!(receipt.input_position, 1);
 assert_eq!(repo.admit(req.clone()).await.unwrap(), receipt);
 assert_eq!(repo.get_receipt(&req.admission_id).await.unwrap(), Some(receipt));
 assert_eq!(repo.get_task(&req.caller, &req.task.task_id).await.unwrap().status, TaskStatus::Submitted);
 assert_eq!(repo.read_batch(&req.spec.session_id, 0, 100).await.unwrap().len(), 1);
 assert_eq!(SessionStore::scan_active(&repo, &session_shard(&req.spec.session_id), None, 1).await.unwrap().items, vec![req.spec.session_id.clone()]);
 assert!(repo.load_spec(&req.spec.session_id).await.unwrap().is_some());
 let mut conflicting = req.clone(); conflicting.request_digest = "other".into();
 assert!(matches!(repo.admit(conflicting).await, Err(RuntimeError::Conflict(_))));
 let mut stranger = req.caller.clone(); stranger.principal = "bob".into();
 assert_eq!(repo.get_task(&stranger, &req.task.task_id).await, Err(RuntimeError::AccessDenied));
 let query = TaskQuery { target: Some(req.task.target.clone()), session_id: None, cursor: None, limit: 1 };
 assert_eq!(TaskQueryStore::list_tasks(&repo, &req.caller, query.clone()).await.unwrap().items.len(), 1);
 assert!(TaskQueryStore::list_tasks(&repo, &stranger, query).await.unwrap().items.is_empty());
}
#[tokio::test]
async fn admission_capacity_and_cross_scope_reject_without_partial_receipt() {
 let (repo, _, mut req) = fixture(); req.spec.policy.max_pending_tasks = 1;
 repo.admit(req.clone()).await.unwrap();
 let mut next = req.clone(); next.admission_id.value = "b".into(); next.task.task_id.value = "b".into(); next.input.event_id.value = "b".into();
 if let SessionInputKind::Submit { task } = &mut next.input.kind { task.task_id = next.task.task_id.clone(); }
 assert_eq!(repo.admit(next.clone()).await.unwrap_err(), RuntimeError::CapacityExceeded);
 assert!(repo.get_receipt(&next.admission_id).await.unwrap().is_none());
 next.caller.scope.tenant = "other".into();
 assert!(matches!(repo.admit(next).await, Err(RuntimeError::InvalidInput(_))));
}
#[tokio::test]
async fn capacity_is_released_only_after_saved_result_input_is_checkpointed() {
 let (repo, _, mut req) = fixture(); req.spec.policy.max_pending_tasks = 1;
 let cp = activated(&repo, &req).await;
 let intent = ResultIntent { result_id: ResultId::new(scope(), "cancel-result"), task_id: req.task.task_id.clone(), node_id: None, attempt: 0, status: TaskStatus::Cancelled, output: vec![], error: None };
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 1, transition: Transition { next_state: cp.state, effects: vec![EffectIntent::SaveResult { intent: intent.clone() }], events: vec![], timer_changes: vec![] } }).await.unwrap();
 let mut next = req.clone(); next.admission_id.value = "next".into(); next.task.task_id.value = "next".into(); next.input.event_id.value = "next".into();
 if let SessionInputKind::Submit { task } = &mut next.input.kind { task.task_id = next.task.task_id.clone(); }
 assert_eq!(repo.admit(next.clone()).await.unwrap_err(), RuntimeError::CapacityExceeded);
 let saved = SavedResult { intent, saved_at_ms: 2000 };
 let pos = repo.append_and_wake(&req.spec.session_id, SessionInput { event_id: EventId::new(scope(), "saved"), at_ms: 2000, kind: SessionInputKind::ResultSaved { result: saved } }).await.unwrap();
 assert_eq!(repo.admit(next.clone()).await.unwrap_err(), RuntimeError::CapacityExceeded);
 repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: pos, transition: Transition { next_state: cp.state, effects: vec![], events: vec![], timer_changes: vec![] } }).await.unwrap();
 assert!(repo.admit(next).await.is_ok());
}
