mod common;
use common::*;
use std::sync::Arc;
use bcn_runtime_api::port::repo::*;
use bcn_runtime_model::*;
use bcn_runtime_store::LocalResultStore;
#[tokio::test]
async fn immutable_result_requires_committed_intent_and_keeps_admission_queries() {
 let (repo, _, req) = fixture(); let cp = activated(&repo, &req).await;
 let results = LocalResultStore::new(repo.clone(), Arc::new(repo.clone()), RuntimeProfile::Local).unwrap();
 assert!(LocalResultStore::new(repo.clone(), Arc::new(repo.clone()), RuntimeProfile::Production).is_err());
 let intent = ResultIntent { result_id: ResultId::new(scope(), "result"), task_id: req.task.task_id.clone(), node_id: None, attempt: 0, status: TaskStatus::Completed, output: vec![MessagePart::Text { text: "authoritative".into() }], error: None };
 assert!(results.put_if_absent(intent.clone()).await.is_err());
 let domain = DomainEvent { event_id: EventId::new(scope(), "completed"), task_id: req.task.task_id.clone(), kind: DomainEventKind::TaskStatusChanged { status: TaskStatus::Completed } };
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 0, transition: Transition { next_state: cp.state, effects: vec![EffectIntent::SaveResult { intent: intent.clone() }], events: vec![domain.clone()], timer_changes: vec![] } }).await.unwrap();
 let event = CommittedEventRef { session_id: req.spec.session_id.clone(), event_id: domain.event_id };
 assert!(repo.publish_hard_event(&cp.fence, event.clone()).await.is_err());
 let saved = results.put_if_absent(intent.clone()).await.unwrap();
 assert_eq!(results.put_if_absent(intent.clone()).await.unwrap(), saved);
 let mut changed = intent; changed.output.clear(); assert!(results.put_if_absent(changed).await.is_err());
 repo.publish_hard_event(&cp.fence, event).await.unwrap();
 let view = repo.get_task(&req.caller, &req.task.task_id).await.unwrap();
 assert_eq!(view.status, TaskStatus::Completed); assert_eq!(view.output, saved.intent.output);
 assert_eq!(view.session_id, req.task.session_id); assert_eq!(view.target, req.task.target);
 let listed = results.list_tasks(&req.caller, TaskQuery { target: None, session_id: None, cursor: None, limit: 10 }).await.unwrap();
 assert_eq!(listed.items, vec![view]);
 assert_eq!(results.read_group_records(&req.task.task_id, None, 1).await.unwrap().items, vec![saved]);
}
#[tokio::test]
async fn external_saved_result_confirmation_is_atomic_and_validates_session_intent() {
 let (repo, _, req) = fixture(); let cp = activated(&repo, &req).await;
 let intent = ResultIntent { result_id: ResultId::new(scope(), "external"), task_id: req.task.task_id.clone(), node_id: None, attempt: 0, status: TaskStatus::Completed, output: vec![MessagePart::Text { text: "external output".into() }], error: None };
 let saved = SavedResult { intent: intent.clone(), saved_at_ms: 5000 };
 let input = SessionInput { event_id: EventId::new(scope(), "external-saved"), at_ms: 5000, kind: SessionInputKind::ResultSaved { result: saved.clone() } };
 assert!(repo.append_and_wake(&req.spec.session_id, input.clone()).await.is_err());
 let event = DomainEvent { event_id: EventId::new(scope(), "external-complete"), task_id: req.task.task_id.clone(), kind: DomainEventKind::TaskStatusChanged { status: TaskStatus::Completed } };
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 0, transition: Transition { next_state: cp.state, effects: vec![EffectIntent::SaveResult { intent }], events: vec![event.clone()], timer_changes: vec![] } }).await.unwrap();
 let mut forged = input.clone(); if let SessionInputKind::ResultSaved { result } = &mut forged.kind { result.intent.output.clear(); }
 assert!(repo.append_and_wake(&req.spec.session_id, forged).await.is_err());
 assert_eq!(repo.read_batch(&req.spec.session_id, 0, 10).await.unwrap().len(), 1);
 let pos = repo.append_and_wake(&req.spec.session_id, input.clone()).await.unwrap();
 assert_eq!(repo.append_and_wake(&req.spec.session_id, input).await.unwrap(), pos);
 repo.publish_hard_event(&cp.fence, CommittedEventRef { session_id: req.spec.session_id.clone(), event_id: event.event_id }).await.unwrap();
 assert_eq!(repo.get_task(&req.caller, &req.task.task_id).await.unwrap().output, saved.intent.output);
}
