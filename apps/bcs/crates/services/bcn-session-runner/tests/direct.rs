use bcn_runtime_api::core::SessionRunner;
use bcn_runtime_model::*;
use bcn_session_runner::DirectA2aRunner;
use std::collections::BTreeMap;
fn scope() -> Scope {
    Scope::new("test", "tenant", "v2")
}
fn message() -> Message {
    Message {
        message_id: MessageId::new(scope(), "m"),
        context_id: None,
        task_id: None,
        parts: vec![MessagePart::Text {
            text: "hello".into(),
        }],
    }
}
fn spec() -> SessionSpec {
    SessionSpec {
        live_output_nodes: None,
        session_id: SessionId::new(scope(), "s"),
        target: ExecutionTarget::Agent {
            agent_id: AgentId::new(scope(), "bot"),
        },
        kind: RunnerKind::DirectA2a,
        definition: None,
        bindings: BTreeMap::from([(
            "bot".into(),
            FrozenTargetBinding {
                target: AgentId::new(scope(), "bot"),
                binding_version: "v1".into(),
                binding_id: "b".into(),
                credential_ref: None,
                capabilities: ConnectorCapabilities {
                    streaming: true,
                    query: true,
                    subscribe: true,
                    input_continuation: true,
                    cancellation: CancellationSupport::SingleTask,
                    idempotency: IdempotencySupport::Unsupported,
                },
            },
        )]),
        policy: ExecutionPolicy {
            execution_timeout_ms: 1000,
            human_timeout_ms: 2000,
            judge_available: true,
            max_pending_tasks: 16,
            max_graph_nodes: 32,
            max_agents: 5,
        },
    }
}
fn input(kind: SessionInputKind) -> SessionInput {
    SessionInput {
        event_id: EventId::new(scope(), "event"),
        at_ms: 10,
        kind,
    }
}
fn submit(id: &str) -> SessionInput {
    input(SessionInputKind::Submit {
        task: QueuedTask {
            task_id: TaskId::new(scope(), id),
            message: message(),
            admitted_at_ms: 10,
            terminal_intent: None,
        },
    })
}
fn started() -> Transition {
    DirectA2aRunner
        .reduce(&DirectA2aRunner.initialize(&spec()).unwrap(), &submit("t"))
        .unwrap()
}
fn agent(state: &RunnerState, event: AgentEvent) -> SessionInput {
    let task = state.current_task.as_ref().unwrap();
    input(SessionInputKind::Agent {
        task_id: task.task_id.clone(),
        node_id: Some("direct".into()),
        dispatch_id: task.nodes["direct"].dispatch_id.clone().unwrap(),
        event,
    })
}
fn handle() -> ExecutionHandle {
    ExecutionHandle {
        target: AgentId::new(scope(), "bot"),
        binding_version: "v1".into(),
        remote_task_id: Some("remote".into()),
        remote_context_id: Some("ctx".into()),
    }
}
#[test]
fn deterministic_save_barrier_precedes_completion() {
    let idle = DirectA2aRunner.initialize(&spec()).unwrap();
    let first = DirectA2aRunner.reduce(&idle, &submit("t")).unwrap();
    assert_eq!(first, DirectA2aRunner.reduce(&idle, &submit("t")).unwrap());
    assert!(matches!(
        &first.effects[..],
        [EffectIntent::SendAgent { .. }]
    ));
    assert_eq!(
        first.next_state.current_task.as_ref().unwrap().status,
        TaskStatus::Working
    );
    let completed = DirectA2aRunner
        .reduce(
            &first.next_state,
            &agent(
                &first.next_state,
                AgentEvent::Completed {
                    output: message().parts,
                },
            ),
        )
        .unwrap();
    assert_eq!(
        completed.next_state.current_task.as_ref().unwrap().status,
        TaskStatus::PendingResult
    );
    assert!(!completed.events.iter().any(|e| matches!(
        e.kind,
        DomainEventKind::TaskStatusChanged {
            status: TaskStatus::Completed
        }
    )));
    let EffectIntent::SaveResult { intent } = &completed.effects[0] else {
        panic!()
    };
    let saved = DirectA2aRunner
        .reduce(
            &completed.next_state,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent: intent.clone(),
                    saved_at_ms: 12,
                },
            }),
        )
        .unwrap();
    assert_eq!(
        saved.next_state.current_task.as_ref().unwrap().status,
        TaskStatus::Completed
    );
    assert!(saved
        .timer_changes
        .iter()
        .any(|t| matches!(t, TimerChange::Cancel(_))));
}
#[test]
fn continuation_uses_same_remote_execution_and_rejects_stale_dispatch() {
    let first = started();
    let waiting = DirectA2aRunner
        .reduce(
            &first.next_state,
            &agent(
                &first.next_state,
                AgentEvent::InputRequired {
                    handle: handle(),
                    prompt: message().parts,
                },
            ),
        )
        .unwrap();
    let interaction = waiting.next_state.current_task.as_ref().unwrap().nodes["direct"]
        .interaction_id
        .clone()
        .unwrap();
    let reply = input(SessionInputKind::HumanReply {
        task_id: TaskId::new(scope(), "t"),
        interaction_id: interaction,
        caller: CallerContext {
            scope: scope(),
            principal: "user".into(),
            trace_id: None,
        },
        message: message(),
    });
    let resumed = DirectA2aRunner.reduce(&waiting.next_state, &reply).unwrap();
    let EffectIntent::SendAgent { request, .. } = &resumed.effects[0] else {
        panic!()
    };
    assert_eq!(request.handle, Some(handle()));
    assert_ne!(
        request.dispatch_id,
        first.next_state.current_task.as_ref().unwrap().nodes["direct"]
            .dispatch_id
            .clone()
            .unwrap()
    );
    assert!(DirectA2aRunner
        .reduce(
            &resumed.next_state,
            &agent(&first.next_state, AgentEvent::Completed { output: vec![] })
        )
        .unwrap()
        .effects
        .is_empty());
}
#[test]
fn unknown_context_blocks_later_tasks() {
    let first = started();
    let failed = DirectA2aRunner
        .reduce(
            &first.next_state,
            &agent(
                &first.next_state,
                AgentEvent::Failed {
                    error: RuntimeError::ExecutionUnknown,
                },
            ),
        )
        .unwrap();
    assert!(failed.next_state.context_blocked);
    assert_eq!(
        DirectA2aRunner.reduce(&failed.next_state, &submit("next")),
        Err(RuntimeError::ContextBlocked)
    );
}
#[test]
fn queued_tasks_wait_for_saved_task_result() {
    let first = started();
    let queued = DirectA2aRunner
        .reduce(&first.next_state, &submit("next"))
        .unwrap();
    assert!(queued.effects.is_empty());
    assert_eq!(queued.next_state.pending_tasks.len(), 1);
    let done = DirectA2aRunner
        .reduce(
            &queued.next_state,
            &agent(&queued.next_state, AgentEvent::Completed { output: vec![] }),
        )
        .unwrap();
    assert_eq!(done.effects.len(), 1);
    let EffectIntent::SaveResult { intent } = &done.effects[0] else {
        panic!()
    };
    let next = DirectA2aRunner
        .reduce(
            &done.next_state,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent: intent.clone(),
                    saved_at_ms: 15,
                },
            }),
        )
        .unwrap();
    assert_eq!(
        next.next_state.current_task.as_ref().unwrap().task_id.value,
        "next"
    );
    assert!(next
        .effects
        .iter()
        .any(|e| matches!(e, EffectIntent::SendAgent { .. })));
}
#[test]
fn cancel_requires_evidence_and_timeout_cancels_downstream() {
    let first = started();
    let accepted = DirectA2aRunner
        .reduce(
            &first.next_state,
            &agent(&first.next_state, AgentEvent::Accepted { handle: handle() }),
        )
        .unwrap();
    let cancel = input(SessionInputKind::Cancel {
        command: CancelCommand {
            task_id: TaskId::new(scope(), "t"),
            operation_id: OperationId::new(scope(), "cancel"),
        },
    });
    let cancelling = DirectA2aRunner
        .reduce(&accepted.next_state, &cancel)
        .unwrap();
    assert!(!cancelling
        .next_state
        .current_task
        .as_ref()
        .unwrap()
        .status
        .is_terminal());
    assert!(matches!(
        cancelling.effects[0],
        EffectIntent::CancelAgent { .. }
    ));
    let EffectIntent::CancelAgent {
        dispatch_id,
        request,
        ..
    } = &cancelling.effects[0]
    else {
        panic!()
    };
    let uncertain = DirectA2aRunner
        .reduce(
            &cancelling.next_state,
            &input(SessionInputKind::Cancellation {
                task_id: TaskId::new(scope(), "t"),
                node_id: None,
                dispatch_id: dispatch_id.clone(),
                operation_id: request.operation_id.clone(),
                outcome: CancelOutcome::Unknown,
            }),
        )
        .unwrap();
    assert!(uncertain.next_state.context_blocked);
    let TimerChange::Upsert(deadline) = &first.timer_changes[0] else {
        panic!()
    };
    let timed = DirectA2aRunner
        .reduce(
            &accepted.next_state,
            &input(SessionInputKind::TimerFired {
                timer_id: deadline.timer_id.clone(), output: vec![],
            }),
        )
        .unwrap();
    assert!(timed
        .effects
        .iter()
        .any(|e| matches!(e, EffectIntent::CancelAgent { .. })));
}

