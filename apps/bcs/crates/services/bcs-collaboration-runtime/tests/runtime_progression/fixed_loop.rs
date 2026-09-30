use super::*;
use std::sync::atomic::{AtomicBool, Ordering};
use bcs_domain::StateMachineExecutionPlan;
use bcs_service_api::port::repo::{StateMachineExecutionPlanSnapshot, StateMachineRunSnapshot};

#[path = "recovery.rs"]
mod recovery_tests;

#[path = "api_projection.rs"]
mod api_projection_tests;

#[path = "event_metadata.rs"]
mod event_metadata_tests;

#[path = "loop_observability.rs"]
mod observability_tests;

#[path = "multi_loop_template.rs"]
mod multi_loop_template_tests;

#[path = "edge_display_names.rs"]
mod edge_display_name_tests;

struct SnapshotDefinitions {
    inner: Arc<MemoryCollaborationStore>,
    fail_save: AtomicBool,
    corrupt_read: AtomicBool,
    hide_definitions: AtomicBool,
}

#[async_trait]
impl StateMachineDefinitionRepoPort for SnapshotDefinitions {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()> {
        StateMachineDefinitionRepoPort::upsert(&*self.inner, definition).await
    }
    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>> {
        if self.hide_definitions.load(Ordering::SeqCst) {
            return Ok(None);
        }
        StateMachineDefinitionRepoPort::get(&*self.inner, id, version).await
    }
    async fn save_run_snapshot(&self, run: &StateMachineRun, version: i32, definition: &CollaborationDefinition,
        bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>, plan: Option<&StateMachineExecutionPlanSnapshot>) -> ServiceResult<()> {
        if self.fail_save.load(Ordering::SeqCst) {
            return Err(ServiceError::InternalError("injected snapshot write failure".into()));
        }
        self.inner.save_run_snapshot(run, version, definition, bindings, plan).await
    }
    async fn get_run_snapshot(&self, run_id: &str) -> ServiceResult<Option<StateMachineRunSnapshot>> {
        let mut snapshot = self.inner.get_run_snapshot(run_id).await?;
        if self.corrupt_read.load(Ordering::SeqCst) {
            if let Some(plan) = snapshot.as_mut().and_then(|snapshot| snapshot.execution_plan.as_mut()) {
                plan.content_hash = "0".repeat(64);
            }
        }
        Ok(snapshot)
    }
}

struct Harness {
    runtime: CollaborationRuntime,
    public_events: Arc<bcs_event_store::MemoryEventStore>,
    store: Arc<MemoryCollaborationStore>,
    definitions: Arc<SnapshotDefinitions>,
    delivery: Arc<RecordingDelivery>,
    channel: Arc<RecordingSessionChannelOutbound>,
    sessions: Arc<SessionManagementServiceImpl>,
}

