//! Publication vectors: a shared binding with distinct per-node grants,
//! and the memory-isolation evidence the capability gate requires before a
//! downstream target may publish.
use crate::harness::*;
use bcs_dist_model::*;
use bcs_service_api::port::dist_agent::CallOperation;
use bcs_service_api::port::dist_agent::CancelOutcome;
use rig::*;
use serde_json::json;
use std::sync::Arc;

// --------------------------------------------------------------------------
// T6 case 4: one shared binding, distinct grants. The research node's new
// data delegation is revoked; the review node still queries and cancels
// its own remote task under its independent grant — nodes and operations
// are never swapped.
// --------------------------------------------------------------------------

#[tokio::test]
async fn shared_binding_distinct_grants() {
    let agent = Arc::new(GrantedAgent::all(Arc::new(ScriptedAgent::default())));
    let rig = rig_with(agent.clone()).await;
    let mut research = node_spec("research", 1);
    research.capabilities.subscribe = false;
    research.capabilities.query_by_task = false;
    let mut review = node_spec("review", 1);
    review.capabilities.subscribe = false;
    review.capabilities.query_by_task = true;
    let credential = research.target.credential_ref.0.clone();
    assert_eq!(credential, review.target.credential_ref.0, "one shared binding");
    let did_research = research.did.clone();
    let did_review = review.did.clone();
    rig.rig.worker.create_spec(research).await;
    rig.rig.worker.create_spec(review).await;
    rig.agent.inner.on_start(Reply::Accepted);
    rig.agent.inner.on_remote(RemoteTaskRef { task_id: "rt-research".into(), context_id: Some("ctx-r".into()) });
    rig.agent.inner.on_start(Reply::Accepted);
    rig.agent.inner.on_remote(RemoteTaskRef { task_id: "rt-review".into(), context_id: Some("ctx-v".into()) });
    // The review node's own task keeps working while it is watched.
    rig.agent.inner.on_task(own_found(TaskStatus::Working, json!({"task": {"id": "rt-review"}})));
    rig.agent.inner.on_cancel(CancelOutcome::ConfirmedCanceled);
    rig.rig.worker.notify_did(&did_research).await;
    rig.rig.worker.notify_did(&did_review).await;
    let running = rig.start();
    until("both accepted", || async {
        rig.phase(&did_research).await == "accepted" && rig.phase(&did_review).await == "accepted"
    })
    .await;

    // The research node's delegation for new data is revoked mid-flight;
    // the review node keeps its own control grant.
    agent.grant(
        "research",
        &credential,
        Grants { start: false, cont: false, query: false, subscribe: false, cancel: false },
    );
    agent.grant("review", &credential, Grants { start: true, cont: false, query: true, subscribe: false, cancel: true });
    // The Worker pushes new input to the research node and cancels the
    // review task.
    push_input(&rig.rig, &did_research, 1).await;
    worker_cancel(&rig.rig, &did_review).await;
    until("the review call is canceled", || async { rig.phase(&did_review).await == "ended" }).await;
    until("the research revoke was observed", || async { agent.denies() >= 1 }).await;

    // The research node's new input never reached the agent: no
    // unauthorized new data or history was sent.
    assert!(
        !rig.agent
            .inner
            .calls()
            .iter()
            .any(|c| c.contains(&format!("{}:in:1", did_research.0))),
        "the revoked research grant delivers nothing"
    );
    assert_eq!(rig.phase(&did_research).await, "accepted", "the research call stays supervised, not stolen");
    // The review node queried and canceled its own original task.
    let review_ops: Vec<_> = agent
        .seen()
        .into_iter()
        .filter(|(_, node, op, _)| node.0 == "review" && matches!(op, CallOperation::Query | CallOperation::Cancel))
        .collect();
    assert!(!review_ops.is_empty(), "the review node acts under its own grant");
    assert!(
        review_ops
            .iter()
            .all(|(_, _, _, remote)| remote.as_ref().is_some_and(|r| r.task_id == "rt-review")),
        "the review grant is bound to its own remote task"
    );
    // No authority ever mixed the two nodes' identities or operations.
    assert!(
        !agent
            .seen()
            .into_iter()
            .any(|(_, node, _, remote)| node.0 == "review"
                && remote.as_ref().is_some_and(|r| r.task_id == "rt-research"))
    );
    running.task.abort();
}

// --------------------------------------------------------------------------
// T6 case 9: a test bot that shares global memory across conversations is
// rejected by the capability gate; only targets deployed with verified
// isolated memory domains (or shared memory disabled) may publish, and a
// different conversation id never substitutes for that evidence.
// --------------------------------------------------------------------------

