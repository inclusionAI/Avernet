use bcn_runtime_api::core::SessionRunner;
use bcn_runtime_model::*;
use bcn_session_runner::StructuredGroupRunner;
use serde_json::{json, Value};
use std::collections::BTreeMap;
fn scope() -> Scope {
    Scope::new("test", "tenant", "v2")
}
fn node(targets: Value) -> Value {
    json!({"kind":"bot_task","display_name":"Task","instruction":"do work","assignee":{"type":"bot_binding","binding":"bot"},"transitions":targets})
}
fn spec(nodes: Value) -> SessionSpec {
    SessionSpec {
        live_output_nodes: None,
        session_id: SessionId::new(scope(), "s"),
        target: ExecutionTarget::Group {
            group_id: GroupId::new(scope(), "g"),
        },
        kind: RunnerKind::StructuredGroup,
        definition: Some(serde_json::from_value(json!({"nodes":nodes})).unwrap()),
        bindings: BTreeMap::from([(
            "bot".into(),
            FrozenTargetBinding {
                target: AgentId::new(scope(), "bot"),
                binding_version: "1".into(),
                binding_id: "b".into(),
                credential_ref: None,
                capabilities: ConnectorCapabilities {
                    streaming: false,
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
        event_id: EventId::new(scope(), "e"),
        at_ms: 20,
        kind,
    }
}
fn message() -> Message {
    Message {
        message_id: MessageId::new(scope(), "m"),
        context_id: None,
        task_id: None,
        parts: vec![MessagePart::Text {
            text: "request".into(),
        }],
    }
}
fn start(spec: &SessionSpec) -> Transition {
    StructuredGroupRunner
        .reduce(
            &StructuredGroupRunner.initialize(spec).unwrap(),
            &input(SessionInputKind::Submit {
                task: QueuedTask {
                    task_id: TaskId::new(scope(), "t"),
                    message: message(),
                    admitted_at_ms: 0,
                    terminal_intent: None,
                },
            }),
        )
        .unwrap()
}
fn complete(state: &RunnerState, id: &str) -> Transition {
    StructuredGroupRunner
        .reduce(
            state,
            &input(SessionInputKind::Agent {
                task_id: TaskId::new(scope(), "t"),
                node_id: Some(id.into()),
                dispatch_id: state.current_task.as_ref().unwrap().nodes[id]
                    .dispatch_id
                    .clone()
                    .unwrap(),
                event: AgentEvent::Completed {
                    output: vec![MessagePart::Text {
                        text: format!("{id} output"),
                    }],
                },
            }),
        )
        .unwrap()
}
fn save(state: &RunnerState, id: &str) -> Transition {
    let intent = state.current_task.as_ref().unwrap().nodes[id]
        .result_intent
        .clone()
        .unwrap();
    StructuredGroupRunner
        .reduce(
            state,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent,
                    saved_at_ms: 30,
                },
            }),
        )
        .unwrap()
}
#[test]
fn sequential_dag_waits_for_authoritative_result() {
    let mut b = node(json!({}));
    b["final_output"] = json!(true);
    let first = start(&spec(
        json!({"a":node(json!({"complete":{"targets":["b"]}})),"b":b}),
    ));
    assert_eq!(first.effects.len(), 1);
    let done = complete(&first.next_state, "a");
    assert_eq!(done.effects.len(), 1);
    assert_eq!(
        done.next_state.current_task.as_ref().unwrap().nodes["b"].phase,
        NodePhase::Pending
    );
    let next = save(&done.next_state, "a");
    assert_eq!(
        next.next_state.current_task.as_ref().unwrap().nodes["b"].phase,
        NodePhase::Running
    );
    assert!(matches!(next.effects[0], EffectIntent::SendAgent { .. }));
}
#[test]
fn parallel_join_waits_for_both_saved_outputs() {
    let mut d = node(json!({}));
    d["final_output"] = json!(true);
    let first = start(&spec(
        json!({"a":node(json!({"complete":{"targets":["b","c"]}})),"b":node(json!({"complete":{"targets":["d"]}})),"c":node(json!({"complete":{"targets":["d"]}})),"d":d}),
    ));
    let a = complete(&first.next_state, "a");
    let fork = save(&a.next_state, "a");
    assert_eq!(fork.effects.len(), 2);
    let b = complete(&fork.next_state, "b");
    let bs = save(&b.next_state, "b");
    assert!(bs.effects.is_empty());
    let c = complete(&bs.next_state, "c");
    let joined = save(&c.next_state, "c");
    assert_eq!(joined.effects.len(), 1);
    let EffectIntent::SendAgent { request, .. } = &joined.effects[0] else {
        panic!()
    };
    assert!(request.message.parts.len() >= 3);
}
#[test]
fn persisted_judge_selects_one_branch_and_skips_other() {
    let mut a = node(json!({"yes":{"targets":["b"]},"no":{"targets":["c"]}}));
    a["judge"] = json!({"criteria":["choose"],"outcomes":["yes","no"]});
    let mut d = node(json!({}));
    d["final_output"] = json!(true);
    let first = start(&spec(
        json!({"a":a,"b":node(json!({"complete":{"targets":["d"]}})),"c":node(json!({"complete":{"targets":["d"]}})),"d":d}),
    ));
    let done = complete(&first.next_state, "a");
    let judged = save(&done.next_state, "a");
    let EffectIntent::Judge { request } = &judged.effects[0] else {
        panic!()
    };
    let chosen = StructuredGroupRunner
        .reduce(
            &judged.next_state,
            &input(SessionInputKind::JudgeDecided {
                task_id: TaskId::new(scope(), "t"),
                node_id: request.node_id.clone(),
                branch: "yes".into(),
            }),
        )
        .unwrap();
    assert!(chosen
        .effects
        .iter()
        .all(|e| !matches!(e, EffectIntent::SendAgent { .. })));
    let persisted = save(&chosen.next_state, &request.node_id);
    assert_eq!(
        persisted.next_state.current_task.as_ref().unwrap().nodes["c"].phase,
        NodePhase::Skipped
    );
    assert_eq!(
        persisted.next_state.current_task.as_ref().unwrap().nodes["b"].phase,
        NodePhase::Running
    );
    let replay = StructuredGroupRunner
        .reduce(
            &persisted.next_state,
            &input(SessionInputKind::JudgeDecided {
                task_id: TaskId::new(scope(), "t"),
                node_id: request.node_id.clone(),
                branch: "no".into(),
            }),
        )
        .unwrap();
    assert!(replay.effects.is_empty());
    assert_eq!(replay.next_state, persisted.next_state);
}
#[test]
fn human_reply_requires_matching_task_interaction_and_scope() {
    let first = start(&spec(
        json!({"h":{"kind":"human_input","display_name":"Input","instruction":"confirm","node_timeout_ms":500}}),
    ));
    let interaction = first.next_state.current_task.as_ref().unwrap().nodes["h"]
        .interaction_id
        .clone()
        .unwrap();
    let reply = |id: &str, scope_value: Scope| {
        input(SessionInputKind::HumanReply {
            task_id: TaskId::new(scope(), "t"),
            interaction_id: InteractionId::new(scope(), id),
            caller: CallerContext {
                scope: scope_value,
                principal: "user".into(),
                trace_id: None,
            },
            message: message(),
        })
    };
    assert!(StructuredGroupRunner
        .reduce(&first.next_state, &reply("wrong", scope()))
        .unwrap()
        .effects
        .is_empty());
    assert_eq!(
        StructuredGroupRunner.reduce(
            &first.next_state,
            &reply(&interaction.value, Scope::new("test", "other", "v2"))
        ),
        Err(RuntimeError::AccessDenied)
    );
    let answer = StructuredGroupRunner
        .reduce(&first.next_state, &reply(&interaction.value, scope()))
        .unwrap();
    assert!(matches!(answer.effects[0], EffectIntent::SaveResult { .. }));
}
#[test]
fn validation_rejects_cycles_unsupported_features_and_limits() {
    let cases = [
        json!({"a":node(json!({"complete":{"targets":["a"]}}))}),
        json!({"a":{"kind":"loop","display_name":"x"}}),
        json!({"a":{"kind":"group_chat","display_name":"x"}}),
        json!({"a":{"kind":"tool_action","display_name":"x"}}),
    ];
    for nodes in cases {
        assert!(StructuredGroupRunner.validate(&spec(nodes)).is_err());
    }
    let mut terminal = node(json!({}));
    terminal["final_output"] = json!(true);
    let mut s = spec(json!({"a":terminal}));
    s.policy.max_graph_nodes = 0;
    assert!(StructuredGroupRunner.validate(&s).is_err());
}
#[test]
fn failed_snapshot_keeps_partial_output_and_blocks_successors() {
    let mut b = node(json!({}));
    b["final_output"] = json!(true);
    let first = start(&spec(
        json!({"a":node(json!({"complete":{"targets":["b"]}})),"b":b}),
    ));
    let failed = StructuredGroupRunner
        .reduce(
            &first.next_state,
            &input(SessionInputKind::Agent {
                task_id: TaskId::new(scope(), "t"),
                node_id: Some("a".into()),
                dispatch_id: first.next_state.current_task.as_ref().unwrap().nodes["a"]
                    .dispatch_id
                    .clone()
                    .unwrap(),
                event: AgentEvent::Snapshot {
                    snapshot: ExecutionSnapshot { input_prompt: None, observation_error: None,
                        handle: ExecutionHandle {
                            target: AgentId::new(scope(), "bot"),
                            binding_version: "1".into(),
                            remote_task_id: Some("r".into()),
                            remote_context_id: None,
                        },
                        status: TaskStatus::Failed,
                        output: vec![MessagePart::Text {
                            text: "partial".into(),
                        }],
                        error: Some(RuntimeError::Unavailable("failed".into())),
                    },
                },
            }),
        )
        .unwrap();
    assert_eq!(
        failed.next_state.current_task.as_ref().unwrap().nodes["a"]
            .result_intent
            .as_ref()
            .unwrap()
            .output,
        vec![MessagePart::Text {
            text: "partial".into()
        }]
    );
    let saved = save(&failed.next_state, "a");
    assert_eq!(
        saved.next_state.current_task.as_ref().unwrap().nodes["b"].phase,
        NodePhase::Skipped
    );
    assert!(!saved
        .effects
        .iter()
        .any(|e| matches!(e, EffectIntent::SendAgent { .. })));
}

#[test]
fn unsolicited_cancelled_snapshot_finishes_after_result_barriers() {
    let mut terminal = node(json!({}));
    terminal["final_output"] = json!(true);
    let first = start(&spec(json!({"a": terminal})));
    let cancelled = StructuredGroupRunner
        .reduce(
            &first.next_state,
            &input(SessionInputKind::Agent {
                task_id: TaskId::new(scope(), "t"),
                node_id: Some("a".into()),
                dispatch_id: first.next_state.current_task.as_ref().unwrap().nodes["a"]
                    .dispatch_id
                    .clone()
                    .unwrap(),
                event: AgentEvent::Snapshot {
                    snapshot: ExecutionSnapshot { input_prompt: None, observation_error: None,
                        handle: ExecutionHandle {
                            target: AgentId::new(scope(), "bot"),
                            binding_version: "1".into(),
                            remote_task_id: Some("remote".into()),
                            remote_context_id: None,
                        },
                        status: TaskStatus::Cancelled,
                        output: vec![],
                        error: None,
                    },
                },
            }),
        )
        .unwrap();
    let saved_node = save(&cancelled.next_state, "a");
    let EffectIntent::SaveResult { intent } = &saved_node.effects[0] else {
        panic!()
    };
    assert_eq!(intent.node_id, None);
    assert_eq!(intent.status, TaskStatus::Cancelled);
    let saved_task = StructuredGroupRunner
        .reduce(
            &saved_node.next_state,
            &input(SessionInputKind::ResultSaved {
                result: SavedResult {
                    intent: intent.clone(),
                    saved_at_ms: 40,
                },
            }),
        )
        .unwrap();
    assert_eq!(
        saved_task.next_state.current_task.unwrap().status,
        TaskStatus::Cancelled
    );
}

#[test]
fn human_cancel_is_saved_and_cancels_its_deadline() {
    let first = start(&spec(
        json!({"h":{"kind":"human_input","display_name":"Input","instruction":"confirm","node_timeout_ms":500}}),
    ));
    let cancelled = StructuredGroupRunner
        .reduce(
            &first.next_state,
            &input(SessionInputKind::Cancel {
                command: CancelCommand {
                    task_id: TaskId::new(scope(), "t"),
                    operation_id: OperationId::new(scope(), "cancel"),
                },
            }),
        )
        .unwrap();
    assert!(matches!(
        cancelled.effects[0],
        EffectIntent::SaveResult { .. }
    ));
    let saved = save(&cancelled.next_state, "h");
    assert!(saved
        .timer_changes
        .iter()
        .any(|t| matches!(t, TimerChange::Cancel(_))));
    assert!(saved
        .effects
        .iter()
        .all(|e| !matches!(e, EffectIntent::SendAgent { .. })));
}

#[test]
fn unsupported_definition_features_never_enter_runner_state() {
    let mut terminal = node(json!({}));
    terminal["final_output"] = json!(true);
    for (field, value) in [
        ("output_contract", json!({"type":"json"})),
        ("action", json!({"type":"run"})),
        ("notification", json!({"mode":"fixed_group"})),
    ] {
        let mut n = terminal.clone();
        n[field] = value;
        assert!(StructuredGroupRunner
            .initialize(&spec(json!({"a": n})))
            .is_err());
    }
    let mut s = spec(
        json!({"a": node(json!({"complete":{"targets":["b"],"guard":"condition"}})), "b": terminal.clone()}),
    );
    assert!(StructuredGroupRunner.validate(&s).is_err());
    s = spec(json!({"a": terminal}));
    s.policy.max_agents = 0;
    assert_eq!(
        StructuredGroupRunner.validate(&s),
        Err(RuntimeError::CapacityExceeded)
    );
}

#[test]
fn human_continuation_returns_working_and_success_exposes_final_output() {
    let mut terminal = node(json!({}));
    terminal["final_output"] = json!(true);
    let first = start(&spec(json!({
        "h":{"kind":"human_input","display_name":"Input","instruction":"confirm","node_timeout_ms":500,"transitions":{"complete":{"targets":["b"]}}},
        "b":terminal,
    })));
    let interaction_id = first.next_state.current_task.as_ref().unwrap().nodes["h"]
        .interaction_id
        .clone()
        .unwrap();
    let answered = StructuredGroupRunner
        .reduce(
            &first.next_state,
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
    let continued = save(&answered.next_state, "h");
    assert_eq!(
        continued.next_state.current_task.as_ref().unwrap().status,
        TaskStatus::Working
    );
    let completed = complete(&continued.next_state, "b");
    let saved = save(&completed.next_state, "b");
    let EffectIntent::SaveResult { intent } = &saved.effects[0] else {
        panic!()
    };
    assert_eq!(
        intent.output,
        vec![MessagePart::Text {
            text: "b output".into()
        }]
    );
    assert_eq!(intent.status, TaskStatus::Completed);
}

#[test]
fn judged_branch_join_keeps_business_output_after_checkpoint_recovery() {
    let mut a = node(json!({"yes":{"targets":["b"]},"no":{"targets":["c"]}}));
    a["judge"] = json!({"criteria":["choose"],"outcomes":["yes","no"]});
    let mut d = node(json!({}));
    d["final_output"] = json!(true);
    let mut s = spec(
        json!({"a":a,"b":node(json!({"complete":{"targets":["d"]}})),"c":node(json!({"complete":{"targets":["d"]}})),"d":d}),
    );
    s.policy.max_graph_nodes = 4;
    let first = start(&s);
    let done = complete(&first.next_state, "a");
    let judged = save(&done.next_state, "a");
    let EffectIntent::Judge { request } = &judged.effects[0] else {
        panic!()
    };
    assert_eq!(
        request.inputs["a"].intent.output,
        vec![MessagePart::Text {
            text: "a output".into()
        }]
    );
    let chosen = StructuredGroupRunner
        .reduce(
            &judged.next_state,
            &input(SessionInputKind::JudgeDecided {
                task_id: TaskId::new(scope(), "t"),
                node_id: request.node_id.clone(),
                branch: "yes".into(),
            }),
        )
        .unwrap();
    let branch = save(&chosen.next_state, &request.node_id);
    let recovered: RunnerState =
        serde_json::from_slice(&serde_json::to_vec(&branch.next_state).unwrap()).unwrap();
    let completed = complete(&recovered, "b");
    let joined = save(&completed.next_state, "b");
    let EffectIntent::SendAgent { request, .. } = &joined.effects[0] else {
        panic!()
    };
    assert_eq!(
        request.message.parts,
        vec![
            MessagePart::Text {
                text: "request".into()
            },
            MessagePart::Text {
                text: "do work".into()
            },
            MessagePart::Text {
                text: "a output".into()
            },
            MessagePart::Text {
                text: "b output".into()
            },
        ]
    );
    assert!(!joined
        .effects
        .iter()
        .any(|e| matches!(e, EffectIntent::Judge { .. })));
}

#[test]
fn cancelled_join_does_not_schedule_successors_and_waits_for_remote_evidence() {
    let mut d = node(json!({}));
    d["final_output"] = json!(true);
    let first = start(&spec(
        json!({"a":node(json!({"complete":{"targets":["b","c"]}})),"b":node(json!({"complete":{"targets":["d"]}})),"c":node(json!({"complete":{"targets":["d"]}})),"d":d}),
    ));
    let done = complete(&first.next_state, "a");
    let fork = save(&done.next_state, "a");
    let cancelled = StructuredGroupRunner
        .reduce(
            &fork.next_state,
            &input(SessionInputKind::Cancel {
                command: CancelCommand {
                    task_id: TaskId::new(scope(), "t"),
                    operation_id: OperationId::new(scope(), "cancel"),
                },
            }),
        )
        .unwrap();
    assert!(!cancelled
        .next_state
        .current_task
        .as_ref()
        .unwrap()
        .status
        .is_terminal());
    assert!(cancelled.effects.is_empty());
    assert_eq!(
        cancelled.next_state.current_task.as_ref().unwrap().nodes["d"].phase,
        NodePhase::Skipped
    );
    let accepted = StructuredGroupRunner
        .reduce(
            &cancelled.next_state,
            &input(SessionInputKind::Agent {
                task_id: TaskId::new(scope(), "t"),
                node_id: Some("b".into()),
                dispatch_id: fork.next_state.current_task.as_ref().unwrap().nodes["b"]
                    .dispatch_id
                    .clone()
                    .unwrap(),
                event: AgentEvent::Accepted {
                    handle: ExecutionHandle {
                        target: AgentId::new(scope(), "bot"),
                        binding_version: "1".into(),
                        remote_task_id: Some("remote-b".into()),
                        remote_context_id: None,
                    },
                },
            }),
        )
        .unwrap();
    let EffectIntent::CancelAgent {
        task_id,
        node_id,
        dispatch_id,
        request,
        ..
    } = &accepted.effects[0]
    else {
        panic!()
    };
    assert_eq!(node_id.as_deref(), Some("b"));
    let confirmed = StructuredGroupRunner
        .reduce(
            &accepted.next_state,
            &input(SessionInputKind::Cancellation {
                task_id: task_id.clone(),
                node_id: node_id.clone(),
                dispatch_id: dispatch_id.clone(),
                operation_id: request.operation_id.clone(),
                outcome: CancelOutcome::Confirmed {
                    affected_remote_task_ids: vec!["remote-b".into()],
                },
            }),
        )
        .unwrap();
    assert!(matches!(
        confirmed.effects[0],
        EffectIntent::SaveResult { .. }
    ));
    assert_eq!(
        confirmed.next_state.current_task.as_ref().unwrap().nodes["b"].phase,
        NodePhase::PendingResult
    );
}

#[test]
fn terminal_human_with_judge_keeps_saved_answer_after_empty_target_selection() {
    let first = start(&spec(json!({
        "h": {
            "kind": "human_input", "display_name": "Input", "instruction": "answer",
            "node_timeout_ms": 1000,
            "judge": { "criteria": ["approve"], "outcomes": ["ok"] },
            "transitions": { "ok": { "targets": [] } },
        },
    })));
    let mut answer = message();
    answer.parts = vec![MessagePart::Text { text: "answer".into() }];
    let answered = StructuredGroupRunner.reduce(
        &first.next_state,
        &input(SessionInputKind::HumanReply {
            task_id: TaskId::new(scope(), "t"),
            interaction_id: first.next_state.current_task.as_ref().unwrap().nodes["h"]
                .interaction_id.clone().unwrap(),
            caller: CallerContext { scope: scope(), principal: "user".into(), trace_id: None },
            message: answer.clone(),
        }),
    ).unwrap();
    let judged = save(&answered.next_state, "h");
    let EffectIntent::Judge { request } = &judged.effects[0] else { panic!() };
    assert_eq!(request.inputs["h"].intent.output, answer.parts);
    let chosen = StructuredGroupRunner.reduce(
        &judged.next_state,
        &input(SessionInputKind::JudgeDecided {
            task_id: TaskId::new(scope(), "t"),
            node_id: request.node_id.clone(),
            branch: "ok".into(),
        }),
    ).unwrap();
    assert!(chosen.next_state.current_task.as_ref().unwrap().nodes[&request.node_id]
        .result_intent.as_ref().unwrap().output.is_empty());
    let recovered: RunnerState = serde_json::from_slice(
        &serde_json::to_vec(&chosen.next_state).unwrap(),
    ).unwrap();
    let saved_judge = save(&recovered, &request.node_id);
    let EffectIntent::SaveResult { intent } = &saved_judge.effects[0] else { panic!() };
    assert_eq!(intent.status, TaskStatus::Completed);
    assert_eq!(intent.output, answer.parts);
    assert!(saved_judge.next_state.current_task.as_ref().unwrap().nodes[&request.node_id]
        .saved_result.as_ref().unwrap().intent.output.is_empty());
}

#[test]
fn frozen_live_selector_is_compatible_and_rejects_foreign_duplicate_or_nonfinal_nodes() {
    let mut pre=node(json!({"complete":{"targets":["final"]}}));pre["final_output"]=json!(false);
    let mut final_node=node(json!({}));final_node["final_output"]=json!(true);
    let original=spec(json!({"pre":pre,"final":final_node}));
    let mut encoded=serde_json::to_value(&original).unwrap();encoded.as_object_mut().unwrap().remove("live_output_nodes");
    assert!(serde_json::from_value::<SessionSpec>(encoded).unwrap().live_output_nodes.is_none());
    for invalid in [vec!["other"],vec!["pre"],vec!["final","final"]] {
        let mut s=original.clone();s.live_output_nodes=Some(invalid.into_iter().map(str::to_owned).collect());
        assert!(StructuredGroupRunner.validate(&s).is_err());
    }
    let mut s=original;s.live_output_nodes=Some(vec!["final".into()]);assert!(StructuredGroupRunner.validate(&s).is_ok());
}