impl Harness {
    async fn new(outcomes: &[&str]) -> Self {
        let group = Arc::new(GroupStore::new());
        group.upsert(test_group()).await.unwrap();
        let session_repo = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(session_repo.clone(), Arc::new(MemoryGroupRepo::new())));
        let public_events = Arc::new(bcs_event_store::MemoryEventStore::new());
        let store = Arc::new(MemoryCollaborationStore::new().with_session_repo(session_repo).with_event_store(public_events.clone()));
        let definitions = Arc::new(SnapshotDefinitions {
            inner: store.clone(), fail_save: AtomicBool::new(false), corrupt_read: AtomicBool::new(false),
            hide_definitions: AtomicBool::new(false),
        });
        let delivery = Arc::new(RecordingDelivery::default());
        let channel = Arc::new(RecordingSessionChannelOutbound::default());
        let decisions = outcomes.iter().map(|outcome| JudgeDecision {
            outcome: (*outcome).into(), reason: "test decision".into(), confidence: 1.0,
            checked_criteria: Vec::new(), retry_instruction: String::new(), raw_response: None,
        }).collect();
        let runtime = test_runtime!(definitions.clone(), store.clone(), store.clone(), store.clone(),
            group, sessions.clone(), delivery.clone(), Arc::new(SequencedJudge::new(decisions)))
            .with_session_channel_outbound(channel.clone())
            .with_loop_execution();
        Self { runtime, public_events, store, definitions, delivery, channel, sessions }
    }

    async fn start(&self, yaml: String, human: bool) -> bcs_service_api::StartStateMachineRunOutcome {
        self.runtime.start_state_machine_run(command(yaml, human)).await.unwrap()
    }

    async fn plan(&self, run_id: &str) -> StateMachineExecutionPlan {
        self.store.get_run_snapshot(run_id).await.unwrap().unwrap().execution_plan.unwrap().plan
    }

    async fn finish(&self, index: usize, run: &StateMachineRun, text: &str) -> bcs_service_api::HandleBotTerminalEventOutcome {
        let delivery_id = self.delivery.commands.lock().await[index].run_id.clone();
        complete_with_text(&self.runtime, &delivery_id, &run.session_id, text).await
    }

    async fn fail(&self, index: usize, run: &StateMachineRun) -> bcs_service_api::HandleBotTerminalEventOutcome {
        let delivery_id = self.delivery.commands.lock().await[index].run_id.clone();
        self.runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".into(), run_id: delivery_id.clone(), event_type: "chat.event".into(),
            event_payload: json!({"run_id": delivery_id, "state": "error", "error": "test delivery failure"}),
            state: ChatEventState::Error, bcs_session_id: Some(run.session_id.clone()),
        }).await.unwrap()
    }

    async fn prompt(&self, index: usize) -> String {
        let command = self.delivery.commands.lock().await[index].clone();
        chat_send_params(&command).message.content.iter()
            .filter_map(|block| block.text.as_deref()).collect::<Vec<_>>().join("\n")
    }
}

fn command(yaml: String, human: bool) -> StartStateMachineRunCommand {
    StartStateMachineRunCommand {
        group_id: "group-1".into(), session_id: None, definition_yaml: Some(yaml), definition: None,
        definition_ref: None, participant_bindings: None, opening_message_override: None,
        input: json!({"question": "iterate"}), caller_id: None,
        authenticated_human: human.then(|| AuthenticatedHumanCaller { actor_id: "human_1001".into(), display_name: None }),
    }
}

fn loop_yaml(iterations: u32, judged: bool, human: bool, attempts: u32) -> String {
    let mut work = json!({"kind": "bot_task", "display_name": "Work", "assignee": {"type": "bot_binding", "binding": "driver"}, "instruction": "Improve the previous result."});
    if human {
        work = json!({"kind": "human_input", "display_name": "Review", "assignee": {"type": "runtime_actor", "actor": "human_1001"}, "notification": {"mode": "direct_assignee"}, "instruction": "Review the previous result.", "node_timeout_ms": 60000});
    }
    if judged { work["judge"] = json!({"type": "llm", "criteria": ["Is another iteration needed?"], "outcomes": ["again", "done"]}); }
    let mut definition = json!({
        "api_version": "bcs.collaboration/v1", "id": "loop-test", "version": 1, "name": "Loop test",
        "participants": {"driver": {"bot_id": "driver-bot", "required": true}},
        "runtime": {"kind": "state_machine", "state_machine": {
            "version": 2, "graph_mode": "hierarchical", "defaults": {"max_attempts": attempts},
            "nodes": {
                "rounds": {"kind": "loop", "display_name": "Rounds", "loop": {
                    "mode": "fixed", "max_iterations": iterations, "entry_node": "work", "result_node": "work",
                    "continue_outcomes": [if judged { "again" } else { "complete" }],
                    "break_outcomes": if judged { json!(["done"]) } else { json!([]) },
                    "exhausted_outcome": "exhausted", "nodes": {"work": work}
                }, "transitions": {"exhausted": {"targets": ["publish"]}}},
                "publish": {"kind": "bot_task", "display_name": "Publish", "assignee": {"type": "bot_binding", "binding": "driver"}, "instruction": "Publish.", "final_output": true}
            }
        }}
    });
    if judged {
        definition["runtime"]["state_machine"]["nodes"]["rounds"]["transitions"] = json!({"done": {"targets": ["publish"]}, "exhausted": {"targets": ["fallback"]}});
        definition["runtime"]["state_machine"]["nodes"]["fallback"] = json!({"kind": "bot_task", "display_name": "Fallback", "assignee": {"type": "bot_binding", "binding": "driver"}, "instruction": "Resolve exhausted loop.", "transitions": {"complete": {"targets": ["publish"]}}});
    }
    if human { definition["runtime"]["state_machine"]["human_input_channel"] = json!({"channel_type": "dingtalk"}); }
    serde_yaml::to_string(&definition).unwrap()
}

