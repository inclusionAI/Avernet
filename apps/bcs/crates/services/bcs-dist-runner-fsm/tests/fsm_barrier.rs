//! Stop-barrier evidence branches and GroupStart idempotence (review round 1 /
//! Task 7). These lock behaviors already implemented by the reducer: Final
//! reports are first-class stop facts (both on a `Canceling` attempt racing to
//! success in the Running phase, and as the last stop evidence that releases
//! the committed terminal), and an installed round survives identical
//! GroupStart replay while refusing different content. Deep scenarios live in
//! `fsm_round.rs`; per-rule validator rejections live in `fsm_validate_matrix.rs`.
#![allow(clippy::unwrap_used, reason = "barrier scenario assertions")]
use bcs_dist_model::*;
use bcs_domain::collaboration::{
    ProjectionPolicy, StateMachineDefaults, StateMachineDefinition,
    StateMachineNodeDefinition, StateMachineNodeKind, StateMachineTransition,
};
use bcs_dist_runner_fsm::FsmReducerRunner;
use bcs_service_api::core::dist_runner::*;
use std::collections::BTreeMap;
use std::num::NonZeroU64;

const NOW: i64 = 1_000;
const NODE_TIMEOUT_MS: u64 = 600_000;
const ROUND_DEADLINE_MS: i64 = 3_600_000;

fn fsm_node(id: &str, targets: &[&str], final_output: bool) -> StateMachineNodeDefinition {
    let transitions = if final_output {
        BTreeMap::new()
    } else {
        BTreeMap::from([("complete".to_string(), StateMachineTransition {
            display_name: None,
            targets: targets.iter().map(|t| t.to_string()).collect(),
            guard: None,
        })])
    };
    StateMachineNodeDefinition {
        kind: StateMachineNodeKind::BotTask,
        display_name: id.to_string(),
        loop_definition: None,
        assignee: None,
        notification: None,
        instruction: Some(format!("work on {id}")),
        output_contract: None,
        node_timeout_ms: Some(NODE_TIMEOUT_MS),
        max_attempts: None,
        transitions,
        action: None,
        visibility: None,
        judge: None,
        final_output,
        extensions: serde_json::Map::new(),
    }
}

fn workflow_of(nodes: Vec<(&str, &[&str], bool)>) -> StateMachineDefinition {
    StateMachineDefinition {
        version: 1,
        graph_mode: bcs_domain::collaboration::StateMachineGraphMode::Acyclic,
        initial_node: None,
        input_schema: None,
        variables: BTreeMap::new(),
        events: BTreeMap::new(),
        projection: ProjectionPolicy::default(),
        defaults: StateMachineDefaults {
            node_timeout_ms: Some(NODE_TIMEOUT_MS),
            max_attempts: 1,
        },
        human_input_channel: None,
        nodes: nodes
            .into_iter()
            .map(|(id, targets, final_output)| (id.to_string(), fsm_node(id, targets, final_output)))
            .collect(),
        extensions: serde_json::Map::new(),
    }
}

fn downstream() -> DownstreamCapabilities {
    DownstreamCapabilities {
        idempotent_start: true, idempotent_input: true, query_by_request: true,
        query_by_task: true, subscribe: true, resume_events: true, cancel: true,
    }
}

fn spec_for(workflow: &StateMachineDefinition, version: u8) -> SessionSpec {
    SessionSpec {
        schema_version: 1,
        sid: Sid(format!("sid-{version}")),
        shard: ShardId(1),
        scope: TaskScope {
            tenant: TenantId("tenant".into()),
            principal: PrincipalId("principal".into()),
        },
        kind: SessionKind::FsmGroup,
        group: GroupId("fixture-group".into()),
        workflow: WorkflowRef {
            definition_id: "fixture-definition".into(),
            revision: format!("v{version}"),
            digest: Digest(format!("workflow-digest-{version}")),
        },
        participants: workflow
            .nodes
            .keys()
            .map(|node| ParticipantBinding {
                node: NodeId(node.clone()),
                target: AgentBinding {
                    agent: AgentId(format!("agent-{node}")),
                    binding_version: BindingVersion(format!("binding-{version}")),
                    credential_ref: CredentialRef("fixture-credential".into()),
                },
                pool: PoolId("fixture-pool".into()),
                capabilities: downstream(),
            })
            .collect(),
        limits: SessionLimits {
            max_queued_tasks: 32,
            max_task_steps: 512,
            task_timeout_millis: ROUND_DEADLINE_MS as u64,
            max_inline_bytes: 65_536,
        },
        digest: Digest(format!("spec-digest-{version}")),
        created_at: TimestampMillis(0),
    }
}