#[test]
fn remote_global_memory_ignores_conversation_id() {
    use bcs_domain::collaboration::{
        ProjectionPolicy, StateMachineDefaults, StateMachineDefinition, StateMachineNodeDefinition,
        StateMachineNodeKind, StateMachineTransition,
    };
    use bcs_dist_runner_fsm::{d0_capabilities, FsmValidator};
    use bcs_service_api::core::dist_runner::{FsmValidatorCoreService, RunnerError};
    use std::collections::BTreeMap;

    let node = |id: &str, final_output: bool| StateMachineNodeDefinition {
        kind: StateMachineNodeKind::BotTask,
        display_name: id.to_string(),
        loop_definition: None,
        assignee: None,
        notification: None,
        instruction: Some(format!("work on {id}")),
        output_contract: None,
        node_timeout_ms: Some(600_000),
        max_attempts: None,
        transitions: if final_output {
            BTreeMap::new()
        } else {
            BTreeMap::from([(
                "complete".to_string(),
                StateMachineTransition {
                    display_name: None,
                    targets: vec!["final".to_string()],
                    guard: None,
                },
            )])
        },
        action: None,
        visibility: None,
        judge: None,
        final_output,
        extensions: serde_json::Map::new(),
    };
    let workflow = StateMachineDefinition {
        version: 1,
        graph_mode: bcs_domain::collaboration::StateMachineGraphMode::Acyclic,
        initial_node: None,
        input_schema: None,
        variables: BTreeMap::new(),
        events: BTreeMap::new(),
        projection: ProjectionPolicy::default(),
        defaults: StateMachineDefaults { node_timeout_ms: Some(600_000), max_attempts: 1 },
        human_input_channel: None,
        nodes: [
            ("start".to_string(), node("start", false)),
            ("final".to_string(), node("final", true)),
        ]
        .into_iter()
        .collect(),
        extensions: serde_json::Map::new(),
    };
    // The SAME test bot serves both nodes, so the two conversations of one
    // group would cross-read through the bot's shared global memory.
    let shared_bot = AgentBinding {
        agent: AgentId("shared-bot".into()),
        binding_version: BindingVersion("b-1".into()),
        credential_ref: CredentialRef("cred://shared-bot/b1".into()),
    };
    let caps = || DownstreamCapabilities {
        idempotent_start: true,
        idempotent_input: true,
        query_by_request: true,
        query_by_task: true,
        subscribe: true,
        resume_events: true,
        cancel: true,
        // No isolation evidence: the target's global memory ignores the
        // conversation boundary.
        isolated_memory: false,
        persistent_dedupe: true,
    };
    let mut spec = SessionSpec {
        schema_version: 1,
        sid: Sid("s0042.7f3a9c".into()),
        shard: ShardId(1),
        scope: TaskScope { tenant: TenantId("t".into()), principal: PrincipalId("p".into()) },
        kind: SessionKind::FsmGroup,
        group: GroupId("g".into()),
        workflow: WorkflowRef {
            definition_id: "d".into(),
            revision: "r1".into(),
            digest: Digest("sha256:w".into()),
        },
        participants: vec![
            ParticipantBinding {
                node: NodeId("start".into()),
                target: shared_bot.clone(),
                pool: PoolId("a2a".into()),
                capabilities: caps(),
            },
            ParticipantBinding {
                node: NodeId("final".into()),
                target: shared_bot,
                pool: PoolId("a2a".into()),
                capabilities: caps(),
            },
        ],
        limits: SessionLimits {
            max_queued_tasks: 1,
            max_task_steps: 8,
            task_timeout_millis: 600_000,
            max_inline_bytes: 65_536,
        },
        digest: Digest("sha256:spec".into()),
        created_at: TimestampMillis(1),
    };

    let validator = FsmValidator;
    let result = validator.validate(&spec, &workflow, &d0_capabilities());
    assert!(
        matches!(&result, Err(RunnerError::InvalidWorkflow { code }) if code.contains("no isolated memory evidence")),
        "a target whose global memory ignores the conversation id is not publishable: {result:?}"
    );
    // Verified isolation of the same shared bot publishes.
    spec.participants[0].capabilities.isolated_memory = true;
    spec.participants[1].capabilities.isolated_memory = true;
    assert!(validator.validate(&spec, &workflow, &d0_capabilities()).is_ok());
    // A different conversation id never substitutes for isolation evidence.
    spec.participants[0].capabilities.isolated_memory = false;
    spec.participants[1].capabilities.isolated_memory = false;
    spec.sid = Sid("s0042.other-conversation".into());
    assert!(
        validator.validate(&spec, &workflow, &d0_capabilities()).is_err(),
        "a fresh conversation id alone never publishes shared global memory"
    );
}
