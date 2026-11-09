mod common;
use common::*;
use bcn_runtime_api::port::repo::*;
use bcn_runtime_model::*;
use bcn_runtime_store::RuntimeStore;
#[tokio::test]
async fn hard_events_require_commit_and_current_owner_without_any_dispatch() {
 let (repo, atomic, req) = fixture(); let cp = activated(&repo, &req).await;
 let event = DomainEvent { event_id: EventId::new(scope(), "human"), task_id: req.task.task_id.clone(), kind: DomainEventKind::InputRequired { interaction_id: InteractionId::new(scope(), "interaction"), prompt: "choose".into() } };
 let reference = CommittedEventRef { session_id: req.spec.session_id.clone(), event_id: event.event_id.clone() };
 assert!(repo.publish_hard_event(&cp.fence, reference.clone()).await.is_err());
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 0, transition: Transition { next_state: cp.state, effects: vec![], events: vec![event], timer_changes: vec![] } }).await.unwrap();
 let cursor = repo.publish_hard_event(&cp.fence, reference.clone()).await.unwrap();
 assert_eq!(cursor, EventCursor { epoch: 0, sequence: 1 });
 assert_eq!(repo.publish_hard_event(&cp.fence, reference.clone()).await.unwrap(), cursor);
 assert!(repo.load(&req.spec.session_id).await.unwrap().unwrap().pending_events.is_empty());
 let next = repo.transfer_shard(cp.fence.clone(), node("two")).await.unwrap();
 let restart = RuntimeStore::new(atomic);
 assert_eq!(restart.publish_hard_event(&cp.fence, reference.clone()).await, Err(RuntimeError::OwnershipLost));
 assert_eq!(restart.publish_hard_event(&next, reference).await.unwrap(), cursor);
 assert_eq!(restart.read_after(&req.task.task_id, Some(EventCursor { epoch: 0, sequence: 0 }), 10).await.unwrap().events.len(), 1);
}
#[tokio::test]
async fn reset_is_atomic_retryable_and_old_dispatch_epoch_cannot_append() {
 let (repo, atomic, req) = fixture(); let cp = submitted(&repo, &req).await;
 let id = effect_command_id(&req.spec.session_id, &cp.pending_effects[0]).unwrap();
 repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id.clone() }).await.unwrap();
 let first = repo.claim(&id, node("connector")).await.unwrap();
 let first = repo.mark_may_have_sent(&first.fence, first.revision).await.unwrap();
 let projection = ConnectorProjection { task_id: req.task.task_id.clone(), output: vec![MessagePart::Text { text: "one".into() }], expected_cursor: EventCursor { epoch: 0, sequence: 0 } };
 let reset_id = EventId::new(scope(), "reset/one");
 let cursor = repo.publish_reset(&first.fence, projection.clone(), reset_id.clone()).await.unwrap();
 assert_eq!(cursor, EventCursor { epoch: 1, sequence: 1 });
 let restart = RuntimeStore::new(atomic);
 assert_eq!(restart.publish_reset(&first.fence, projection, reset_id).await.unwrap(), cursor);
 let delta = TaskEvent { id: EventId::new(scope(), "delta"), task_id: req.task.task_id.clone(), cursor: cursor.clone(), kind: TaskEventKind::Delta { text: " two".into() } };
 let cursor2 = restart.append(&first.fence, delta.clone()).await.unwrap();
 assert_eq!(cursor2, EventCursor { epoch: 1, sequence: 2 });
 let moved = restart.takeover(&first.fence, node("new-connector")).await.unwrap();
 assert_eq!(restart.append(&first.fence, delta.clone()).await, Err(RuntimeError::OwnershipLost));
 let mut fresh = delta; fresh.id.value = "delta-new".into(); fresh.cursor = cursor2.clone();
 assert!(restart.append(&moved.fence, fresh).await.is_err());
 let projection = ConnectorProjection { task_id: req.task.task_id.clone(), output: vec![MessagePart::Text { text: "recovered".into() }], expected_cursor: cursor2 };
 assert_eq!(restart.publish_reset(&moved.fence, projection, EventId::new(scope(), "reset/two")).await.unwrap(), EventCursor { epoch: 2, sequence: 1 });
 let page = restart.read_after(&req.task.task_id, Some(EventCursor { epoch: 1, sequence: 2 }), 10).await.unwrap();
 assert!(page.cursor_expired); assert_eq!(page.snapshot.task.output, vec![MessagePart::Text { text: "recovered".into() }]);
}
#[tokio::test]
async fn parallel_node_resets_and_deltas_preserve_peer_projection() {
 use bcn_runtime_api::core::SessionRunner;
 use bcn_session_runner::StructuredGroupRunner;
 use serde_json::json;
 let (repo, _, mut req) = fixture();
 req.spec.kind = RunnerKind::StructuredGroup;
 req.spec.target = ExecutionTarget::Group { group_id: GroupId::new(scope(), "group") }; req.task.target = req.spec.target.clone();
 let bot = json!({"kind":"bot_task","display_name":"Task","instruction":"work","assignee":{"type":"bot_binding","binding":"agent"},"transitions":{"complete":{"targets":["c"]}}});
 let mut final_node = bot.clone(); final_node["transitions"] = json!({}); final_node["final_output"] = json!(true);
 let mut entry = bot.clone(); entry["transitions"] = json!({"complete":{"targets":["a","b"]}});
 req.spec.definition = Some(serde_json::from_value(json!({"nodes":{"entry":entry,"a":bot.clone(),"b":bot,"c":final_node}})).unwrap());
 repo.admit(req.clone()).await.unwrap();
 let owner = repo.claim_shard(session_shard(&req.spec.session_id), node("worker"), None).await.unwrap();
 let initial = StructuredGroupRunner.initialize(&req.spec).unwrap();
 let cp = repo.activate(req.spec.clone(), initial, owner).await.unwrap();
 let message = if let SessionInputKind::Submit { task } = &req.input.kind { task.message.clone() } else { panic!() };
 let effects = ["a", "b"].into_iter().map(|name| EffectIntent::SendAgent { task_id: req.task.task_id.clone(), node_id: Some(name.into()), request: AgentSendRequest { dispatch_id: DispatchId::new(scope(), format!("dispatch-{name}")), operation_id: OperationId::new(scope(), format!("operation-{name}")), binding: binding(), message: message.clone(), handle: None, expires_at_ms: None } }).collect();
 let transition = Transition { next_state: cp.state.clone(), effects, events: vec![], timer_changes: vec![] };
 let cp = repo.commit(SessionCommit { session_id: req.spec.session_id.clone(), fence: cp.fence, expected_revision: cp.revision, consumed_input: 1, transition }).await.unwrap();
 assert_eq!(cp.pending_effects.len(), 2);
 let mut dispatches = vec![];
 for effect in &cp.pending_effects {
  let id = effect_command_id(&req.spec.session_id, effect).unwrap();
  repo.publish(&cp.fence, CommittedEffectRef { session_id: req.spec.session_id.clone(), command_id: id.clone() }).await.unwrap();
  let d = repo.claim(&id, node("connector")).await.unwrap();
  dispatches.push(repo.mark_may_have_sent(&d.fence, d.revision).await.unwrap());
 }
 let first = repo.publish_reset(&dispatches[0].fence, ConnectorProjection { task_id: req.task.task_id.clone(), output: vec![MessagePart::Text { text: "A".into() }], expected_cursor: EventCursor { epoch: 0, sequence: 0 } }, EventId::new(scope(), "reset-a")).await.unwrap();
 let second = repo.publish_reset(&dispatches[1].fence, ConnectorProjection { task_id: req.task.task_id.clone(), output: vec![MessagePart::Text { text: "B".into() }], expected_cursor: first }, EventId::new(scope(), "reset-b")).await.unwrap();
 assert_eq!(second.epoch, 2);
 repo.append(&dispatches[0].fence, TaskEvent { id: EventId::new(scope(), "delta-a"), task_id: req.task.task_id.clone(), cursor: second, kind: TaskEventKind::Delta { text: "+".into() } }).await.unwrap();
 let snapshot = repo.read_snapshot(&req.task.task_id).await.unwrap().unwrap();
 assert_eq!(snapshot.task.output, vec![MessagePart::Text { text: "A+".into() }, MessagePart::Text { text: "B".into() }]);
 assert_eq!(snapshot.task.status, TaskStatus::Submitted);
}
