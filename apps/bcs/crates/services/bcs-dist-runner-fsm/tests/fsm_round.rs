//! Pinned strict-FSM round scenarios (plan T5): designated final output,
//! publication rejection before any Task exists, deterministic replay,
//! bounded retry, and the strict-source stop barrier. Every scenario drives
//! the real reducer through `DistRunnerCoreService::{init,reduce}` only.
#![allow(clippy::unwrap_used, reason = "pinned scenario assertions")]
use bcs_dist_model::*;
use bcs_domain::collaboration::{
    FixedLoopDefinition, FixedLoopMode, JudgePolicy, ProjectionPolicy, StateMachineDefaults,
    StateMachineDefinition, StateMachineNodeDefinition, StateMachineNodeKind,
    StateMachineTransition,
};
use bcs_dist_runner_fsm::{d0_capabilities, FsmReducerRunner, FsmValidator};
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
    workflow_with_defaults(nodes, None)
}

/// `max_attempts` is the definition-level default; `None` leaves the workflow
/// default of one attempt.
fn workflow_with_defaults(
    nodes: Vec<(&str, &[&str], bool)>,
    max_attempts: Option<i32>,
) -> StateMachineDefinition {
    let defaults = StateMachineDefaults {
        node_timeout_ms: Some(NODE_TIMEOUT_MS),
        max_attempts: max_attempts.unwrap_or(1),
    };
    StateMachineDefinition {
        version: 1,
        graph_mode: bcs_domain::collaboration::StateMachineGraphMode::Acyclic,
        initial_node: None,
        input_schema: None,
        variables: BTreeMap::new(),
        events: BTreeMap::new(),
        projection: ProjectionPolicy::default(),
        defaults,
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
        isolated_memory: true,
        persistent_dedupe: true,
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

fn cancel(tid: &str) -> RunnerInput {
    RunnerInput::Cancel { tid: Tid(tid.into()) }
}

fn task_deadline(tid: &str) -> RunnerInput {
    RunnerInput::Timeout(Deadline {
        id: DeadlineId("fixture-task-deadline".into()),
        at: TimestampMillis(NOW + ROUND_DEADLINE_MS),
        kind: DeadlineKind::Task { tid: Tid(tid.into()) },
    })
}

/// A pinned round driven through the real core-service contract; the
/// `*_of` helpers keep scenario assertions one line each.
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

    /// Dispatched (node, attempt, did) tuples in emission order.
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

    /// Cancel-requested dispatch ids in emission order.
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
fn fork_workflow() -> StateMachineDefinition {
    workflow_of(vec![
        ("start", &["left", "right"], false),
        ("left", &["final"], false),
        ("right", &["final"], false),
        ("final", &[], true),
    ])
}

/// The designated final node is the only round product, whichever branch
/// replied last; the identical input sequence replays byte-identically.
#[test]
fn fork_join_uses_designated_final() {
    let designated = serde_json::json!({"state": "completed", "message": {"parts": [{"text": "designated"}]}});
    let branch = |name: &str| serde_json::json!({"state":"completed", "message":{"parts":[{"text":name}]}});
    let mut orders = Vec::new();
    for first in ["right", "left"] {
        let second = if first == "right" { "left" } else { "right" };
        let mut round = Round::open(fork_workflow(), 1);
        let input = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
        let opened = round.step(&[group_start(input)]);
        let (_, start_did) = dispatch_of(&opened, "start");

        let mut decisions = vec![opened];
        decisions.push(round.step(&[agent(
            "task-1",
            &start_did,
            2,
            AgentEventKind::Final,
            branch("start"),
        )]));
        let fanout = decisions.last().unwrap();
        let (_, first_did) = dispatch_of(fanout, first);
        let (_, second_did) = dispatch_of(fanout, second);
        decisions.push(round.step(&[agent("task-1", &first_did, 3, AgentEventKind::Final, branch(first))]));
        decisions.push(round.step(&[agent(
            "task-1",
            &second_did,
            4,
            AgentEventKind::Final,
            branch(second),
        )]));
        let joined = decisions.last().unwrap();
        let (_, final_did) = dispatch_of(joined, "final");
        decisions.push(round.step(&[agent("task-1", &final_did, 5, AgentEventKind::Final, designated.clone())]));
        orders.push(decisions);
    }

    // Whatever branch replies first, the product is the designated final
    // node payload, produced by exactly one terminal in exactly one round.
    let terminals: Vec<Vec<(TerminalStatus, Payload)>> =
        orders.iter().map(|chain| chain.iter().flat_map(Round::finished).collect()).collect();
    for (label, chain) in terminals.iter().enumerate() {
        assert_eq!(chain.len(), 1, "order {label} commits exactly one terminal");
        assert_eq!(
            chain[0],
            (TerminalStatus::Completed, Payload::Inline(designated.clone())),
            "order {label} takes the product from the designated final node"
        );
    }
    for decision in orders[0].iter().take(orders[0].len() - 1) {
        assert!(
            Round::finished(decision).is_empty(),
            "no decision before the final reply commits a terminal"
        );
    }
    assert_eq!(orders[0].len(), orders[1].len());

    // Replay law: same frozen state and ordered inputs reproduce the decision.
    let mut replay = Round::open(fork_workflow(), 1);
    let input = round_for(&replay.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = replay.step(&[group_start(input)]);
    assert_eq!(opened, orders[0][0], "replay of the same start is identical");
}

/// Publication rejects unpublishable workflows before any Task exists: no
/// single designated final output, node kinds excluded at D0, Judge/Loop
/// policies, cycles, multiple entries and unbound participants. Rejection is
/// deterministic and the attempt bound is clamped to the capability ceiling.
#[test]
fn validate_rejects_unpublishable_workflows() {
    let validator = FsmValidator;
    let caps = d0_capabilities();

    let not_final = workflow_of(vec![("start", &["a"], false), ("a", &["start"], false)]);
    let two_finals = workflow_of(vec![
        ("start", &["a", "b"], false),
        ("a", &[], true),
        ("b", &[], true),
    ]);
    let mut judge = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
    judge.nodes.get_mut("start").unwrap().judge = Some(JudgePolicy {
        outcomes: vec!["pass".into()],
        ..JudgePolicy::default()
    });
    let mut human = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
    human.nodes.get_mut("start").unwrap().kind = StateMachineNodeKind::HumanInput;
    let mut loopy = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
    loopy.nodes.get_mut("start").unwrap().kind = StateMachineNodeKind::Loop;
    loopy.nodes.get_mut("start").unwrap().loop_definition = Some(FixedLoopDefinition {
        mode: FixedLoopMode::Fixed,
        max_iterations: 2,
        entry_node: "final".into(),
        result_node: "final".into(),
        continue_outcomes: vec!["complete".into()],
        continue_display_name: None,
        break_outcomes: vec![],
        exhausted_outcome: "complete".into(),
        nodes: BTreeMap::new(),
    });
    let cycle = workflow_of(vec![
        ("start", &["a"], false),
        ("a", &["start"], false),
        ("final", &[], true),
    ]);
    let two_entries = workflow_of(vec![
        ("start", &["final"], false),
        ("side", &["final"], false),
        ("final", &[], true),
    ]);
    let isolated = workflow_of(vec![
        ("start", &["final"], false),
        ("final", &[], true),
        ("island", &["final"], false),
    ]);

    for (label, workflow) in [
        ("not_final", &not_final),
        ("two_finals", &two_finals),
        ("judge", &judge),
        ("human_input", &human),
        ("loop", &loopy),
        ("cycle", &cycle),
        ("two_entries", &two_entries),
        ("isolated", &isolated),
    ] {
        let spec = spec_for(workflow, 1);
        let first = validator.validate(&spec, workflow, &caps);
        let second = validator.validate(&spec, workflow, &caps);
        let code = match &first {
            Err(RunnerError::InvalidWorkflow { code }) => code.clone(),
            other => panic!("{label} must be rejected, got {other:?}"),
        };
        assert_eq!(first, second, "{label} rejection is deterministic");
        // Rejection happens before any Task exists: init on the same frozen
        // admission never yields a dispatchable state.
        assert!(
            matches!(
                FsmReducerRunner::d0().init(&EvaluationContext {
                    spec: &spec,
                    workflow,
                    now: TimestampMillis(NOW),
                }),
                Err(RunnerError::InvalidWorkflow { .. })
            ),
            "{label} cannot pass init"
        );
        let labels = [
            "final",
            "final_output",
            "judge",
            "human_input",
            "loop",
            "cycle",
            "entry",
        ];
        assert!(
            labels.iter().any(|needle| code.contains(needle)),
            "{label} code should name the violated rule, got {code}"
        );
    }

    // A node without a participant binding in the frozen spec is rejected.
    let mut unbound_spec = {
        let workflow = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
        spec_for(&workflow, 1)
    };
    unbound_spec.participants.pop();
    let workflow = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
    assert!(
        matches!(
            validator.validate(&unbound_spec, &workflow, &caps),
            Err(RunnerError::InvalidWorkflow { .. })
        ),
        "missing participant binding is rejected"
    );

    // Attempt bounds are clamped to the capability ceiling.
    let excessive = workflow_with_defaults(
        vec![("start", &["final"], false), ("final", &[], true)],
        Some(9),
    );
    let spec = spec_for(&excessive, 1);
    let validated = validator.validate(&spec, &excessive, &caps).unwrap();
    assert_eq!(validated.max_attempts["start"], caps.max_attempts);
    assert_eq!(validated.final_node.0, "final");
}

/// Same state plus the same ordered inputs always reproduce the decision,
/// including every generated dispatch identity.
#[test]
fn determinism_replay_law() {
    let mut decisions = Vec::new();
    let mut runs = [0, 1].map(|_| Round::open(fork_workflow(), 1));
    for round in runs.iter_mut() {
        let input = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
        let opened = round.step(&[group_start(input)]);
        let (_, start_did) = dispatch_of(&opened, "start");
        let mut chain = vec![opened];
        chain.push(round.step(&[agent(
            "task-1",
            &start_did,
            2,
            AgentEventKind::Final,
            serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}}),
        )]));
        let fanout = chain.last().unwrap();
        let (_, left_did) = dispatch_of(fanout, "left");
        let (_, right_did) = dispatch_of(fanout, "right");
        chain.push(round.step(&[agent(
            "task-1",
            &left_did,
            3,
            AgentEventKind::Delta,
            serde_json::json!({"value": "left delta"}),
        )]));
        chain.push(round.step(&[agent(
            "task-1",
            &left_did,
            4,
            AgentEventKind::Final,
            serde_json::json!({"state":"completed", "message":{"parts":[{"text":"left"}]}}),
        )]));
        chain.push(round.step(&[agent(
            "task-1",
            &right_did,
            5,
            AgentEventKind::Final,
            serde_json::json!({"state":"completed", "message":{"parts":[{"text":"right"}]}}),
        )]));
        let joined = chain.last().unwrap();
        let (_, final_did) = dispatch_of(joined, "final");
        chain.push(round.step(&[agent(
            "task-1",
            &final_did,
            6,
            AgentEventKind::Final,
            serde_json::json!({"state":"completed", "message":{"parts":[{"text":"designated"}]}}),
        )]));
        decisions.push(chain);
    }
    assert_eq!(
        decisions[0], decisions[1],
        "identical state and ordered inputs must give identical decisions"
    );
}

