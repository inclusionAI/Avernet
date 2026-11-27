//! R25 conformance entries: the concrete `FsmReducerRunner` and the shared
//! `FsmValidator` call the centralized contract harnesses (plan T5). The
//! deep scenario proofs live in `fsm_round.rs`; these entries pin the
//! production types to the shared determinism contracts.
#![allow(clippy::unwrap_used, reason = "contract assertion")]
use bcs_dist_model::*;
use bcs_domain::collaboration::{
    ProjectionPolicy, StateMachineDefaults, StateMachineDefinition, StateMachineNodeDefinition,
    StateMachineNodeKind, StateMachineTransition,
};
use bcs_dist_runner_fsm::{d0_capabilities, FsmReducerRunner, FsmValidator};
use bcs_service_api::core::dist_runner::*;
use bcs_test_support::contract::distributed::services::{
    dist_runner_core_service_contract_tests, fsm_validator_core_service_contract_tests,
};
use std::collections::BTreeMap;
use std::num::NonZeroU64;

fn node(id: &str, targets: &[&str], final_output: bool) -> StateMachineNodeDefinition {
    StateMachineNodeDefinition {
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
                    targets: targets.iter().map(|t| t.to_string()).collect(),
                    guard: None,
                },
            )])
        },
        action: None,
        visibility: None,
        judge: None,
        final_output,
        extensions: serde_json::Map::new(),
    }
}

fn workflow() -> StateMachineDefinition {
    StateMachineDefinition {
        version: 1,
        graph_mode: bcs_domain::collaboration::StateMachineGraphMode::Acyclic,
        initial_node: None,
        input_schema: None,
        variables: BTreeMap::new(),
        events: BTreeMap::new(),
        projection: ProjectionPolicy::default(),
        defaults: StateMachineDefaults {
            node_timeout_ms: Some(600_000),
            max_attempts: 1,
        },
        human_input_channel: None,
        nodes: [
            ("start", vec!["left", "right"]),
            ("left", vec!["final"]),
            ("right", vec!["final"]),
            ("final", vec![]),
        ]
        .into_iter()
        .map(|(id, targets)| (id.to_string(), node(id, &targets, id == "final")))
        .collect(),
        extensions: serde_json::Map::new(),
    }
}

fn spec(workflow: &StateMachineDefinition) -> SessionSpec {
    SessionSpec {
        schema_version: 1,
        sid: Sid("sid-1".into()),
        shard: ShardId(1),
        scope: TaskScope {
            tenant: TenantId("tenant".into()),
            principal: PrincipalId("principal".into()),
        },
        kind: SessionKind::FsmGroup,
        group: GroupId("fixture-group".into()),
        workflow: WorkflowRef {
            definition_id: "fixture-definition".into(),
            revision: "v1".into(),
            digest: Digest("workflow-digest-1".into()),
        },
        participants: workflow
            .nodes
            .keys()
            .map(|name| ParticipantBinding {
                node: NodeId(name.clone()),
                target: AgentBinding {
                    agent: AgentId(format!("agent-{name}")),
                    binding_version: BindingVersion("binding-1".into()),
                    credential_ref: CredentialRef("fixture-credential".into()),
                },
                pool: PoolId("fixture-pool".into()),
                capabilities: DownstreamCapabilities {
                    idempotent_start: true,
                    idempotent_input: true,
                    query_by_request: true,
                    query_by_task: true,
                    subscribe: true,
                    resume_events: true,
                    cancel: true,
                    isolated_memory: true,
                    persistent_dedupe: true,
                },
            })
            .collect(),
        limits: SessionLimits {
            max_queued_tasks: 32,
            max_task_steps: 512,
            task_timeout_millis: 3_600_000,
            max_inline_bytes: 65_536,
        },
        digest: Digest("spec-digest-1".into()),
        created_at: TimestampMillis(0),
    }
}

#[test]
fn conformance_dist_runner_core_service() {
    let runner = FsmReducerRunner::d0();
    let workflow = workflow();
    let frozen = spec(&workflow);
    let context = EvaluationContext {
        spec: &frozen,
        workflow: &workflow,
        now: TimestampMillis(1_000),
    };
    let state = runner.init(&context).unwrap();
    let input = RoundInput {
        schema_version: RoundSchemaVersion::V1,
        source: GroupRoundSource::GroupA2a,
        key: RoundKey {
            scope: frozen.scope.clone(),
            group: frozen.group.clone(),
            sid: frozen.sid.clone(),
            tid: Tid("task-1".into()),
            generation: NonZeroU64::new(1).unwrap(),
        },
        ordered_parts: vec![RoundPart::Text {
            text: "resolve the incident".into(),
        }],
        workflow_ref: frozen.workflow.clone(),
        history_cut: BlobRef {
            key: "fixture-history".into(),
            digest: Digest("fixture-history-digest".into()),
        },
        execution_deadline: TimestampMillis(3_601_000),
    };
    let inputs = vec![
        RunnerInput::GroupStart { input },
        RunnerInput::Agent {
            tid: Tid("task-1".into()),
            event: AgentEvent {
                did: Did("fsm:task-1:1:start:1".into()),
                holder_epoch: HolderEpoch(1),
                seq: InputSeq(2),
                kind: AgentEventKind::Final,
                remote_event_id: None,
                payload: Payload::Inline(serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}})),
            },
        },
        RunnerInput::Cancel { tid: Tid("task-1".into()) },
    ];
    dist_runner_core_service_contract_tests(&runner, &context, &state, &inputs).unwrap();
}

#[test]
fn conformance_fsm_validator_core_service() {
    let validator = FsmValidator;
    let workflow = workflow();
    let frozen = spec(&workflow);
    fsm_validator_core_service_contract_tests(
        &validator,
        &frozen,
        &workflow,
        &d0_capabilities(),
    )
    .unwrap();
}
