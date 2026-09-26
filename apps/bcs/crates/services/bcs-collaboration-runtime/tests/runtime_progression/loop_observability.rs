use super::*;
use bcs_domain::StateMachineLoopRouteKind;
use bcs_service_api::{StateMachineLoopInstrumentationHook, StateMachineLoopMetric as Metric, StateMachineLoopOutcome};

#[derive(Default)]
pub(super) struct RecordingLoopMetrics(pub(super) std::sync::Mutex<Vec<Metric>>);

impl StateMachineLoopInstrumentationHook for RecordingLoopMetrics {
    fn record(&self, metric: Metric) { self.0.lock().unwrap().push(metric); }
}

fn completed(route: StateMachineLoopRouteKind, outcome: &str) -> Metric {
    Metric::IterationCompleted { route, outcome: StateMachineLoopOutcome::from_outcome(outcome) }
}

#[tokio::test]
async fn loop_observations_log_retry_and_stale_attempt_without_recounting() {
    use tracing::instrument::WithSubscriber;
    let buffer = SharedLogBuffer::default();
    let subscriber = tracing_subscriber::fmt().json().with_ansi(false).with_writer(buffer.clone()).finish();
    async {
        let mut h = Harness::new(&[]).await;
        let metrics = Arc::new(RecordingLoopMetrics::default());
        h.runtime = h.runtime.with_loop_instrumentation(Some(metrics.clone()));
        let run = h.start(loop_yaml(2, false, false, 2), false).await.view.run;
        h.fail(0, &run).await;
        h.finish(0, &run, "late private artifact").await;
        h.runtime.cancel_state_machine_run(bcs_service_api::CancelStateMachineRunCommand {
            run_id: run.run_id.clone(), reason: None,
        }).await.unwrap();
        h.finish(1, &run, "cancelled private artifact").await;
        assert_eq!(*metrics.0.lock().unwrap(), [Metric::IterationStarted]);
    }.with_subscriber(subscriber).await;
    let logs = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
    let transitions: Vec<Value> = logs.lines().filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .filter(|event| event["fields"]["message"] == "state_machine: loop node transition").collect();
    assert!(transitions.iter().any(|event| event["fields"]["transition"] == "retry_scheduled"
        && event["fields"]["attempt"] == 1 && event["fields"]["loop_iteration"] == 1));
    assert!(transitions.iter().any(|event| event["fields"]["transition"] == "event_ignored"
        && event["fields"]["attempt"] == 0 && event["fields"]["loop_iteration"] == 1));
    assert!(!serde_json::to_string(&transitions).unwrap().contains("private artifact"));
}

#[tokio::test]
async fn loop_observations_follow_judged_human_transitions_and_preserve_log_identity() {
    use tracing::instrument::WithSubscriber;
    let buffer = SharedLogBuffer::default();
    let subscriber = tracing_subscriber::fmt().json().with_ansi(false).with_writer(buffer.clone()).finish();
    let (run, plan) = async {
        let mut h = Harness::new(&["again", "done"]).await;
        let metrics = Arc::new(RecordingLoopMetrics::default());
        h.runtime = h.runtime.with_loop_instrumentation(Some(metrics.clone()));
        let run = h.start(loop_yaml(3, true, true, 1), true).await.view.run;
        let plan = h.plan(&run.run_id).await;
        for iteration in [1, 2] {
            h.runtime.respond_human_node(RespondHumanNodeCommand {
                run_id: run.run_id.clone(), node_id: iteration_id(&plan, iteration),
                caller_actor_id: "human_1001".into(), content: "private human artifact".into(),
                source: HumanResponseSource::Http,
            }).await.unwrap();
        }
        assert_eq!(*metrics.0.lock().unwrap(), [Metric::IterationStarted,
            completed(StateMachineLoopRouteKind::Continue, "again"), Metric::IterationStarted,
            completed(StateMachineLoopRouteKind::Break, "done")]);
        (run, plan)
    }.with_subscriber(subscriber).await;
    let logs = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
    let transitions: Vec<Value> = logs.lines().filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .filter(|event| event["fields"]["message"] == "state_machine: loop node transition").collect();
    assert_eq!(transitions.len(), 4);
    let fields = &transitions.last().unwrap()["fields"];
    assert_eq!(fields["run_id"], run.run_id);
    assert_eq!(fields["loop_id"], "rounds");
    assert_eq!(fields["loop_iteration"], 2);
    assert_eq!(fields["loop_max_iterations"], 3);
    assert_eq!(fields["definition_node_id"], "work");
    assert_eq!(fields["execution_node_id"], iteration_id(&plan, 2));
    assert_eq!(fields["attempt"], 0);
    assert_eq!(fields["selected_outcome"], "done");
    assert!(!serde_json::to_string(&transitions).unwrap().contains("private human artifact"));
}

