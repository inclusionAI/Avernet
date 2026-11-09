mod common;
use common::*;
use bcn_runtime_api::port::repo::*;
use bcn_runtime_model::*;
use bcn_runtime_store::RuntimeStore;
#[tokio::test]
async fn committed_publication_ack_and_dispatch_restart_preserve_exact_operation() {
 let (repo, atomic, req) = fixture();
 let cp = submitted(&repo, &req).await;
 let id = effect_command_id(&req.spec.session_id, &cp.pending_effects[0]).unwrap();
 let reference = CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id.clone() };
 let command = repo.publish(&cp.fence, reference.clone()).await.unwrap();
 assert_eq!(repo.publish(&cp.fence, reference.clone()).await.unwrap(), command);
 assert!(repo.load(&req.spec.session_id).await.unwrap().unwrap().pending_effects.is_empty());
 let consumer = node("connector");
 let received = repo.receive(&scope(), &consumer, None, 10).await.unwrap(); assert_eq!(received.items, vec![command]);
 let first = repo.claim(&id, consumer.clone()).await.unwrap();
 assert_eq!(first.phase, DispatchPhase::Prepared);
 assert_eq!(repo.claim(&id, consumer.clone()).await.unwrap(), first);
 repo.ack(&id, &consumer).await.unwrap();
 let restarted = RuntimeStore::new(atomic);
 let recovered = DispatchStore::scan_active(&restarted, &scope(), None, 10).await.unwrap().items;
 assert_eq!(recovered.len(), 1);
 assert_eq!(recovered[0].context, DispatchContext { command_id: id.clone(), session_id: req.spec.session_id.clone(), task_id: req.task.task_id.clone(), node_id: None });
 assert_eq!(serde_json::from_slice::<DispatchRecord>(&serde_json::to_vec(&recovered[0]).unwrap()).unwrap(), recovered[0]);
 let sent = restarted.mark_may_have_sent(&first.fence, first.revision).await.unwrap();
 assert_eq!(sent.phase, DispatchPhase::MayHaveSent);
 let moved = restarted.takeover(&sent.fence, node("connector-new")).await.unwrap();
 assert_eq!(moved.operation, first.operation); assert_eq!(moved.phase, DispatchPhase::MayHaveSent);
 assert_eq!(moved.fence.epoch, 2);
 let input = SessionInput { event_id: EventId::new(scope(), "terminal"), at_ms: 1000, kind: SessionInputKind::Agent { task_id: req.task.task_id.clone(), node_id: None, dispatch_id: moved.fence.dispatch_id.clone(), event: AgentEvent::Completed { output: vec![] } } };
 let receipt = DispatchReceipt::Agent { event: AgentEvent::Completed { output: vec![] } };
 assert_eq!(restarted.save_receipt(&sent.fence, sent.revision, receipt.clone(), &req.spec.session_id, input.clone()).await, Err(RuntimeError::OwnershipLost));
 restarted.save_receipt(&moved.fence, moved.revision, receipt, &req.spec.session_id, input).await.unwrap();
 assert!(DispatchStore::scan_active(&restarted, &scope(), None, 10).await.unwrap().items.is_empty());
 assert_eq!(restarted.read_batch(&req.spec.session_id, 1, 10).await.unwrap().len(), 1);
 let new_owner = restarted.transfer_shard(cp.fence.clone(), node("new-worker")).await.unwrap();
 assert!(matches!(restarted.publish(&cp.fence, reference.clone()).await, Err(RuntimeError::OwnershipLost)));
 assert!(restarted.publish(&new_owner, reference).await.is_ok());
 assert!(restarted.receive(&scope(), &consumer, None, 10).await.unwrap().items.is_empty());
}
#[tokio::test]
async fn uncommitted_effect_and_wrong_receipt_kind_fail_closed() {
 let (repo, _, req) = fixture(); let cp = submitted(&repo, &req).await;
 assert!(repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: CommandId::new(scope(), "invented") }).await.is_err());
 let id = effect_command_id(&req.spec.session_id, &cp.pending_effects[0]).unwrap();
 assert!(repo.claim(&id, node("connector")).await.is_err());
 repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id.clone() }).await.unwrap();
 let first = repo.claim(&id, node("connector")).await.unwrap();
 let sent = repo.mark_may_have_sent(&first.fence, first.revision).await.unwrap();
 assert!(repo.save_receipt(&sent.fence, sent.revision, DispatchReceipt::Cancellation { outcome: CancelOutcome::Unknown }, &req.spec.session_id, req.input.clone()).await.is_err());
 assert_eq!(repo.read_batch(&req.spec.session_id, 0, 10).await.unwrap().len(), 1);
}
#[tokio::test]
async fn cancellation_recovery_preserves_frozen_payload_and_rejects_wrong_task() {
 let (repo, _, req) = fixture(); let cp = submitted(&repo, &req).await;
 let request = AgentCancelRequest { operation_id: OperationId::new(scope(), "cancel-operation"), handle: ExecutionHandle { target: binding().target, binding_version: "v1".into(), remote_task_id: Some("remote-task".into()), remote_context_id: Some("remote-context".into()) }, exclusive_context: false };
 let effect = EffectIntent::CancelAgent { task_id: req.task.task_id.clone(), node_id: None, dispatch_id: DispatchId::new(scope(), "cancel-dispatch"), binding: binding(), request: request.clone() };
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 1, transition: Transition { next_state: cp.state, effects: vec![effect.clone()], events: vec![], timer_changes: vec![] } }).await.unwrap();
 let id = effect_command_id(&req.spec.session_id, &effect).unwrap();
 repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id.clone() }).await.unwrap();
 let first = repo.claim(&id, node("connector")).await.unwrap();
 let sent = repo.mark_may_have_sent(&first.fence, first.revision).await.unwrap();
 let moved = repo.takeover(&sent.fence, node("new-connector")).await.unwrap();
 assert_eq!(moved.operation, DispatchOperation::Cancel { dispatch_id: DispatchId::new(scope(), "cancel-dispatch"), binding: binding(), request: request.clone() });
 let mut input = SessionInput { event_id: EventId::new(scope(), "cancel-receipt"), at_ms: 1000, kind: SessionInputKind::Cancellation { task_id: TaskId::new(scope(), "wrong-task"), node_id: None, dispatch_id: moved.fence.dispatch_id.clone(), operation_id: request.operation_id, outcome: CancelOutcome::Unknown } };
 let receipt = DispatchReceipt::Cancellation { outcome: CancelOutcome::Unknown };
 assert!(repo.save_receipt(&moved.fence, moved.revision, receipt.clone(), &req.spec.session_id, input.clone()).await.is_err());
 if let SessionInputKind::Cancellation { task_id, .. } = &mut input.kind { *task_id = req.task.task_id.clone(); }
 repo.save_receipt(&moved.fence, moved.revision, receipt, &req.spec.session_id, input.clone()).await.unwrap();
 assert_eq!(repo.read_batch(&req.spec.session_id, 1, 10).await.unwrap()[0].input, input);
}
#[tokio::test]
async fn delivery_ack_requires_persisted_dispatch_and_does_not_drop_nonterminal_work() {
 let (repo, _, req) = fixture(); let cp = submitted(&repo, &req).await;
 let id = effect_command_id(&req.spec.session_id, &cp.pending_effects[0]).unwrap();
 repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id.clone() }).await.unwrap();
 let connector = node("connector"); repo.receive(&scope(), &connector, None, 10).await.unwrap();
 assert!(repo.ack(&id, &connector).await.is_err());
 let first = repo.claim(&id, connector.clone()).await.unwrap();
 let sent = repo.mark_may_have_sent(&first.fence, first.revision).await.unwrap();
 let event = AgentEvent::Accepted { handle: ExecutionHandle { target: binding().target, binding_version: "v1".into(), remote_task_id: Some("remote".into()), remote_context_id: None } };
 let input = SessionInput { event_id: EventId::new(scope(), "accepted"), at_ms: 1000, kind: SessionInputKind::Agent { task_id: req.task.task_id.clone(), node_id: None, dispatch_id: sent.fence.dispatch_id.clone(), event: event.clone() } };
 repo.save_receipt(&sent.fence, sent.revision, DispatchReceipt::Agent { event }, &req.spec.session_id, input).await.unwrap();
 repo.ack(&id, &connector).await.unwrap();
 assert_eq!(DispatchStore::scan_active(&repo, &scope(), None, 1).await.unwrap().items[0].phase, DispatchPhase::Accepted);
}
#[tokio::test]
async fn replayed_committed_identities_do_not_recreate_acknowledged_outboxes() {
 let (repo, _, req) = fixture(); let cp = submitted(&repo, &req).await;
 let effect = cp.pending_effects[0].clone(); let event = cp.pending_events[0].clone();
 let id = effect_command_id(&req.spec.session_id, &effect).unwrap();
 repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id }).await.unwrap();
 repo.publish_hard_event(&cp.fence, CommittedEventRef { session_id: req.spec.session_id.clone(), event_id: event.event_id.clone() }).await.unwrap();
 let current = repo.load(&req.spec.session_id).await.unwrap().unwrap();
 let updated = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: current.revision, consumed_input: 1, transition: Transition { next_state: current.state, effects: vec![effect], events: vec![event], timer_changes: vec![] } }).await.unwrap();
 assert!(updated.pending_effects.is_empty()); assert!(updated.pending_events.is_empty());
}
#[tokio::test]
async fn finished_dispatch_is_immutable_and_exact_receipt_retry_is_canonical() {
 let (repo, _, req) = fixture(); let cp = submitted(&repo, &req).await;
 let id = effect_command_id(&req.spec.session_id, &cp.pending_effects[0]).unwrap();
 repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id.clone() }).await.unwrap();
 let d = repo.claim(&id, node("connector")).await.unwrap(); let d = repo.mark_may_have_sent(&d.fence, d.revision).await.unwrap();
 let receipt = DispatchReceipt::Agent { event: AgentEvent::Completed { output: vec![] } };
 let input = SessionInput { event_id: EventId::new(scope(), "done"), at_ms: 1000, kind: SessionInputKind::Agent { task_id: req.task.task_id.clone(), node_id: None, dispatch_id: d.fence.dispatch_id.clone(), event: AgentEvent::Completed { output: vec![] } } };
 let done = repo.save_receipt(&d.fence, d.revision, receipt.clone(), &req.spec.session_id, input.clone()).await.unwrap();
 assert_eq!(repo.save_receipt(&done.fence, d.revision, receipt.clone(), &req.spec.session_id, input.clone()).await.unwrap(), done);
 let mut other_input = input; other_input.event_id.value = "duplicate-different-id".into();
 assert!(repo.save_receipt(&done.fence, done.revision, receipt, &req.spec.session_id, other_input).await.is_err());
 assert!(repo.save_handle(&done.fence, done.revision, ExecutionHandle { target: binding().target, binding_version: "v1".into(), remote_task_id: Some("changed".into()), remote_context_id: None }).await.is_err());
 assert!(repo.takeover(&done.fence, node("new")).await.is_err());
 assert!(repo.append(&done.fence, TaskEvent { id: EventId::new(scope(), "late-delta"), task_id: req.task.task_id.clone(), cursor: EventCursor { epoch: 0, sequence: 0 }, kind: TaskEventKind::Delta { text: "late".into() } }).await.is_err());
}
#[tokio::test]
async fn leased_head_does_not_hide_the_next_ready_command() {
 let (repo, _, req) = fixture(); let cp = submitted(&repo, &req).await;
 let second = EffectIntent::Judge { request: JudgeRequest { operation_id: OperationId::new(scope(), "judge-two"), task_id: req.task.task_id.clone(), node_id: "judge".into(), prompt: "choose".into(), choices: vec!["a".into()], inputs: Default::default() } };
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 1, transition: Transition { next_state: cp.state, effects: vec![second], events: vec![], timer_changes: vec![] } }).await.unwrap();
 for effect in &cp.pending_effects { repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: effect_command_id(&req.spec.session_id, effect).unwrap() }).await.unwrap(); }
 assert_eq!(repo.receive(&scope(), &node("A"), None, 1).await.unwrap().items.len(), 1);
 let first = repo.receive(&scope(), &node("B"), None, 1).await.unwrap();
 assert!(first.items.is_empty()); assert!(first.next_cursor.is_some());
 let next = repo.receive(&scope(), &node("B"), first.next_cursor, 1).await.unwrap();
 assert_eq!(next.items.len(), 1); assert!(next.next_cursor.is_none());
}