fn round_for(spec: &SessionSpec, tid: &str, deadline_after_now: i64) -> RoundInput {
    RoundInput {
        schema_version: RoundSchemaVersion::V1,
        source: GroupRoundSource::GroupA2a,
        key: RoundKey {
            scope: spec.scope.clone(),
            group: spec.group.clone(),
            sid: spec.sid.clone(),
            tid: Tid(tid.into()),
            generation: NonZeroU64::new(1).unwrap(),
        },
        ordered_parts: vec![RoundPart::Text {
            text: "resolve the incident".into(),
        }],
        workflow_ref: spec.workflow.clone(),
        history_cut: BlobRef {
            key: "fixture-history".into(),
            digest: Digest("fixture-history-digest".into()),
        },
        execution_deadline: TimestampMillis(NOW + deadline_after_now),
    }
}

fn group_start(input: RoundInput) -> RunnerInput {
    RunnerInput::GroupStart { input }
}
fn agent(tid: &str, did: &str, seq: u64, kind: AgentEventKind, payload: serde_json::Value) -> RunnerInput {
    RunnerInput::Agent {
        tid: Tid(tid.into()),
        event: AgentEvent {
            did: Did(did.into()),
            holder_epoch: HolderEpoch(1),
            seq: InputSeq(seq),
            kind,
            remote_event_id: None,
            payload: Payload::Inline(payload),
        },
    }
}

struct Round {
    runner: FsmReducerRunner,
    spec: SessionSpec,
    workflow: StateMachineDefinition,
    state: RunnerState,
}

impl Round {
    fn open(workflow: StateMachineDefinition, version: u8) -> Round {
        let runner = FsmReducerRunner::d0();
        let spec = spec_for(&workflow, version);
        let context = EvaluationContext {
            spec: &spec,
            workflow: &workflow,
            now: TimestampMillis(NOW),
        };
        let state = runner.init(&context).unwrap();
        Round { runner, spec, workflow, state }
    }

    fn context<'a>(&'a self, spec: &'a SessionSpec, workflow: &'a StateMachineDefinition) -> EvaluationContext<'a> {
        EvaluationContext { spec, workflow, now: TimestampMillis(NOW) }
    }

    fn step(&mut self, inputs: &[RunnerInput]) -> Decision {
        let spec = self.spec.clone();
        let workflow = self.workflow.clone();
        self.step_with(&spec, &workflow, inputs)
    }

    fn step_with(
        &mut self,
        spec: &SessionSpec,
        workflow: &StateMachineDefinition,
        inputs: &[RunnerInput],
    ) -> Decision {
        let context = self.context(spec, workflow);
        let decision = self.runner.reduce(&context, &self.state, inputs).unwrap();
        self.state = decision.next_state.clone();
        decision
    }

    fn dispatches(decision: &Decision) -> Vec<(String, u32, String)> {
        decision
            .actions
            .iter()
            .filter_map(|action| match action {
                ActionIntent::StartDispatch(spec) => {
                    Some((spec.node.0.clone(), spec.attempt, spec.did.0.clone()))
                }
                _ => None,
            })
            .collect()
    }

    fn cancels(decision: &Decision) -> Vec<String> {
        decision
            .actions
            .iter()
            .filter_map(|action| match action {
                ActionIntent::CancelDispatch { did, .. } => Some(did.0.clone()),
                _ => None,
            })
            .collect()
    }

    fn finished(decision: &Decision) -> Vec<(TerminalStatus, Payload)> {
        decision
            .actions
            .iter()
            .filter_map(|action| match action {
                ActionIntent::FinishTask { status, result, .. } => Some((*status, result.clone())),
                _ => None,
            })
            .collect()
    }
}

