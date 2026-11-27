#![allow(clippy::unwrap_used, reason = "test fixtures and assertions")]
use bcs_direct_a2a_runner::{DirectA2aRunner, DirectA2aState};
use bcs_dist_encoding::digest_json;
use bcs_dist_model::*;
use bcs_service_api::core::dist_runner::*;
use bcs_test_support::contract::distributed::services::collaboration_mode_runner_service_contract_tests;
use serde_json::{Value, json};

fn tid() -> Tid {
    Tid("task".into())
}

fn spec() -> SessionSpec {
    let agent = AgentId("bot".into());
    let config = json!({"agent": agent});
    SessionSpec {
        schema_version: 1,
        sid: Sid("session".into()),
        shard: ShardId(0),
        scope: TaskScope {
            tenant: TenantId("tenant".into()),
            principal: PrincipalId("bot:caller".into()),
        },
        target: SessionTarget::DirectAgent { agent: agent.clone() },
        mode_envelope: ModeConfigEnvelope {
            mode: CollaborationMode::DirectA2a,
            mode_version: 1,
            revision: "1".into(),
            digest: digest_json(&config),
            payload: Payload::Inline(config),
        },
        authorized_agents: vec![AuthorizedAgent { agent, pool: PoolId("pool".into()) }],
        limits: SessionLimits {
            max_queued_tasks: 2,
            max_task_steps: 100,
            task_timeout_millis: 1000,
            max_inline_bytes: 65536,
        },
        digest: Digest("test".into()),
        created_at: TimestampMillis(0),
    }
}

fn event(kind: AgentEventKind, value: Value) -> RunnerInput {
    RunnerInput::Agent {
        tid: tid(),
        event: AgentEvent {
            did: Did("task.agent.1".into()),
            holder_epoch: HolderEpoch(1),
            seq: InputSeq(1),
            kind,
            remote_event_id: None,
            payload: Payload::Inline(value),
        },
    }
}

fn chunk(value: &str, append: bool) -> RunnerInput {
    event(
        AgentEventKind::Delta,
        json!({
            "artifact":{"artifactId":"a", "parts":[{"text": value}]}, "append": append,
        }),
    )
}

struct Task {
    spec: SessionSpec,
    config: AuthorizedAgent,
    state: DirectA2aState,
}

impl Task {
    async fn new() -> Self {
        let spec = spec();
        let Payload::Inline(raw) = &spec.mode_envelope.payload else { panic!("expected inline config") };
        let config = DirectA2aRunner
            .parse_and_validate_config(
                raw,
                &spec.authorized_agents,
                &spec.limits,
            )
            .unwrap();
        let state = DirectA2aRunner
            .init_task(&spec, &config, &tid(), 2, TimestampMillis(100))
            .await
            .unwrap();
        Self { spec, config, state }
    }

    async fn reduce(&mut self, input: RunnerInput) -> TypedDecision<DirectA2aState> {
        let decision = DirectA2aRunner
            .reduce_task(
                &self.spec,
                &self.config,
                &tid(),
                2,
                &self.state,
                &[input],
                TimestampMillis(100),
            )
            .await
            .unwrap();
        // Exercise the same persisted representation a different Worker would restore.
        self.state =
            serde_json::from_value(serde_json::to_value(&decision.next_state).unwrap()).unwrap();
        decision
    }

    async fn start(&mut self) -> Deadline {
        let payload = Payload::Inline(json!({"parts":[{"text":"hello"}]}));
        let d = self.reduce(RunnerInput::Start { tid: tid(), payload: payload.clone() }).await;
        let [ActionIntent::StartDispatch(call)] = &d.actions[..] else {
            panic!("expected a single dispatch")
        };
        assert_eq!(call.agent, self.config.agent);
        assert_eq!(call.pool, self.config.pool);
        assert_eq!(call.source.0, "agent");
        assert_eq!(call.did.0, "task.agent.1");
        assert_eq!(call.downstream_context.as_deref(), Some("session:2"));
        assert_eq!(call.request, payload);
        assert_eq!(call.deadline, TimestampMillis(1100));
        assert_eq!(d.next_deadlines.len(), 1);
        d.next_deadlines[0].clone()
    }
}

fn assert_show(action: &ActionIntent, expected: ArtifactUpdate) {
    assert!(
        matches!(action, ActionIntent::Show { artifact_id, update, class: OutputClass::Output, .. }
        if artifact_id.0 == "answer" && update == &expected),
        "{action:?}"
    );
}