#[test]
fn continuation_has_distinct_status_event_ids_and_ignores_old_deadline() {
    let first = started();
    let wait = DirectA2aRunner
        .reduce(
            &first.next_state,
            &agent(
                &first.next_state,
                AgentEvent::InputRequired {
                    handle: handle(),
                    prompt: vec![],
                },
            ),
        )
        .unwrap();
    let interaction_id = wait.next_state.current_task.as_ref().unwrap().nodes["direct"]
        .interaction_id
        .clone()
        .unwrap();
    let resumed = DirectA2aRunner
        .reduce(
            &wait.next_state,
            &input(SessionInputKind::HumanReply {
                task_id: TaskId::new(scope(), "t"),
                interaction_id,
                caller: CallerContext {
                    scope: scope(),
                    principal: "user".into(),
                    trace_id: None,
                },
                message: message(),
            }),
        )
        .unwrap();
    let opened = first
        .events
        .iter()
        .find(|e| {
            matches!(
                e.kind,
                DomainEventKind::TaskStatusChanged {
                    status: TaskStatus::Working
                }
            )
        })
        .unwrap();
    let continued = resumed
        .events
        .iter()
        .find(|e| {
            matches!(
                e.kind,
                DomainEventKind::TaskStatusChanged {
                    status: TaskStatus::Working
                }
            )
        })
        .unwrap();
    assert_ne!(opened.event_id, continued.event_id);
    let TimerChange::Upsert(old) = &first.timer_changes[0] else {
        panic!()
    };
    let stale = DirectA2aRunner
        .reduce(
            &resumed.next_state,
            &input(SessionInputKind::TimerFired {
                timer_id: old.timer_id.clone(), output: vec![],
            }),
        )
        .unwrap();
    assert_eq!(stale.next_state, resumed.next_state);
    assert!(stale.effects.is_empty());
}