fn iteration_id(plan: &StateMachineExecutionPlan, iteration: u32) -> String {
    plan.node_metadata.values().find(|meta| meta.iteration == Some(iteration)).unwrap().execution_node_id.clone()
}

#[tokio::test]
async fn empty_break_runs_all_iterations_then_continues_and_projects_previous_result_once() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(3, false, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    assert_eq!(started.view.nodes.len(), 4);
    assert!(!started.view.nodes.iter().any(|node| node.node_id == "rounds"));
    let first = h.prompt(0).await;
    assert!(first.contains("[Loop Context]\nloop_id: rounds\niteration: 1\nmax_iterations: 3\n\n[Previous Iteration Result]\n(none - this is the first iteration)"));
    for iteration in 1..=3 {
        let text = format!("result-{iteration}");
        let outcome = h.finish((iteration - 1) as usize, run, &text).await;
        assert_eq!(outcome.view.unwrap().run.status, StateMachineRunStatus::Running);
        let next_prompt = h.prompt(iteration as usize).await;
        if iteration < 3 {
            let source = h.store.get_node_run(&run.run_id, &iteration_id(&plan, iteration)).await.unwrap().unwrap();
            let expected = format!("[Loop Context]\nloop_id: rounds\niteration: {}\nmax_iterations: 3\n\n[Previous Iteration Result]\niteration: {iteration}\nresult_node_id: work\noutcome: complete\ncompleted_at: {}\noutput:\n{text}\n\n[Upstream Outputs]\n(none)\n", iteration + 1, source.completed_at.unwrap());
            assert!(next_prompt.contains(&expected), "{next_prompt}");
            assert_eq!(next_prompt.matches(&text).count(), 1);
        } else {
            assert!(!next_prompt.contains("[Loop Context]"));
            assert!(next_prompt.contains("result-3"));
            assert!(!next_prompt.contains("result-1"));
            assert!(!next_prompt.contains("result-2"));
        }
    }
    let finished = h.finish(3, run, "published").await.view.unwrap();
    assert_eq!(finished.run.status, StateMachineRunStatus::Completed);
    assert_eq!(finished.run.output.as_deref(), Some("published"));
    assert!(finished.nodes.iter().all(|node| node.attempt == 0 && node.status == StateMachineNodeStatus::Completed));
}