fn upsert(text: &str) -> ArtifactUpdate {
    ArtifactUpdate::Upsert { content: Payload::Inline(json!(text)) }
}

#[tokio::test]
async fn delta_snapshot_and_authoritative_final_share_one_output() {
    let mut task = Task::new().await;
    let deadline = task.start().await;
    let first = task.reduce(chunk("hello", true)).await;
    assert_show(&first.actions[0], ArtifactUpdate::AppendText { delta: Payload::Inline(json!("hello")) });
    assert_eq!(first.next_deadlines, vec![deadline]);
    let d = task.reduce(chunk("!", true)).await;
    assert_show(&d.actions[0], ArtifactUpdate::AppendText { delta: Payload::Inline(json!("!")) });
    let d = task.reduce(chunk("replacement", false)).await;
    assert_show(&d.actions[0], upsert("replacement"));
    let d = task
        .reduce(event(
            AgentEventKind::Snapshot,
            json!({"task":{"artifacts":[
                {"artifactId":"a","parts":[{"text":"recovered"}]}
            ]}}),
        ))
        .await;
    assert_show(&d.actions[0], upsert("recovered"));
    let d = task
        .reduce(event(
            AgentEventKind::Final,
            json!({
                "state":"completed", "artifacts":[{"artifactId":"a","parts":[{"text":"final"}]}]
            }),
        ))
        .await;
    assert_show(&d.actions[0], upsert("final"));
    assert!(matches!(
        d.actions[1],
        ActionIntent::FinishTask { status: TerminalStatus::Completed, .. }
    ));
    assert!(d.next_deadlines.is_empty());
}

#[tokio::test]
async fn status_only_and_message_final_preserve_stream() {
    for (value, output) in [
        (json!({"state":"completed"}), None),
        (json!({"state":"completed", "artifacts":[]}), None),
        (json!({"state":"completed", "message":{"parts":[{"text":"Done"}]}}), None),
    ] {
        let mut task = Task::new().await;
        task.start().await;
        task.reduce(chunk("partial", false)).await;
        let d = task.reduce(event(AgentEventKind::Final, value)).await;
        if let Some(text) = output {
            assert_show(&d.actions[0], upsert(text));
        }
        assert_eq!(d.actions.len(), 1 + usize::from(output.is_some()));
        assert!(matches!(
            d.actions.last(),
            Some(ActionIntent::FinishTask { status: TerminalStatus::Completed, .. })
        ));
    }
}

#[tokio::test]
async fn mixed_parts_survive_stream_projection_and_snapshot() {
    use bcs_dist_projection::OutputProjectionService;
    use bcs_service_api::{core::dist_projection::OutputProjectionCoreService, port::dist_output::DistOutputAdapterPort};
    let file = json!({"url":"https://example.com/report.pdf", "mediaType":"application/pdf", "metadata":{"label":"report"}});
    let text = json!({"text":"caption", "metadata":{"language":"en"}});
    let data = json!({"data":{"count":1}});
    for parts in [vec![file.clone(), text.clone(), data.clone()], vec![text.clone(), file.clone(), data.clone()]] {
        let mut task = Task::new().await;
        task.start().await;
        let mut display = DisplaySnapshot { format_version: 1, units: vec![] };
        let mut expected = vec![];
        for part in parts {
            let decision = task.reduce(event(AgentEventKind::Delta, json!({
                "artifact":{"artifactId":"a", "parts":[part.clone()]}, "append":true
            }))).await;
            for action in decision.actions {
                let ActionIntent::Show { source, artifact_id, class, update, .. } = action else { panic!() };
                let change = ArtifactChange { source, artifact_id, class, update };
                let wire = bcs_dist_a2a_output::A2aOutputAdapter.artifact(&change).unwrap();
                assert_eq!(wire["artifact"]["parts"], json!([part]));
                display = OutputProjectionService.fold_committed(&display, &[change], &PHASE_ONE_OUTPUT_LIMITS).unwrap();
            }
            expected.push(part);
            let wire = bcs_dist_a2a_output::A2aOutputAdapter.snapshot(&serde_json::to_value(&display).unwrap()).unwrap();
            assert_eq!(wire["artifacts"][0]["parts"], json!(expected));
        }
        let final_decision = task.reduce(event(AgentEventKind::Final, json!({"state":"completed"}))).await;
        assert_eq!(final_decision.actions.len(), 1);
        assert!(serde_json::to_string(&task.state).unwrap().len() < 512);
    }
}

