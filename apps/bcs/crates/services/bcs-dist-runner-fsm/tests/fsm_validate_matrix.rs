//! Focused validator rejection matrix (review round 1 / Task 7): one test per
//! load-bearing publication rule, each asserting the rule's own error code —
//! not just the `InvalidWorkflow` variant. Deep scenario proofs (dispatching,
//! replay, stop barriers) live in `fsm_round.rs` / `fsm_barrier.rs`.
#![allow(clippy::unwrap_used, reason = "matrix assertions")]
use bcs_dist_model::*;
use bcs_domain::collaboration::{
    HumanInputChannelDefinition, ProjectionPolicy, StateMachineDefaults,
    StateMachineDefinition, StateMachineNodeDefinition, StateMachineNodeKind,
    StateMachineTransition,
};
use bcs_dist_runner_fsm::{d0_capabilities, FsmValidator};
use bcs_service_api::core::dist_runner::*;
use std::collections::BTreeMap;

const NODE_TIMEOUT_MS: u64 = 600_000;

/// Raw node builder: `final_output` and `transitions` are set independently so
/// a fixture can violate exactly one rule (the shared `fsm_node`-style helper
/// drops transitions on final nodes, which would mask this class of bugs).
fn raw_node(
    id: &str,
    kind: StateMachineNodeKind,
    final_output: bool,
    transitions: BTreeMap<String, StateMachineTransition>,
) -> StateMachineNodeDefinition {
    StateMachineNodeDefinition {
        kind,
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

fn complete(targets: &[&str]) -> BTreeMap<String, StateMachineTransition> {
    BTreeMap::from([("complete".to_string(), StateMachineTransition {
        display_name: None,
        targets: targets.iter().map(|t| t.to_string()).collect(),
        guard: None,
    })])
}

fn serial_workflow() -> StateMachineDefinition {
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
        nodes: [
            ("start".to_string(), raw_node("start", StateMachineNodeKind::BotTask, false, complete(&["final"]))),
            ("final".to_string(), raw_node("final", StateMachineNodeKind::BotTask, true, BTreeMap::new())),
        ]
        .into_iter()
        .collect(),
        extensions: serde_json::Map::new(),
    }
}

fn participant(node: &str) -> ParticipantBinding {
    ParticipantBinding {
        node: NodeId(node.into()),
        target: AgentBinding {
            agent: AgentId(format!("agent-{node}")),
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
    }
}

fn spec_for(workflow: &StateMachineDefinition) -> SessionSpec {
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
            .map(|node| participant(node))
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

/// The matrix core: validate must fail with the named rule's own code.
fn assert_rejected(label: &str, workflow: &StateMachineDefinition, spec: &SessionSpec, needle: &str) {
    match FsmValidator.validate(spec, workflow, &d0_capabilities()) {
        Err(RunnerError::InvalidWorkflow { code }) => assert!(
            code.contains(needle),
            "{label} must name its rule ({needle:?}); got {code:?}"
        ),
        other => panic!("{label} must be rejected, got {other:?}"),
    }
}

/// Valid workflow sanity: the base fixture must be publishable, so every
/// rejection below is attributable to the single mutated rule.
#[test]
fn matrix_base_workflow_publishes() {
    let workflow = serial_workflow();
    let spec = spec_for(&workflow);
    let validated = FsmValidator
        .validate(&spec, &workflow, &d0_capabilities())
        .expect("the unmutated fixture is publishable");
    assert_eq!(validated.final_node.0, "final");
    assert_eq!(validated.entry.0, "start");
}

/// Exactly one designated final_output: two transition-less finals trip the
/// count rule alone (fixture corrected per review: no node carries transitions
/// that could redirect the failure to a different rule).
#[test]
fn matrix_rejects_two_final_outputs() {
    let workflow = StateMachineDefinition {
        nodes: [
            ("start".to_string(), raw_node("start", StateMachineNodeKind::BotTask, false, complete(&["a", "b"]))),
            ("a".to_string(), raw_node("a", StateMachineNodeKind::BotTask, true, BTreeMap::new())),
            ("b".to_string(), raw_node("b", StateMachineNodeKind::BotTask, true, BTreeMap::new())),
        ]
        .into_iter()
        .collect(),
        ..serial_workflow()
    };
    let spec = spec_for(&workflow);
    assert_rejected("two finals", &workflow, &spec, "exactly one final_output node; found 2");
}

/// The sole-round-product invariant: a final_output node must not declare
/// transitions (its payload IS the round product).
#[test]
fn matrix_rejects_final_node_with_transitions() {
    let mut workflow = serial_workflow();
    workflow
        .nodes
        .get_mut("final")
        .unwrap()
        .transitions = complete(&["start"]);
    let spec = spec_for(&workflow);
    assert_rejected(
        "final node with transitions",
        &workflow,
        &spec,
        "is final_output and must not declare transitions",
    );
}

/// Guarded transitions are not admitted (guards imply judgement, which the
/// strict FSM plane rejects at validate() time).
#[test]
fn matrix_rejects_guarded_transition() {
    let mut workflow = serial_workflow();
    workflow
        .nodes
        .get_mut("start")
        .unwrap()
        .transitions
        .get_mut("complete")
        .unwrap()
        .guard = Some("parts.approved".into());
    let spec = spec_for(&workflow);
    assert_rejected("guarded transition", &workflow, &spec, "guarded transitions are not admitted");
}

/// Fan-out beyond the capability bound (`max_fan_out` = 8 at D0).
#[test]
fn matrix_rejects_fan_out_above_bound() {
    let satellite: Vec<(String, StateMachineNodeDefinition)> = (1..=9)
        .map(|i| (
            format!("sat{i}"),
            raw_node(&format!("sat{i}"), StateMachineNodeKind::BotTask, false, complete(&["final"])),
        ))
        .collect();
    let workflow = StateMachineDefinition {
        nodes: [
            ("start".to_string(), raw_node("start", StateMachineNodeKind::BotTask, false, complete(&[
                "sat1", "sat2", "sat3", "sat4", "sat5", "sat6", "sat7", "sat8", "sat9",
            ]))),
            ("final".to_string(), raw_node("final", StateMachineNodeKind::BotTask, true, BTreeMap::new())),
        ]
        .into_iter()
        .chain(satellite)
        .collect(),
        ..serial_workflow()
    };
    let spec = spec_for(&workflow);
    assert_rejected("fan-out above bound", &workflow, &spec, "fans out to 9 targets; the admitted bound is 8");
}

/// Declaring an interactive surface rejects the whole workflow at validate()
/// time — never accepted-then-suspended.
#[test]
fn matrix_rejects_human_input_channel() {
    let mut workflow = serial_workflow();
    workflow.human_input_channel = Some(HumanInputChannelDefinition {
        channel_type: "group_chat".into(),
        fixed_group: None,
    });
    let spec = spec_for(&workflow);
    assert_rejected(
        "human_input_channel",
        &workflow,
        &spec,
        "human_input_channel declares an interactive surface",
    );
}

/// Every dispatchable node needs a positive timeout once resolved from
/// overrides / defaults.
#[test]
fn matrix_rejects_zero_node_timeout() {
    let mut workflow = serial_workflow();
    workflow.nodes.get_mut("start").unwrap().node_timeout_ms = Some(0);
    let spec = spec_for(&workflow);
    assert_rejected("zero node timeout", &workflow, &spec, "no positive dispatch timeout");
}

#[test]
fn matrix_rejects_zero_default_timeout() {
    let mut workflow = serial_workflow();
    workflow.defaults.node_timeout_ms = Some(0);
    workflow.nodes.get_mut("start").unwrap().node_timeout_ms = None;
    let spec = spec_for(&workflow);
    assert_rejected(
        "zero default timeout",
        &workflow,
        &spec,
        "node start has no positive dispatch timeout",
    );
}

/// A node asking for zero attempts is structurally broken, not a bound issue.
#[test]
fn matrix_rejects_zero_max_attempts() {
    let mut workflow = serial_workflow();
    workflow.nodes.get_mut("start").unwrap().max_attempts = Some(0);
    let spec = spec_for(&workflow);
    assert_rejected("zero max_attempts", &workflow, &spec, "max_attempts must be at least 1");
}

/// A non-final node without transitions can never complete and is rejected
/// (distinct from an empty `targets` list, which trips the no-target rule).
#[test]
fn matrix_rejects_non_final_node_without_transitions() {
    let mut workflow = serial_workflow();
    workflow
        .nodes
        .get_mut("start")
        .unwrap()
        .transitions = BTreeMap::new();
    let spec = spec_for(&workflow);
    assert_rejected(
        "non-final without transitions",
        &workflow,
        &spec,
        "is not final_output and must declare a transition",
    );
}

/// A transition pointing outside the workflow cannot be dispatched.
#[test]
fn matrix_rejects_dangling_target() {
    let mut workflow = serial_workflow();
    workflow
        .nodes
        .get_mut("start")
        .unwrap()
        .transitions = complete(&["ghost"]);
    let spec = spec_for(&workflow);
    assert_rejected("dangling target", &workflow, &spec, "transition target ghost not found");
}

/// A binding whose target cannot return a complete terminal snapshot (neither
/// `query_by_task` nor `query_by_request`): a status-only / delta-only target
/// cannot back the full GetTask recovery read that publication promises for
/// every dispatched node.
#[test]
fn matrix_rejects_status_only_recovery_target() {
    let workflow = serial_workflow();
    let mut spec = spec_for(&workflow);
    let start = spec
        .participants
        .iter_mut()
        .find(|p| p.node.0 == "start")
        .expect("fixture binds the start node");
    start.capabilities.query_by_task = false;
    start.capabilities.query_by_request = false;
    assert_rejected(
        "status-only recovery target",
        &workflow,
        &spec,
        "complete terminal snapshot",
    );
}

/// A binding without memory-isolation evidence is not publishable: shared
/// global memory across conversations leaks round state, and only verified
/// isolated memory domains (or disabled shared memory) publish.
#[test]
fn matrix_rejects_shared_global_memory_target() {
    let workflow = serial_workflow();
    let mut spec = spec_for(&workflow);
    let start = spec
        .participants
        .iter_mut()
        .find(|p| p.node.0 == "start")
        .expect("fixture binds the start node");
    start.capabilities.isolated_memory = false;
    assert_rejected(
        "shared global memory target",
        &workflow,
        &spec,
        "isolated memory",
    );
}

/// The frozen spec binds every dispatchable node exactly once.
#[test]
fn matrix_rejects_duplicate_participant_binding() {
    let workflow = serial_workflow();
    let mut spec = spec_for(&workflow);
    spec.participants.push(participant("start"));
    assert_rejected(
        "duplicate participant binding",
        &workflow,
        &spec,
        "node start has more than one participant binding",
    );
}