#[tokio::test]
async fn loop_observations_count_only_the_entry_and_result_in_a_multi_node_body() {
    let mut h = Harness::new(&[]).await;
    let metrics = Arc::new(RecordingLoopMetrics::default());
    h.runtime = h.runtime.with_loop_instrumentation(Some(metrics.clone()));
    let mut yaml: Value = serde_yaml::from_str(&loop_yaml(1, false, false, 1)).unwrap();
    let body = &mut yaml["runtime"]["state_machine"]["nodes"]["rounds"]["loop"];
    body["nodes"]["result"] = body["nodes"]["work"].clone();
    body["nodes"]["work"]["transitions"] = json!({"complete": {"targets": ["result"]}});
    body["result_node"] = json!("result");
    let nodes = &mut yaml["runtime"]["state_machine"]["nodes"];
    for branch in ["left", "right"] {
        nodes[branch] = nodes["publish"].clone();
        nodes[branch]["final_output"] = json!(false);
        nodes[branch]["transitions"] = json!({"complete": {"targets": ["publish"]}});
    }
    nodes["rounds"]["transitions"]["exhausted"]["targets"] = json!(["left", "right"]);
    let run = h.start(serde_yaml::to_string(&yaml).unwrap(), false).await.view.run;
    h.finish(0, &run, "entry result").await;
    assert_eq!(*metrics.0.lock().unwrap(), [Metric::IterationStarted]);
    h.finish(1, &run, "loop result").await;
    assert_eq!(h.delivery.commands.lock().await.len(), 4, "result fans out to both ordinary nodes");
    assert_eq!(*metrics.0.lock().unwrap(), [Metric::IterationStarted,
        completed(StateMachineLoopRouteKind::Exhausted, "complete")]);
}

#[tokio::test]
async fn loop_observations_preserve_v1_and_classify_compile_rejections_without_error_text() {
    use bcs_service_api::StateMachineLoopCompileRejection as Reason;
    let mut h = Harness::new(&[]).await;
    let metrics = Arc::new(RecordingLoopMetrics::default());
    h.runtime = h.runtime.with_loop_instrumentation(Some(metrics.clone()));
    let fixture = include_str!("../fixtures/fixed_loop.yaml");
    let mut definition: Value = serde_yaml::from_str(fixture).unwrap();
    for (limits, reason) in [
        (bcs_config_api::FixedLoopLimits { max_fixed_loop_iterations: 1, ..Default::default() }, Reason::ResourceLimit),
        (bcs_config_api::FixedLoopLimits { max_fixed_loop_body_nodes: 1, ..Default::default() }, Reason::ResourceLimit),
        (bcs_config_api::FixedLoopLimits { max_compiled_state_machine_nodes: 1, ..Default::default() }, Reason::ResourceLimit),
        (bcs_config_api::FixedLoopLimits { max_compiled_state_machine_bytes: 1, ..Default::default() }, Reason::ResourceLimit),
    ] {
        h.runtime = h.runtime.with_fixed_loop_limits(limits);
        let outcome = h.runtime.validate_definition_yaml(bcs_service_api::ValidateCollaborationDefinitionYamlCommand {
            definition_yaml: fixture.into(), judge_available: true,
        }).await.unwrap();
        assert!(!outcome.valid);
        assert_eq!(metrics.0.lock().unwrap().last(), Some(&Metric::CompileRejected { reason }));
    }
    h.runtime = h.runtime.with_fixed_loop_limits(Default::default());
    definition["runtime"]["state_machine"]["graph_mode"] = json!("acyclic");
    let invalid = h.runtime.validate_definition_yaml(bcs_service_api::ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: serde_yaml::to_string(&definition).unwrap(), judge_available: true,
    }).await.unwrap();
    assert!(!invalid.valid);
    assert_eq!(metrics.0.lock().unwrap().len(), 5);
    assert_eq!(metrics.0.lock().unwrap().last(), Some(&Metric::CompileRejected { reason: Reason::InvalidDefinition }));
    // Ordinary v1 executes without any Loop observations.
    definition["runtime"]["state_machine"]["version"] = json!(1);
    let final_node = definition["runtime"]["state_machine"]["nodes"]["publish"].clone();
    definition["runtime"]["state_machine"]["nodes"] = json!({"publish": final_node});
    definition["participants"] = json!({"coordinator": {"bot_id": "driver-bot", "required": true}});
    let run = h.start(serde_yaml::to_string(&definition).unwrap(), false).await.view.run;
    h.finish(0, &run, "v1 output").await;
    assert_eq!(metrics.0.lock().unwrap().len(), 5);
}

#[tokio::test]
async fn loop_observations_count_committed_iterations_not_retries_or_stale_events() {
    let mut h = Harness::new(&[]).await;
    let metrics = Arc::new(RecordingLoopMetrics::default());
    h.runtime = h.runtime.with_loop_instrumentation(Some(metrics.clone()));
    let run = h.start(loop_yaml(2, false, false, 2), false).await.view.run;
    assert_eq!(*metrics.0.lock().unwrap(), [Metric::IterationStarted]);
    h.fail(0, &run).await;
    h.finish(0, &run, "late old attempt").await;
    assert_eq!(*metrics.0.lock().unwrap(), [Metric::IterationStarted]);
    h.finish(1, &run, "first").await;
    h.finish(1, &run, "duplicate").await;
    h.finish(2, &run, "last").await;
    h.finish(3, &run, "published").await;
    assert_eq!(*metrics.0.lock().unwrap(), [
        Metric::IterationStarted,
        Metric::IterationCompleted { route: StateMachineLoopRouteKind::Continue, outcome: StateMachineLoopOutcome::Complete },
        Metric::IterationStarted,
        Metric::IterationCompleted { route: StateMachineLoopRouteKind::Exhausted, outcome: StateMachineLoopOutcome::Complete },
    ]);
}