/// Bounded retry dispatches a stable new attempt identity after a definitive
/// failure and freezes the round after the bound is exhausted.
#[test]
fn bounded_retry_before_failing_the_round() {
    let workflow = workflow_with_defaults(
        vec![("start", &["final"], false), ("final", &[], true)],
        Some(2),
    );
    let mut round = Round::open(workflow, 1);
    let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(inputs)]);
    let (_, first_did) = dispatch_of(&opened, "start");

    let retried = round.step(&[agent(
        "task-1",
        &first_did,
        2,
        AgentEventKind::Error,
        serde_json::json!({"error": "boom"}),
    )]);
    let (attempt, retry_did) = dispatch_of(&retried, "start");
    assert_eq!(attempt, 2);
    assert_ne!(retry_did, first_did, "a retry is a new attempt identity");
    assert!(
        retry_did.starts_with("task-1.fsm:") && retry_did.contains("start:2"),
        "attempt identities derive from round/node/attempt, not time: {retry_did}"
    );
    assert!(Round::finished(&retried).is_empty());

    let exhausted = round.step(&[agent(
        "task-1",
        &retry_did,
        3,
        AgentEventKind::Error,
        serde_json::json!({"error": "boom again"}),
    )]);
    assert_eq!(Round::finished(&exhausted).len(), 1, "one failure terminal");
    let (status, Payload::Inline(result)) = &Round::finished(&exhausted)[0] else {
        panic!("failure result is inline");
    };
    assert_eq!(*status, TerminalStatus::Failed);
    assert!(
        result["frozen_reason"].as_str().unwrap().contains("start"),
        "the frozen reason names the exhausted node: {result}"
    );

    // Late duplicate reports do not open a second terminal.
    let dup = round.step(&[agent(
        "task-1",
        &retry_did,
        4,
        AgentEventKind::Error,
        serde_json::json!({"error": "duplicate"}),
    )]);
    assert!(dup.actions.is_empty(), "late duplicate stays inert after the terminal");
    let mut round2 = Round::open(
        workflow_with_defaults(vec![("start", &["final"], false), ("final", &[], true)], Some(2)),
        1,
    );
    let inputs = round_for(&round2.spec, "task-1", ROUND_DEADLINE_MS);
    let opened2 = round2.step(&[group_start(inputs)]);
    assert_eq!(opened2, opened);
}