#[test]
fn result_ack_must_match_frozen_payload_and_queue_has_a_bound() {
    let first = started();
    let done = DirectA2aRunner
        .reduce(
            &first.next_state,
            &agent(&first.next_state, AgentEvent::Completed { output: vec![] }),
        )
        .unwrap();
    let EffectIntent::SaveResult { intent } = &done.effects[0] else {
        panic!()
    };
    let mut forged = intent.clone();
    forged.output = message().parts;
    assert!(matches!(
        DirectA2aRunner.reduce(
            &done.next_state,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent: forged,
                    saved_at_ms: 1
                }
            })
        ),
        Err(RuntimeError::Conflict(_))
    ));
    let mut state = first.next_state;
    for n in 0..16 {
        state = DirectA2aRunner
            .reduce(&state, &submit(&format!("queued{n}")))
            .unwrap()
            .next_state;
    }
    assert_eq!(
        DirectA2aRunner.reduce(&state, &submit("overflow")),
        Err(RuntimeError::CapacityExceeded)
    );
}

#[test]
fn restored_checkpoint_produces_identical_transition_with_bounded_ids() {
    let mut request = submit(&"task".repeat(300));
    request.event_id = EventId::new(scope(), "huge");
    let idle = DirectA2aRunner.initialize(&spec()).unwrap();
    let state = DirectA2aRunner.reduce(&idle, &request).unwrap().next_state;
    let recovered: RunnerState =
        serde_json::from_slice(&serde_json::to_vec(&state).unwrap()).unwrap();
    let done = agent(
        &state,
        AgentEvent::Completed {
            output: message().parts,
        },
    );
    assert_eq!(
        DirectA2aRunner.reduce(&state, &done).unwrap(),
        DirectA2aRunner.reduce(&recovered, &done).unwrap()
    );
    let dispatch = state.current_task.unwrap().nodes["direct"]
        .dispatch_id
        .clone()
        .unwrap();
    assert!(dispatch.value.len() <= 64);
    assert!(dispatch.value.bytes().all(|v| v.is_ascii_hexdigit()));
}