#[tokio::test]
async fn break_preserves_future_reachable_targets_then_skips_future_iterations_and_projects_only_selected_result() {
    let h = Harness::new(&["again", "done"]).await;
    let started = h.start(loop_yaml(3, true, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    h.finish(0, run, "first-continued").await;
    assert_eq!(h.store.get_node_run(&run.run_id, "publish").await.unwrap().unwrap().status, StateMachineNodeStatus::Pending);
    let outcome = h.finish(1, run, "second-converged").await.view.unwrap();
    assert_eq!(outcome.run.status, StateMachineRunStatus::Running);
    assert_eq!(h.store.get_node_run(&run.run_id, &iteration_id(&plan, 3)).await.unwrap().unwrap().status, StateMachineNodeStatus::Skipped);
    assert_eq!(h.store.get_node_run(&run.run_id, "fallback").await.unwrap().unwrap().status, StateMachineNodeStatus::Skipped);
    let prompt = h.prompt(2).await;
    assert!(prompt.contains("second-converged"));
    assert!(!prompt.contains("first-continued"));
    assert_eq!(h.finish(2, run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn judged_exhaustion_preserves_real_outcome_and_waits_for_the_outer_branch() {
    let h = Harness::new(&["again", "again"]).await;
    let started = h.start(loop_yaml(2, true, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    h.finish(0, run, "first").await;
    h.finish(1, run, "second").await;
    let last = h.store.get_node_run(&run.run_id, &iteration_id(&plan, 2)).await.unwrap().unwrap();
    assert_eq!(last.outcome.as_deref(), Some("again"));
    assert!(h.prompt(2).await.contains("Resolve exhausted loop."));
    assert_eq!(h.store.get_node_run(&run.run_id, "publish").await.unwrap().unwrap().status, StateMachineNodeStatus::Pending);
    h.finish(2, run, "fallback-output").await;
    let prompt = h.prompt(3).await;
    assert!(prompt.contains("fallback-output"));
    assert!(!prompt.contains("[ln-"), "unselected result artifacts must not leak: {prompt}");
    assert_eq!(h.finish(3, run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn human_entry_query_and_notification_share_context_and_reject_previous_round_reply() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(2, false, true, 1), true).await;
    let run = &started.view.run;
    assert!(h.delivery.commands.lock().await.is_empty());
    let query = ListPendingHumanNodesCommand { run_id: run.run_id.clone(), caller_actor_id: "human_1001".into() };
    let first = h.runtime.list_pending_human_nodes(query.clone()).await.unwrap().remove(0);
    assert_eq!(serde_json::to_value(&first).unwrap()["loop_context"]["previous_result"], Value::Null);
    let events = h.channel.events.lock().await;
    assert_eq!(serde_json::to_value(&first.loop_context).unwrap(), serde_json::to_value(&events[0].loop_context).unwrap());
    drop(events);
    let response = RespondHumanNodeCommand { run_id: run.run_id.clone(), node_id: first.node_id.clone(), caller_actor_id: "human_1001".into(), content: "human-first-result".into(), source: HumanResponseSource::Http };
    h.runtime.respond_human_node(response.clone()).await.unwrap();
    let second = h.runtime.list_pending_human_nodes(query).await.unwrap().remove(0);
    assert_ne!(first.response_ref, second.response_ref);
    assert!(second.upstream_artifacts.is_empty());
    let previous = second.loop_context.as_ref().unwrap().previous_result.as_ref().unwrap();
    assert_eq!(previous.execution_node_id, first.node_id);
    assert_eq!(previous.output, "human-first-result");
    assert_eq!(previous.outcome, "complete");
    let events = h.channel.events.lock().await;
    assert_eq!(events.len(), 2);
    assert_eq!(serde_json::to_value(&second.loop_context).unwrap(), serde_json::to_value(&events[1].loop_context).unwrap());
    drop(events);
    assert!(h.runtime.respond_human_node(response).await.is_err());
    assert!(h.runtime.respond_human_node(RespondHumanNodeCommand {
        run_id: run.run_id.clone(), node_id: second.node_id.clone(), caller_actor_id: "human_other".into(),
        content: "unauthorized".into(), source: HumanResponseSource::Http,
    }).await.is_err());
    assert!(h.delivery.commands.lock().await.is_empty());
    h.runtime.respond_human_node(RespondHumanNodeCommand { run_id: run.run_id.clone(), node_id: second.node_id, caller_actor_id: "human_1001".into(), content: "human-second-result".into(), source: HumanResponseSource::Http }).await.unwrap();
    assert!(h.prompt(0).await.contains("human-second-result"));
    assert_eq!(h.finish(0, run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn snapshot_and_opening_write_failures_prevent_all_dispatch() {
    let h = Harness::new(&[]).await;
    h.definitions.fail_save.store(true, Ordering::SeqCst);
    let error = h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)).await.err().unwrap();
    assert!(error.to_string().contains("snapshot"));
    assert!(h.delivery.commands.lock().await.is_empty());
    let h = Harness::new(&[]).await;
    let runtime = h.runtime.with_message_repo(Arc::new(FailingAppendMessageRepo::default()));
    let error = runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)).await.err().unwrap();
    assert!(error.to_string().contains("opening message write failed"));
    assert!(h.delivery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn retry_keeps_iteration_and_context_and_stale_completion_cannot_overwrite_previous_result() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(3, false, false, 2), false).await;
    let run = &started.view.run;
    h.finish(0, run, "immutable-first").await;
    let original = h.prompt(1).await;
    h.fail(1, run).await;
    assert_eq!(h.prompt(2).await, original);
    h.finish(0, run, "stale-overwrite").await;
    h.finish(1, run, "stale-attempt").await;
    assert_eq!(h.delivery.commands.lock().await.len(), 3);
    let view = h.finish(2, run, "second-after-retry").await.view.unwrap();
    let plan = h.plan(&run.run_id).await;
    assert_eq!(view.nodes.iter().find(|node| node.node_id == iteration_id(&plan, 2)).unwrap().attempt, 1);
    assert_eq!(view.nodes.iter().find(|node| node.node_id == iteration_id(&plan, 3)).unwrap().attempt, 0);
    assert!(h.prompt(3).await.contains("second-after-retry"));
    assert!(!h.prompt(3).await.contains("stale-"));
}

#[tokio::test]
async fn rerun_inherits_historical_plan_bindings_and_ids_with_new_attempt_zero_nodes() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let run = &started.view.run;
    let source = h.store.get_run_snapshot(&run.run_id).await.unwrap().unwrap();
    h.finish(0, run, "old-first").await;
    assert_eq!(h.fail(1, run).await.view.unwrap().run.status, StateMachineRunStatus::Failed);
    wait_for_callback_status(&h.sessions, &run.session_id, "not_applicable").await;
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    // Current compile limits cannot affect an existing plan, including rerun.
    let runtime = h.runtime.with_fixed_loop_limits(bcs_config_api::FixedLoopLimits { max_fixed_loop_iterations: 1, ..Default::default() });
    let rerun = runtime.rerun_state_machine_run(RerunStateMachineCommand { source_run_id: run.run_id.clone(), authenticated_human: None }).await.unwrap();
    assert!(rerun.created);
    assert_eq!(started.view.node_execution_metadata.as_ref().unwrap().len(), 2);
    assert_eq!(rerun.view.node_execution_metadata, started.view.node_execution_metadata);
    assert_ne!(rerun.view.run.run_id, run.run_id);
    assert_eq!(rerun.view.nodes.iter().map(|node| &node.node_id).collect::<Vec<_>>(), started.view.nodes.iter().map(|node| &node.node_id).collect::<Vec<_>>());
    assert!(rerun.view.nodes.iter().all(|node| node.attempt == 0 && node.artifact_text.is_none() && node.outcome.is_none()));
    let copied = h.store.get_run_snapshot(&rerun.view.run.run_id).await.unwrap().unwrap();
    assert_eq!(copied.execution_plan.as_ref().unwrap().content_hash, source.execution_plan.as_ref().unwrap().content_hash);
    assert_eq!(serde_json::to_value(&copied.resolved_participant_bindings).unwrap(), serde_json::to_value(&source.resolved_participant_bindings).unwrap());
    let request = h.delivery.commands.lock().await.last().unwrap().clone();
    let prompt = chat_send_params(&request).message.content.iter().filter_map(|block| block.text.as_deref()).collect::<Vec<_>>().join("\n");
    assert!(prompt.contains("iteration: 1\nmax_iterations: 2"));
    assert!(!prompt.contains("old-first"));
}

#[tokio::test]
async fn corrupt_snapshot_stops_terminal_progression_and_human_context_queries() {
    for human in [false, true] {
        let h = Harness::new(&[]).await;
        let started = h.start(loop_yaml(2, false, human, 1), human).await;
        let run = &started.view.run;
        h.definitions.corrupt_read.store(true, Ordering::SeqCst);
        let error = if human {
            h.runtime.list_pending_human_nodes(ListPendingHumanNodesCommand {
                run_id: run.run_id.clone(), caller_actor_id: "human_1001".into(),
            }).await.err().unwrap()
        } else {
            let delivery_id = h.delivery.commands.lock().await[0].run_id.clone();
            h.runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
                bot_id: "driver-bot".into(), run_id: delivery_id.clone(), event_type: "chat.event".into(),
                event_payload: json!({"state": "final", "run_id": delivery_id, "message": {"content": [{"type": "text", "text": "must not advance"}]}}),
                state: ChatEventState::Final, bcs_session_id: Some(run.session_id.clone()),
            }).await.err().unwrap()
        };
        assert!(error.to_string().contains("snapshot"));
        let nodes = h.store.list_node_runs(&run.run_id).await.unwrap();
        assert_eq!(nodes.iter().filter(|node| node.status == StateMachineNodeStatus::Running).count(), 1);
        assert!(!nodes.iter().any(|node| node.status == StateMachineNodeStatus::Completed));
        assert_eq!(h.delivery.commands.lock().await.len(), if human { 0 } else { 1 });
        assert_eq!(h.channel.events.lock().await.len(), if human { 1 } else { 0 });
    }
}

#[tokio::test]
async fn invalid_judge_outcome_fails_without_dispatching_another_iteration() {
    let h = Harness::new(&["not-declared"]).await;
    let started = h.start(loop_yaml(3, true, false, 1), false).await;
    let failed = h.finish(0, &started.view.run, "candidate").await.view.unwrap();
    assert_eq!(failed.run.status, StateMachineRunStatus::Failed);
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert!(!failed.nodes.iter().any(|node| node.status == StateMachineNodeStatus::Completed));
}

#[tokio::test]
async fn cancel_preserves_completed_iteration_and_ignores_late_terminal_events() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(3, false, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    h.finish(0, run, "completed-audit").await;
    let cancelled = h.runtime.cancel_state_machine_run(bcs_service_api::CancelStateMachineRunCommand {
        run_id: run.run_id.clone(), reason: Some("user cancelled".into()),
    }).await.unwrap();
    assert_eq!(cancelled.run.status, StateMachineRunStatus::Aborted);
    let late = h.finish(1, run, "late-result").await.view.unwrap();
    assert_eq!(late.run.status, StateMachineRunStatus::Aborted);
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    let first = late.nodes.iter().find(|node| node.node_id == iteration_id(&plan, 1)).unwrap();
    assert_eq!(first.status, StateMachineNodeStatus::Completed);
    assert_eq!(first.artifact_text.as_deref(), Some("completed-audit"));
    assert!(!late.nodes.iter().any(|node| node.artifact_text.as_deref() == Some("late-result")));
}

#[tokio::test]
async fn timeout_retries_same_bot_iteration_and_fails_human_without_advancing() {
    for human in [false, true] {
        let h = Harness::new(&[]).await;
        let mut definition: Value = serde_yaml::from_str(&loop_yaml(2, false, human, 2)).unwrap();
        definition["runtime"]["state_machine"]["nodes"]["rounds"]["loop"]["nodes"]["work"]["node_timeout_ms"] = json!(1000);
        let started = h.start(serde_yaml::to_string(&definition).unwrap(), human).await;
        let run = &started.view.run;
        let plan = h.plan(&run.run_id).await;
        let deadline = started.view.nodes.iter().find_map(|node| node.timeout_deadline_ms).unwrap();
        tokio::time::sleep(Duration::from_millis(deadline.saturating_sub(bcs_protocol::now_ms()) + 5)).await;
        assert_eq!(h.runtime.process_expired_node_timeouts(10, 0).await.unwrap(), 1);
        let view = h.runtime.get_state_machine_run(&run.run_id).await.unwrap().unwrap();
        let first = view.nodes.iter().find(|node| node.node_id == iteration_id(&plan, 1)).unwrap();
        assert_eq!(first.attempt, if human { 0 } else { 1 });
        assert_eq!(view.run.status, if human { StateMachineRunStatus::Failed } else { StateMachineRunStatus::Running });
        assert_eq!(h.delivery.commands.lock().await.len(), if human { 0 } else { 2 });
        if !human {
            assert_eq!(h.prompt(0).await, h.prompt(1).await);
            let deadline = first.timeout_deadline_ms.unwrap();
            tokio::time::sleep(Duration::from_millis(deadline.saturating_sub(bcs_protocol::now_ms()) + 5)).await;
            assert_eq!(h.runtime.process_expired_node_timeouts(10, 0).await.unwrap(), 1);
            assert_eq!(h.runtime.get_state_machine_run(&run.run_id).await.unwrap().unwrap().run.status, StateMachineRunStatus::Failed);
        }
        assert_eq!(h.store.get_node_run(&run.run_id, &iteration_id(&plan, 2)).await.unwrap().unwrap().status, StateMachineNodeStatus::Pending);
    }
}

#[tokio::test]
async fn one_shot_loop_uses_session_bindings_and_does_not_persist_a_group_definition() {
    let mut h = Harness::new(&[]).await;
    h.runtime = h.runtime.with_result_publisher(Arc::new(RecordingResultPublisher::default()));
    let session = h.sessions.create_or_reactivate(CreateOrReactivateCommand {
        group_id: "group-1".into(), session_id: None,
        params: NewSessionParams { session_kind: SessionKind::Chat, participants: test_group().participants, ..Default::default() },
    }).await.unwrap().session;
    let mut definition: Value = serde_yaml::from_str(&loop_yaml(2, false, false, 1)).unwrap();
    for field in ["api_version", "id", "version"] { definition.as_object_mut().unwrap().remove(field); }
    definition["participants"]["driver"].as_object_mut().unwrap().remove("bot_id");
    let started = h.runtime.start_session_state_machine_run(StartSessionStateMachineRunCommand {
        session_id: session.id.clone(), caller_bot_id: "driver-bot".into(),
        definition_yaml: serde_yaml::to_string(&definition).unwrap(),
        participant_bindings: BTreeMap::from([("driver".into(), RuntimeParticipantBinding {
            source: "manual".into(), bot_ids: vec!["driver-bot".into()], extensions: Default::default(),
        })]),
        opening_message: None, input: Value::Null, judge_available: false,
    }).await.unwrap();
    let run = &started.view.run;
    assert_eq!(run.session_id, session.id);
    assert_eq!(started.view.nodes.len(), 3);
    assert!(StateMachineDefinitionRepoPort::get(&*h.store, &run.definition_id, run.definition_version).await.unwrap().is_none());
    assert!(GroupRuntimeBindingRepoPort::get(&*h.store, "group-1").await.unwrap().is_none());
    h.finish(0, run, "first").await;
    h.finish(1, run, "second").await;
    assert_eq!(h.finish(2, run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn parallel_body_join_and_next_iteration_use_the_designated_result() {
    for reverse in [false, true] {
        let h = Harness::new(&[]).await;
        let mut definition: Value = serde_yaml::from_str(&loop_yaml(2, false, false, 1)).unwrap();
        let loop_def = &mut definition["runtime"]["state_machine"]["nodes"]["rounds"]["loop"];
        let work = loop_def["nodes"]["work"].clone();
        loop_def["result_node"] = json!("result");
        loop_def["nodes"] = json!({"work": work.clone(), "left": work.clone(), "right": work.clone(), "result": work});
        loop_def["nodes"]["work"]["transitions"] = json!({"complete": {"targets": ["left", "right"]}});
        for branch in ["left", "right"] { loop_def["nodes"][branch]["transitions"] = json!({"complete": {"targets": ["result"]}}); }
        let started = h.start(serde_yaml::to_string(&definition).unwrap(), false).await;
        let metadata = started.view.node_execution_metadata.as_ref().unwrap();
        assert_eq!(metadata.len(), 8, "all four body nodes in both iterations must be projected");
        for iteration in 1..=2 {
            let names: std::collections::BTreeSet<_> = metadata.values().filter(|meta| meta.iteration == iteration)
                .map(|meta| meta.definition_node_id.as_str()).collect();
            assert_eq!(names, std::collections::BTreeSet::from(["work", "left", "right", "result"]));
        }
        let run = &started.view.run;
        h.finish(0, run, "entry-output").await;
        assert_eq!(h.delivery.commands.lock().await.len(), 3);
        let (first, second) = if reverse { (2, 1) } else { (1, 2) };
        h.finish(first, run, "first-parallel-output").await;
        assert_eq!(h.delivery.commands.lock().await.len(), 3, "join must wait for both branches");
        h.finish(second, run, "last-parallel-output").await;
        let join_prompt = h.prompt(3).await;
        assert!(join_prompt.contains("first-parallel-output") && join_prompt.contains("last-parallel-output"));
        assert!(!join_prompt.contains("[Loop Context]"));
        h.finish(3, run, "designated-result").await;
        let next = h.prompt(4).await;
        assert!(next.contains("result_node_id: result"));
        assert_eq!(next.matches("designated-result").count(), 1);
        assert!(!next.contains("parallel-output"));
    }
}

#[tokio::test]
async fn configured_definition_keeps_authoring_loops_and_starts_with_compiled_nodes() {
    let h = Harness::new(&[]).await;
    let configured = h.runtime.configure_group_runtime(ConfigureGroupRuntimeCommand {
        group_id: "group-1".into(), definition_yaml: Some(loop_yaml(2, false, false, 1)),
        definition: None, definition_ref: None, participant_bindings: BTreeMap::new(),
        auto_start_on_service_invocation: true,
    }).await.unwrap();
    let reference = configured.default_definition.unwrap();
    let saved = StateMachineDefinitionRepoPort::get(&*h.store, &reference.id, reference.version).await.unwrap().unwrap();
    let value = serde_json::to_value(saved).unwrap();
    assert_eq!(value["runtime"]["state_machine"]["version"], 2);
    assert_eq!(value["runtime"]["state_machine"]["nodes"]["rounds"]["kind"], "loop");
    let mut start = command(String::new(), false);
    start.definition_yaml = None;
    let started = h.runtime.start_state_machine_run(start).await.unwrap();
    assert_eq!(started.view.nodes.len(), 3);
    assert_eq!(h.plan(&started.view.run.run_id).await.state_machine.nodes.len(), 3);
    h.finish(0, &started.view.run, "first").await;
    h.finish(1, &started.view.run, "second").await;
    assert_eq!(h.finish(2, &started.view.run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn consecutive_loops_keep_independent_contexts_and_project_the_selected_outer_edge() {
    let h = Harness::new(&[]).await;
    let mut definition: Value = serde_yaml::from_str(&loop_yaml(2, false, false, 1)).unwrap();
    let nodes = &mut definition["runtime"]["state_machine"]["nodes"];
    nodes["second_loop"] = nodes["rounds"].clone();
    nodes["rounds"]["transitions"]["exhausted"]["targets"] = json!(["second_loop"]);
    let started = h.start(serde_yaml::to_string(&definition).unwrap(), false).await;
    let run = &started.view.run;
    h.finish(0, run, "first-loop-iteration-1").await;
    h.finish(1, run, "first-loop-exhausted").await;
    let entry = h.prompt(2).await;
    assert!(entry.contains("loop_id: second_loop\niteration: 1"));
    assert!(entry.contains("(none - this is the first iteration)"));
    assert!(entry.contains("first-loop-exhausted"));
    assert!(!entry.contains("first-loop-iteration-1"));
    h.finish(2, run, "second-loop-iteration-1").await;
    let next = h.prompt(3).await;
    assert!(next.contains("loop_id: second_loop\niteration: 2"));
    assert!(next.contains("second-loop-iteration-1"));
    assert!(!next.contains("first-loop-"));
    h.finish(3, run, "second-loop-exhausted").await;
    assert_eq!(h.finish(4, run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn concurrent_loop_rerun_creates_one_child_and_dispatches_its_first_node_once() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let run = &started.view.run;
    h.fail(0, run).await;
    wait_for_callback_status(&h.sessions, &run.session_id, "not_applicable").await;
    let command = RerunStateMachineCommand { source_run_id: run.run_id.clone(), authenticated_human: None };
    let (first, second) = tokio::join!(
        h.runtime.rerun_state_machine_run(command.clone()),
        h.runtime.rerun_state_machine_run(command),
    );
    let first = first.unwrap();
    let second = second.unwrap();
    assert_eq!(first.view.run.run_id, second.view.run.run_id);
    assert_ne!(first.created, second.created);
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    assert_eq!(serde_json::to_value(h.plan(&run.run_id).await).unwrap(), serde_json::to_value(h.plan(&first.view.run.run_id).await).unwrap());
}

#[path = "history_loop.rs"]
mod history_loop;