/// A dispatch timeout cancels the old attempt, and only its own stop
/// evidence releases the slot for the next attempt.
#[test]
fn timeout_retries_only_after_stop_evidence() {
    let workflow = workflow_with_defaults(
        vec![("start", &["final"], false), ("final", &[], true)],
        Some(2),
    );
    let mut round = Round::open(workflow, 1);
    let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(inputs)]);
    let first_did = dispatch_of(&opened, "start").1;
    let dispatch_deadline = opened
        .next_deadlines
        .iter()
        .find(|deadline| {
            matches!(deadline.kind, DeadlineKind::Dispatch { .. })
        })
        .expect("each dispatch registers a timeout")
        .clone();

    let timed_out = round.step(&[RunnerInput::Timeout(dispatch_deadline)]);
    assert_eq!(Round::cancels(&timed_out), vec![first_did.clone()]);
    assert!(
        Round::dispatches(&timed_out).is_empty(),
        "an unverified old attempt never releases the slot"
    );
    assert!(Round::finished(&timed_out).is_empty());

    let evidence = round.step(&[agent(
        "task-1",
        &first_did,
        2,
        AgentEventKind::Error,
        serde_json::json!({"error": "observed late"}),
    )]);
    let (attempt, retry_did) = dispatch_of(&evidence, "start");
    assert_eq!(attempt, 2, "stop evidence admits the bounded retry");
    assert_ne!(retry_did, first_did);

    // Bound exhausted at the timeout: the round fails behind the barrier
    // after the old attempt's own stop evidence.
    let mut bounded = Round::open(
        workflow_with_defaults(vec![("start", &["final"], false), ("final", &[], true)], Some(1)),
        1,
    );
    let inputs = round_for(&bounded.spec, "task-2", ROUND_DEADLINE_MS);
    let opened = bounded.step(&[group_start(inputs)]);
    let did = dispatch_of(&opened, "start").1;
    let deadline = opened.next_deadlines[0].clone();
    let barrier = bounded.step(&[RunnerInput::Timeout(deadline)]);
    assert!(Round::finished(&barrier).is_empty());
    let closure = bounded.step(&[agent(
        "task-2",
        &did,
        2,
        AgentEventKind::Error,
        serde_json::json!({"error": "late stop"}),
    )]);
    assert_eq!(Round::finished(&closure).len(), 1);
    assert_eq!(Round::finished(&closure)[0].0, TerminalStatus::Failed);
}