#[test]
fn queued_cancellation_waits_for_save_and_never_dispatches_cancelled_task() {
    let first = started();
    let queued = DirectA2aRunner
        .reduce(&first.next_state, &submit("queued"))
        .unwrap();
    let cancel = input(SessionInputKind::Cancel {
        command: CancelCommand {
            task_id: TaskId::new(scope(), "queued"),
            operation_id: OperationId::new(scope(), "cancel-queued"),
        },
    });
    let cancelled = DirectA2aRunner.reduce(&queued.next_state, &cancel).unwrap();
    assert_eq!(cancelled.next_state.pending_tasks.len(), 1);
    let EffectIntent::SaveResult { intent } = &cancelled.effects[0] else {
        panic!()
    };
    assert_eq!(intent.status, TaskStatus::Cancelled);
    let duplicate = DirectA2aRunner
        .reduce(&cancelled.next_state, &cancel)
        .unwrap();
    assert!(duplicate.effects.is_empty());
    let mut wrong = intent.clone();
    wrong.output = message().parts;
    assert!(matches!(
        DirectA2aRunner.reduce(
            &cancelled.next_state,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent: wrong,
                    saved_at_ms: 1
                }
            })
        ),
        Err(RuntimeError::Conflict(_))
    ));
    let completed = DirectA2aRunner
        .reduce(
            &cancelled.next_state,
            &agent(
                &cancelled.next_state,
                AgentEvent::Completed { output: vec![] },
            ),
        )
        .unwrap();
    let EffectIntent::SaveResult {
        intent: active_result,
    } = &completed.effects[0]
    else {
        panic!()
    };
    let active_saved = DirectA2aRunner
        .reduce(
            &completed.next_state,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent: active_result.clone(),
                    saved_at_ms: 1,
                },
            }),
        )
        .unwrap();
    assert!(active_saved.effects.is_empty());
    assert_eq!(
        active_saved
            .next_state
            .current_task
            .as_ref()
            .unwrap()
            .task_id
            .value,
        "t"
    );
    let saved_cancel = DirectA2aRunner
        .reduce(
            &active_saved.next_state,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent: intent.clone(),
                    saved_at_ms: 2,
                },
            }),
        )
        .unwrap();
    assert!(saved_cancel.next_state.pending_tasks.is_empty());
    assert!(saved_cancel.effects.is_empty());
    assert!(saved_cancel
        .events
        .iter()
        .any(|e| e.task_id.value == "queued"
            && matches!(
                e.kind,
                DomainEventKind::TaskStatusChanged {
                    status: TaskStatus::Cancelled
                }
            )));
}

#[test]
fn unsaved_queued_cancellations_still_consume_capacity() {
    let mut state = started().next_state;
    for n in 0..16 {
        let name = format!("queued{n}");
        state = DirectA2aRunner
            .reduce(&state, &submit(&name))
            .unwrap()
            .next_state;
        let cancelled = DirectA2aRunner
            .reduce(
                &state,
                &input(SessionInputKind::Cancel {
                    command: CancelCommand {
                        task_id: TaskId::new(scope(), name),
                        operation_id: OperationId::new(scope(), format!("cancel{n}")),
                    },
                }),
            )
            .unwrap();
        assert!(matches!(
            cancelled.effects[0],
            EffectIntent::SaveResult { .. }
        ));
        state = cancelled.next_state;
    }
    assert_eq!(
        DirectA2aRunner.reduce(&state, &submit("overflow")),
        Err(RuntimeError::CapacityExceeded)
    );
}