#[tokio::test]
async fn message_only_final_supplies_an_answer() {
    let mut task = Task::new().await;
    task.start().await;
    let decision = task.reduce(event(AgentEventKind::Final, json!({
        "state":"completed", "message":{"parts":[{"text":"answer"}]}
    }))).await;
    assert_show(&decision.actions[0], upsert("answer"));
    assert_eq!(decision.actions.len(), 2);
}

#[tokio::test]
async fn questions_preserve_prompts_and_forward_only_the_matching_reply() {
    let mut task = Task::new().await;
    task.start().await;
    for seq in 1..=2 {
        let id = InteractionId(format!("question-{seq}"));
        let (kind, status) = if seq == 1 {
            (AgentEventKind::InputRequired { interaction: id.clone() }, TaskStatus::InputRequired)
        } else {
            (AgentEventKind::AuthRequired { interaction: id.clone() }, TaskStatus::AuthRequired)
        };
        let d = task
            .reduce(event(kind, json!({"message":{"parts":[{"text":"Please confirm"}]}})))
            .await;
        let [ActionIntent::SetStatus { status: waiting, interactions, .. }] = d.actions.as_slice()
        else {
            panic!()
        };
        assert_eq!(waiting, &status);
        assert_eq!(interactions[0].id, id);
        assert_eq!(interactions[0].prompt.as_deref(), Some("Please confirm"));
        assert_eq!(
            interactions[0].origin,
            InteractionOrigin::Dispatch { did: Did("task.agent.1".into()) }
        );
        let reply = RunnerInput::HumanReply {
            tid: tid(),
            interaction: id,
            responder: PrincipalId("bot:caller".into()),
            payload: Payload::Inline(json!({"parts":[{"text":"yes"}]})),
        };
        let d = task.reduce(reply.clone()).await;
        let [
            ActionIntent::ContinueDispatch { did, input },
            ActionIntent::SetStatus { status: TaskStatus::Working, .. },
        ] = d.actions.as_slice()
        else {
            panic!()
        };
        assert_eq!(did.0, "task.agent.1");
        assert_eq!(input.seq.0, seq);
        assert_eq!(input.input_id.0, format!("task.agent.1:in:{seq}"));
        assert!(task.reduce(reply).await.actions.is_empty());
    }
}

#[tokio::test]
async fn timeout_cancels_once_and_remote_error_finishes_immediately() {
    let mut task = Task::new().await;
    let timeout = task.start().await;
    let d = task.reduce(RunnerInput::Timeout(timeout)).await;
    assert!(matches!(
        d.actions.as_slice(),
        [
            ActionIntent::CancelDispatch { .. },
            ActionIntent::FinishTask { status: TerminalStatus::Failed, .. },
        ]
    ));
    assert!(d.next_deadlines.is_empty());
    let mut task = Task::new().await;
    task.start().await;
    let d = task
        .reduce(event(AgentEventKind::Error, json!({"state":"failed","code":"agent_failed"})))
        .await;
    assert!(matches!(
        d.actions.as_slice(),
        [ActionIntent::FinishTask { status: TerminalStatus::Failed, .. }]
    ));
}

#[tokio::test]
async fn thinking_is_ephemeral_and_tools_have_their_own_output_class() {
    let mut task = Task::new().await;
    task.start().await;
    let d = task
        .reduce(event(AgentEventKind::Thinking, json!({"message":{"parts":[{"text":"thinking"}]}})))
        .await;
    assert!(d.actions.is_empty());
    let tool = json!({"toolCallId":"id", "phase":"start", "name":"search", "args":{}});
    let d = task.reduce(event(AgentEventKind::Tool, json!({"tool":tool}))).await;
    assert!(matches!(d.actions.as_slice(), [ActionIntent::Show {
        class: OutputClass::Tool, update: ArtifactUpdate::Upsert {content: Payload::Inline(v)}, ..
    }] if v == &tool));
}

#[test]
fn config_requires_the_selected_agent_to_be_authorized() {
    let spec = spec();
    let runner = DirectA2aRunner;
    assert!(runner.shares_session_downstream_context());
    assert!(
        runner
            .parse_and_validate_config(
                &json!({"agent":"other"}),
                &spec.authorized_agents,
                &spec.limits
            )
            .is_err()
    );
    assert!(runner.parse_and_validate_config(&json!({"agent":"bot"}), &[], &spec.limits).is_err());
}