fn dispatch_of(decision: &Decision, node: &str) -> (u32, String) {
    let hits: Vec<_> = Round::dispatches(decision).into_iter().filter(|(n, _, _)| n == node).collect();
    assert_eq!(hits.len(), 1, "one dispatch for {node}");
    (hits[0].1, hits[0].2.clone())
}

fn dispatch_deadline_of(decision: &Decision, did: &str) -> Deadline {
    decision
        .next_deadlines
        .iter()
        .find(|deadline| match &deadline.kind {
            DeadlineKind::Dispatch { did: d, .. } => d.0 == did,
            _ => false,
        })
        .cloned()
        .expect("the dispatch registers its own timeout")
}

/// Replaying the identical GroupStart is inert: the installed round already
/// owns the dispatches, and nothing is re-created or re-derived.
#[test]
fn group_start_replay_is_idempotent() {
    let workflow = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
    let mut round = Round::open(workflow, 1);
    let input = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let first = round.step(&[group_start(input.clone())]);
    assert_eq!(Round::dispatches(&first).len(), 1, "the admission dispatches once");

    let replayed = round.step(&[group_start(input)]);
    assert!(
        Round::dispatches(&replayed).is_empty(),
        "identical GroupStart replay re-creates no dispatch"
    );
    assert!(Round::cancels(&replayed).is_empty());
    assert!(Round::finished(&replayed).is_empty());
    assert_eq!(replayed.next_state, first.next_state, "replay leaves the state unchanged");

    // The pinned round still runs to completion on the original admission.
    let did = dispatch_of(&first, "start").1;
    let done = round.step(&[agent(
        "task-1",
        &did,
        2,
        AgentEventKind::Final,
        serde_json::json!({"value": "start"}),
    )]);
    let nodes: Vec<String> = Round::dispatches(&done).into_iter().map(|(n, _, _)| n).collect();
    assert_eq!(nodes, vec!["final".to_string()], "the round continues from the first admission");
}

/// A GroupStart carrying different content cannot replace the installed
/// round: the runner state holds exactly one round per generation.
#[test]
fn different_group_start_cannot_replace_the_installed_round() {
    let workflow = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
    let mut round = Round::open(workflow, 1);
    let input = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    round.step(&[group_start(input)]);

    let mut different = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    different.ordered_parts = vec![RoundPart::Text {
        text: "a different ask".into(),
    }];
    let result = round.runner.reduce(
        &round.context(&round.spec, &round.workflow),
        &round.state,
        &[group_start(different)],
    );
    match result {
        Err(RunnerError::InvalidWorkflow { code }) => assert!(
            code.contains("cannot replace the one in this runner state"),
            "the replacement rejection names the rule; got {code}"
        ),
        other => panic!("a different round must be rejected, got {other:?}"),
    }
}

/// A Final report on a `Canceling` attempt is that attempt's own stop fact:
/// the timeout stood and the verified success releases the slot, so the round
/// proceeds on the success path (no retry, no failure).
#[test]
fn final_on_canceling_attempt_counts_as_verified_success() {
    let workflow = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
    let mut round = Round::open(workflow, 1);
    let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(inputs)]);
    let did = dispatch_of(&opened, "start").1;
    let deadline = dispatch_deadline_of(&opened, &did);

    let timed_out = round.step(&[RunnerInput::Timeout(deadline)]);
    assert_eq!(Round::cancels(&timed_out), vec![did.clone()], "the unverified attempt is canceled");
    assert!(Round::dispatches(&timed_out).is_empty(), "no retry before stop evidence");

    let raced = round.step(&[agent(
        "task-1",
        &did,
        2,
        AgentEventKind::Final,
        serde_json::json!({"value": "late success"}),
    )]);
    assert!(
        Round::dispatches(&raced).iter().any(|(node, attempt, _)| node == "final" && *attempt == 1),
        "the verified late Final releases the slot and joins to the final node"
    );
    assert!(
        !Round::dispatches(&raced).iter().any(|(node, _, _)| node == "start"),
        "the timed-out attempt is never re-dispatched"
    );
    assert!(Round::finished(&raced).is_empty(), "no terminal from a timeout that lost the race");
}

