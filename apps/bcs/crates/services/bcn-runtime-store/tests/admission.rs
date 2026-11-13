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

#[tokio::test]
async fn admission_rejects_metadata_that_can_exhaust_future_snapshot_reserve() {
    use bcn_runtime_api::port::repo::*;
    let (repo,_,mut req)=fixture();
    let giant=TaskId::new(req.caller.scope.clone(),"\"".repeat(16000));
    req.task.task_id=giant.clone();
    let SessionInputKind::Submit{task}=&mut req.input.kind else{unreachable!()};task.task_id=giant;
    assert!(repo.admit(req.clone()).await.is_err());
    assert!(repo.get_receipt(&req.admission_id).await.unwrap().is_none());
    assert!(repo.read_batch(&req.spec.session_id,0,100).await.unwrap().is_empty());
}

#[test]
fn escaped_maximum_ids_and_reserved_payloads_fit_full_reset_envelope() {
    let (_,_,req)=fixture();let mut task=req.task;
    let s=Scope::new("e".repeat(64),"t".repeat(64),"n".repeat(64));
    task.task_id=TaskId::new(s.clone(),"\"".repeat(256));task.session_id=SessionId::new(s.clone(),"\"".repeat(256));
    task.target=ExecutionTarget::Agent{agent_id:AgentId::new(s.clone(),"\"".repeat(256))};
    task.created_at_ms=u64::MAX;task.updated_at_ms=u64::MAX;
    task.error=Some(RuntimeError::Unavailable("\u{0000}".repeat(512)));
    let empty=vec![MessagePart::Text{text:String::new()}];
    let content=(MAX_VALUE_BYTES-131072-serde_json::to_vec(&empty).unwrap().len())/2;
    task.output=vec![MessagePart::Text{text:"\"".repeat(content)}];
    let mut i=ActiveInteraction{interaction_id:InteractionId::new(s.clone(),"\"".repeat(256)),prompt:String::new()};
    let content=(65536-serde_json::to_vec(&vec![i.clone()]).unwrap().len())/2;
    i.prompt="\"".repeat(content);task.active_interactions=vec![i];
    assert!(serde_json::to_vec(&task.output).unwrap().len()<=MAX_VALUE_BYTES-131072);
    assert!(serde_json::to_vec(&task.active_interactions).unwrap().len()<=65536);
    let event=TaskEvent{id:EventId::new(s,"\"".repeat(256)),task_id:task.task_id.clone(),cursor:EventCursor{epoch:u64::MAX,sequence:u64::MAX},kind:TaskEventKind::Reset{snapshot:task}};
    let encoded=serde_json::to_vec(&serde_json::json!({"schema":1,"data":event})).unwrap();
    assert!(encoded.len()<MAX_VALUE_BYTES,"full Reset including escaped IDs must fit: {}",encoded.len());
}