/// INPUT_REQUIRED/AUTH_REQUIRED on a strict source freeze the failure, stop
/// new dispatches, cancel every in-flight attempt (including the one that
/// asked) and commit exactly one failure terminal after all stop evidence.
#[test]
fn unexpected_interaction_enters_stop_barrier() {
    for kind in [
        AgentEventKind::InputRequired {
            interaction: InteractionId("ask-1".into()),
        },
        AgentEventKind::AuthRequired {
            interaction: InteractionId("auth-1".into()),
        },
    ] {
        let mut round = Round::open(fork_workflow(), 1);
        let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
        let opened = round.step(&[group_start(inputs)]);
        let (_, start_did) = dispatch_of(&opened, "start");
        let fanned = round.step(&[agent(
            "task-1",
            &start_did,
            2,
            AgentEventKind::Final,
            serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}}),
        )]);
        let (_, left_did) = dispatch_of(&fanned, "left");
        let (_, right_did) = dispatch_of(&fanned, "right");

        let barrier = round.step(&[agent(
            "task-1",
            &left_did,
            3,
            kind.clone(),
            serde_json::json!({"ask": "-human"}),
        )]);
        assert_eq!(Round::dispatches(&barrier).len() + Round::cancels(&barrier).len(), 2);
        assert!(
            !barrier.actions.iter().any(|action| {
                matches!(action, ActionIntent::SetStatus { .. } | ActionIntent::Show { .. })
            }),
            "the strict source never enters the wait-for-input path"
        );
        assert_eq!(
            Round::cancels(&barrier),
            vec![left_did.clone(), right_did.clone()],
            "both in-flight attempts are canceled, the requester included"
        );
        assert!(Round::finished(&barrier).is_empty());

        let half_stopped = round.step(&[agent(
            "task-1",
            &right_did,
            4,
            AgentEventKind::Error,
            serde_json::json!({"error": "canceled"}),
        )]);
        assert!(
            Round::finished(&half_stopped).is_empty(),
            "no terminal while the requesting attempt is unverified"
        );

        let closure = round.step(&[agent(
            "task-1",
            &left_did,
            5,
            AgentEventKind::Error,
            serde_json::json!({"error": "canceled"}),
        )]);
        assert_eq!(Round::finished(&closure).len(), 1);
        let (status, Payload::Inline(result)) = &Round::finished(&closure)[0] else {
            panic!("inline failure result");
        };
        assert_eq!(*status, TerminalStatus::Failed);
        let reason = result["frozen_reason"].as_str().unwrap();
        let frozen_for_auth = matches!(
            kind,
            AgentEventKind::AuthRequired { .. }
        );
        if frozen_for_auth {
            assert!(reason.contains("auth_required"), "got {result}");
        } else {
            assert!(reason.contains("input_required"), "got {result}");
        }
    }
}