#[test]
fn unknown_execution_fails_already_queued_tasks_after_save_without_dispatch() {
    let first = started();
    let queued = DirectA2aRunner
        .reduce(&first.next_state, &submit("queued"))
        .unwrap();
    let failed = DirectA2aRunner
        .reduce(
            &queued.next_state,
            &agent(
                &queued.next_state,
                AgentEvent::Failed {
                    error: RuntimeError::ExecutionUnknown,
                },
            ),
        )
        .unwrap();
    assert_eq!(failed.effects.len(), 2);
    let intent = failed
        .effects
        .iter()
        .find_map(|e| match e {
            EffectIntent::SaveResult { intent } if intent.task_id.value == "queued" => {
                Some(intent.clone())
            }
            _ => None,
        })
        .unwrap();
    assert_eq!(intent.status, TaskStatus::Failed);
    assert_eq!(intent.error, Some(RuntimeError::ContextBlocked));
    let recovered: RunnerState =
        serde_json::from_slice(&serde_json::to_vec(&failed.next_state).unwrap()).unwrap();
    let saved = DirectA2aRunner
        .reduce(
            &recovered,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent,
                    saved_at_ms: 2,
                },
            }),
        )
        .unwrap();
    assert!(saved.next_state.pending_tasks.is_empty());
    assert!(saved.events.iter().any(|e| e.task_id.value == "queued"
        && matches!(
            e.kind,
            DomainEventKind::TaskStatusChanged {
                status: TaskStatus::Failed
            }
        )));
    assert!(saved.effects.is_empty());
}

#[test]
fn cancelled_execution_deadline_does_not_end_current_human_wait() {
    let first = started();
    let execution_timer = first.timer_changes.iter().find_map(|change| match change {
        TimerChange::Upsert(deadline) => Some(deadline.timer_id.clone()),
        _ => None,
    }).unwrap();
    let waiting = DirectA2aRunner.reduce(
        &first.next_state,
        &agent(&first.next_state, AgentEvent::InputRequired {
            handle: handle(),
            prompt: vec![],
        }),
    ).unwrap();
    assert!(waiting.timer_changes.contains(&TimerChange::Cancel(execution_timer.clone())));
    let human_timer = waiting.timer_changes.iter().find_map(|change| match change {
        TimerChange::Upsert(deadline) => Some(deadline.timer_id.clone()),
        _ => None,
    }).unwrap();
    // Cancellation cannot remove an execution timeout already in the inbox.
    let recovered: RunnerState = serde_json::from_slice(
        &serde_json::to_vec(&waiting.next_state).unwrap(),
    ).unwrap();
    let stale = DirectA2aRunner.reduce(
        &recovered,
        &input(SessionInputKind::TimerFired { timer_id: execution_timer, output: vec![] }),
    ).unwrap();
    assert_eq!(stale.next_state, recovered);
    assert!(stale.effects.is_empty());
    assert!(stale.events.is_empty());
    assert!(stale.timer_changes.is_empty());
    let expired = DirectA2aRunner.reduce(
        &stale.next_state,
        &input(SessionInputKind::TimerFired { timer_id: human_timer, output: vec![] }),
    ).unwrap();
    assert_eq!(expired.next_state.current_task.as_ref().unwrap().status, TaskStatus::PendingResult);
    assert!(expired.effects.iter().any(|effect| matches!(effect, EffectIntent::SaveResult { .. })));
}
#[test]
fn application_failure_keeps_frozen_intent_against_late_remote_completion() {
 let first=started();
 let failure=DirectA2aRunner.reduce(&first.next_state,&input(SessionInputKind::TaskFailure {task_id:TaskId::new(scope(),"t"),error:RuntimeError::CapacityExceeded,output:vec![]})).unwrap();
 assert!(failure.next_state.context_blocked);
 let intent=failure.next_state.current_task.as_ref().unwrap().result_intent.clone().unwrap();
 let late=DirectA2aRunner.reduce(&failure.next_state,&agent(&failure.next_state,AgentEvent::Completed{output:message().parts})).unwrap();
 assert_eq!(late.next_state.current_task.as_ref().unwrap().result_intent,Some(intent));
 assert!(late.effects.is_empty());
}