#[test]
fn dispatch_identity_is_accepted_by_joint_worker() {
    let mut round = Round::open(workflow_of(vec![("final", &[], true)]), 1);
    let input = round_for(&round.spec, "s0042.context.task", ROUND_DEADLINE_MS);
    let decision = round.step(&[group_start(input)]);
    let dispatch = decision.actions.iter().find_map(|action| match action {
        ActionIntent::StartDispatch(spec) => Some(spec),
        _ => None,
    }).unwrap();
    assert!(dispatch.did.0.starts_with(&format!("{}.", dispatch.tid.0)));
}

#[test]
fn final_node_final_after_timeout_finishes_once() {
    let mut round = Round::open(workflow_of(vec![("final", &[], true)]), 1);
    let input = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(input)]);
    let did = dispatch_of(&opened, "final").1;
    let deadline = dispatch_deadline_of(&opened, &did);
    round.step(&[RunnerInput::Timeout(deadline)]);
    let report = agent("task-1", &did, 2, AgentEventKind::Final,
        serde_json::json!({"value": "late final"}));
    let finished = round.step(&[report.clone()]);
    assert_eq!(Round::finished(&finished).len(), 1);
    assert_eq!(Round::finished(&finished)[0].0, TerminalStatus::Completed);
    assert!(Round::finished(&round.step(&[report])).is_empty());
}

/// Final reports are stop evidence inside the barrier: the terminal commits
/// only after the last one lands, exactly once, with the frozen status.
#[test]
fn final_counts_as_stop_evidence_in_stopping_phase() {
    let workflow = workflow_of(vec![
        ("start", &["left", "right"], false),
        ("left", &["final"], false),
        ("right", &["final"], false),
        ("final", &[], true),
    ]);
    let mut round = Round::open(workflow, 1);
    let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(inputs)]);
    let start_did = dispatch_of(&opened, "start").1;
    let fanned = round.step(&[agent(
        "task-1",
        &start_did,
        2,
        AgentEventKind::Final,
        serde_json::json!({"value": "start"}),
    )]);
    let left_did = dispatch_of(&fanned, "left").1;
    let right_did = dispatch_of(&fanned, "right").1;

    let canceled = round.step(&[RunnerInput::Cancel { tid: Tid("task-1".into()) }]);
    assert!(Round::finished(&canceled).is_empty(), "canceling never commits while attempts are alive");

    let half_stopped = round.step(&[agent(
        "task-1",
        &left_did,
        3,
        AgentEventKind::Final,
        serde_json::json!({"value": "left late"}),
    )]);
    assert!(
        Round::finished(&half_stopped).is_empty(),
        "a Final for one branch is not enough: the other attempt is still unverified"
    );

    let closure = round.step(&[agent(
        "task-1",
        &right_did,
        4,
        AgentEventKind::Final,
        serde_json::json!({"value": "right late"}),
    )]);
    assert_eq!(Round::finished(&closure).len(), 1);
    let (status, Payload::Inline(result)) = &Round::finished(&closure)[0] else {
        panic!("inline terminal result");
    };
    assert_eq!(*status, TerminalStatus::Canceled, "the frozen status survives, whatever the evidence kind");
    assert_eq!(
        result["frozen_reason"].as_str().unwrap(),
        "canceled by request",
        "the original frozen reason survives: {result}"
    );
}