/// A parallel branch failure does not produce a whole-round terminal while
/// the sibling attempt is still in flight.
#[test]
fn sibling_in_flight_blocks_terminal() {
    let mut round = Round::open(fork_workflow(), 1);
    let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(inputs)]);
    let (_, start_did) = dispatch_of(&opened, "start");
    let fanned = round.step(&[agent(
        "task-1",
        &start_did,
        2,
        AgentEventKind::Final,
        serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}}),
    )]);
    let (_, left_did) = dispatch_of(&fanned, "left");
    let (_, right_did) = dispatch_of(&fanned, "right");

    let failed = round.step(&[agent(
        "task-1",
        &left_did,
        3,
        AgentEventKind::Error,
        serde_json::json!({"error": "left failed"}),
    )]);
    assert!(
        Round::finished(&failed).is_empty(),
        "no terminal while the sibling attempt is in flight"
    );
    assert_eq!(Round::cancels(&failed), vec![right_did.clone()]);

    let closure = round.step(&[agent(
        "task-1",
        &right_did,
        4,
        AgentEventKind::Error,
        serde_json::json!({"error": "right stopped"}),
    )]);
    assert_eq!(Round::finished(&closure).len(), 1);
    let (status, Payload::Inline(result)) = &Round::finished(&closure)[0] else {
        panic!("inline failure result");
    };
    assert_eq!(*status, TerminalStatus::Failed);
    assert!(
        result["frozen_reason"].as_str().unwrap().contains("left"),
        "the first frozen reason survives: {result}"
    );

    // Exactly one terminal over the full sequence, and duplicates stay inert.
    let total: usize = [&opened, &fanned, &failed, &closure]
        .iter()
        .map(|d| Round::finished(d).len())
        .sum();
    assert_eq!(total, 1, "exactly one terminal");
}