#[tokio::test]
async fn conformance_direct_a2a_replays_the_same_start() {
    let input = RunnerInput::Start { tid: tid(), payload: Payload::Inline(json!("hello")) };
    collaboration_mode_runner_service_contract_tests(&DirectA2aRunner, &spec(), &tid(), &[input])
        .await.unwrap();
}

#[tokio::test]
async fn a_working_snapshot_closes_an_out_of_band_question() {
    for auth in [false, true] {
        let mut task = Task::new().await;
        task.start().await;
        let id = InteractionId("question".into());
        let kind = if auth { AgentEventKind::AuthRequired { interaction: id.clone() } }
            else { AgentEventKind::InputRequired { interaction: id.clone() } };
        task.reduce(event(kind, json!({"message":{"parts":[{"text":"Confirm"}]}}))).await;
        for snapshot in [json!({"task":{"artifacts":[]}}),
            json!({"task":{"status":{"state":"TASK_STATE_UNSPECIFIED"}}})] {
            assert!(task.reduce(event(AgentEventKind::Snapshot, snapshot)).await.actions.is_empty());
            assert_eq!(serde_json::to_value(&task.state).unwrap()["awaiting"], "question");
        }
        let working = json!({"task":{"status":{"state":"TASK_STATE_WORKING"}}});
        let decision = task.reduce(event(AgentEventKind::Snapshot, working.clone())).await;
        assert!(matches!(decision.actions.as_slice(),
            [ActionIntent::SetStatus { status: TaskStatus::Working, interactions, .. }] if interactions.is_empty()));
        assert!(serde_json::to_value(&task.state).unwrap()["awaiting"].is_null());
        assert!(task.reduce(event(AgentEventKind::Snapshot, working)).await.actions.is_empty());
        let stale = RunnerInput::HumanReply { tid: tid(), interaction: id,
            responder: PrincipalId("caller".into()), payload: Payload::Inline(json!("late")) };
        assert!(task.reduce(stale).await.actions.is_empty());
    }
}

#[tokio::test]
async fn multiple_text_parts_keep_their_boundaries() {
    let parts = json!([{"text":"first"},{"text":"second"}]);
    for (kind, payload) in [
        (AgentEventKind::Delta, json!({"artifact":{"artifactId":"a","parts":parts},"append":false})),
        (AgentEventKind::Snapshot, json!({"task":{"artifacts":[{"artifactId":"a","parts":parts}]}})),
        (AgentEventKind::Final, json!({"state":"completed","message":{"parts":parts}})),
    ] {
        let mut task = Task::new().await;
        task.start().await;
        let decision = task.reduce(event(kind, payload)).await;
        assert_show(&decision.actions[0], ArtifactUpdate::UpsertParts { content: Payload::Inline(parts.clone()) });
    }
}

#[tokio::test]
async fn format_switches_stay_consistent_with_projection_after_restore() {
    use bcs_dist_projection::OutputProjectionService;
    use bcs_service_api::core::dist_projection::OutputProjectionCoreService;
    let mut task = Task::new().await;
    task.start().await;
    let mut display = DisplaySnapshot { format_version: 1, units: vec![] };
    for (index, (parts, append, snapshot)) in [
        (json!([{"text":"start"}]), false, false),
        (json!([{"url":"https://example.com/file"}]), true, false),
        (json!([{"text":"caption"}]), true, false),
        (json!([{"text":"reset"}]), false, true),
        (json!([{"text":"tail"}]), true, false),
        (json!([{"data":{"n":1}}]), false, true),
        (json!([{"text":"end"}]), true, false),
    ].into_iter().enumerate() {
        let artifact = json!({"artifactId":"a","parts":parts});
        let (kind, payload) = if snapshot { (AgentEventKind::Snapshot, json!({"task":{"artifacts":[artifact]}})) }
            else { (AgentEventKind::Delta, json!({"artifact":artifact,"append":append})) };
        let decision = task.reduce(event(kind, payload)).await;
        for action in decision.actions {
            let ActionIntent::Show { source, artifact_id, class, update, .. } = action else { panic!() };
            display = OutputProjectionService.fold_committed(&display,
                &[ArtifactChange { source, artifact_id, class, update }], &PHASE_ONE_OUTPUT_LIMITS).unwrap();
        }
        let expected = if (3..=4).contains(&index) || index == 0 { DisplayContentFormat::Json }
            else { DisplayContentFormat::Parts };
        assert_eq!(display.units[0].content_format, expected);
    }
    assert_eq!(display.units[0].content, json!([{"data":{"n":1}},{"text":"end"}]));
}