/// Round cancellation freezes immediately and commits only after every
/// in-flight attempt has stopped.
#[test]
fn round_cancel_waits_for_stop_evidence() {
    let mut round = Round::open(fork_workflow(), 1);
    let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(inputs)]);
    let fanned = round.fan_out("task-1", 2, &opened);
    let (_, left_did) = dispatch_of(&fanned, "left");
    let (_, right_did) = dispatch_of(&fanned, "right");

    let canceled = round.step(&[cancel("task-1")]);
    assert_eq!(Round::cancels(&canceled), vec![left_did.clone(), right_did.clone()]);
    assert!(Round::finished(&canceled).is_empty());

    let half = round.step(&[agent(
        "task-1",
        &left_did,
        4,
        AgentEventKind::Error,
        serde_json::json!({"error": "stopped"}),
    )]);
    assert!(Round::finished(&half).is_empty());

    let closure = round.step(&[agent(
        "task-1",
        &right_did,
        5,
        AgentEventKind::Error,
        serde_json::json!({"error": "stopped"}),
    )]);
    assert_eq!(Round::finished(&closure).len(), 1);
    assert_eq!(Round::finished(&closure)[0].0, TerminalStatus::Canceled);
}

/// The round task deadline runs the strict stop barrier; an already-expired
/// admission fails without ever dispatching work.
#[test]
fn execution_deadline_fails_the_round() {
    let mut round = Round::open(fork_workflow(), 1);
    let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(inputs)]);
    let fanned = round.fan_out("task-1", 2, &opened);
    let (_, left_did) = dispatch_of(&fanned, "left");
    let (_, right_did) = dispatch_of(&fanned, "right");

    let expired = round.step(&[task_deadline("task-1")]);
    assert_eq!(Round::cancels(&expired), vec![left_did.clone(), right_did.clone()]);
    assert!(Round::finished(&expired).is_empty());

    let closure = round.step(&[agent(
        "task-1",
        &left_did,
        4,
        AgentEventKind::Error,
        serde_json::json!({"error": "stopped"}),
    )]);
    assert!(Round::finished(&closure).is_empty());

    let closure = round.step(&[agent(
        "task-1",
        &right_did,
        5,
        AgentEventKind::Error,
        serde_json::json!({"error": "stopped"}),
    )]);
    assert_eq!(Round::finished(&closure).len(), 1);
    assert_eq!(Round::finished(&closure)[0].0, TerminalStatus::Failed);

    // An admission whose execution window already elapsed never creates work.
    let mut late = Round::open(fork_workflow(), 1);
    let inputs = round_for(&late.spec, "task-late", -1);
    let decision = late.step(&[group_start(inputs)]);
    assert!(Round::dispatches(&decision).is_empty());
    assert_eq!(Round::finished(&decision).len(), 1);
    assert_eq!(Round::finished(&decision)[0].0, TerminalStatus::Failed);
}

/// An in-flight round keeps the workflow/binding revision it was frozen
/// with; a newer published version cannot leak into its decisions, and only
/// a fresh context under the new revision uses it.
#[test]
fn old_context_replay_pins_workflow() {
    let v1 = workflow_of(vec![("start", &["final"], false), ("final", &[], true)]);
    let v2 = workflow_of(vec![
        ("start", &["draft", "final"], false),
        ("draft", &["final"], false),
        ("final", &[], true),
    ]);
    let mut round = Round::open(v1.clone(), 1);
    let inputs = round_for(&round.spec, "task-1", ROUND_DEADLINE_MS);
    let opened = round.step(&[group_start(inputs)]);
    let (_, start_did) = dispatch_of(&opened, "start");

    let newer_spec = spec_for(&v2, 2);
    // Same session digest but changed workflow content: rejected.
    let result = round.runner.reduce(
        &round.context(&round.spec, &v2),
        &round.state,
        &[agent(
            "task-1",
            &start_did,
            2,
            AgentEventKind::Final,
            serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}}),
        )],
    );
    assert!(
        matches!(result, Err(RunnerError::InvalidWorkflow { .. })),
        "a newer workflow revision cannot leak into an in-flight round"
    );
    // New spec digest (binding change): rejected as well.
    let result = round.runner.reduce(
        &round.context(&newer_spec, &v2),
        &round.state,
        &[agent(
            "task-1",
            &start_did,
            2,
            AgentEventKind::Final,
            serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}}),
        )],
    );
    assert!(
        matches!(result, Err(RunnerError::InvalidWorkflow { .. })),
        "a rebound session cannot leak into an in-flight round"
    );

    // The pinned round keeps running on the original revision.
    let pinned = round.step(&[agent(
        "task-1",
        &start_did,
        2,
        AgentEventKind::Final,
        serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}}),
    )]);
    let nodes: Vec<String> = Round::dispatches(&pinned)
        .into_iter()
        .map(|(node, _, _)| node)
        .collect();
    assert_eq!(nodes, vec!["final".to_string()], "v1 wiring is pinned");

    // A fresh context under the new revision admits the new wiring.
    let mut fresh = Round::open(v2.clone(), 2);
    let inputs = round_for(&fresh.spec, "task-2", ROUND_DEADLINE_MS);
    let opened2 = fresh.step(&[group_start(inputs)]);
    let nodes: Vec<String> = Round::dispatches(&opened2)
        .into_iter()
        .map(|(node, _, _)| node)
        .collect();
    assert_eq!(nodes, vec!["start".to_string()]);
    let (_, start2) = dispatch_of(&opened2, "start");
    let drafted = fresh.step(&[agent(
        "task-2",
        &start2,
        3,
        AgentEventKind::Final,
        serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}}),
    )]);
    // The designated final node waits for every predecessor: only the draft
    // branch runs after the fan-out.
    let nodes: Vec<String> = Round::dispatches(&drafted)
        .into_iter()
        .map(|(node, _, _)| node)
        .collect();
    assert_eq!(nodes, vec!["draft".to_string()]);
    let (_, draft2) = dispatch_of(&drafted, "draft");
    let joined = fresh.step(&[agent(
        "task-2",
        &draft2,
        4,
        AgentEventKind::Final,
        serde_json::json!({"state":"completed", "message":{"parts":[{"text":"draft"}]}}),
    )]);
    let nodes: Vec<String> = Round::dispatches(&joined)
        .into_iter()
        .map(|(node, _, _)| node)
        .collect();
    assert_eq!(nodes, vec!["final".to_string()]);
}

impl Round {
    /// Finalize the `start` node from its dispatch in `opened` and return the
    /// follow-up decision (fan-out for forks, the next node for serial DAGs).
    fn fan_out(&mut self, tid: &str, seq: u64, opened: &Decision) -> Decision {
        let did = opened
            .actions
            .iter()
            .find_map(|action| match action {
                ActionIntent::StartDispatch(dispatch) if dispatch.node.0 == "start" => {
                    Some(dispatch.did.0.clone())
                }
                _ => None,
            })
            .expect("the start node dispatch");
        self.step(&[agent(
            tid,
            &did,
            seq,
            AgentEventKind::Final,
            serde_json::json!({"state":"completed", "message":{"parts":[{"text":"start"}]}}),
        )])
    }
}
