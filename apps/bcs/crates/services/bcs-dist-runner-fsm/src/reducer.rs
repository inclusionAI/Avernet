//! Pure strict-FSM round reducer (plan T5).
//!
//! [`FsmReducerRunner`] folds [`RunnerInput`] batches for one frozen group
//! round into dispatch/cancel/complete intents. It performs no IO, reads no
//! clock and generates no random identities: `now` and each input enter
//! explicitly, every attempt identity derives from the round key, node name
//! and attempt counter, and the same state plus the same ordered inputs
//! always reproduce the same [`Decision`].
//!
//! Stop semantics (D0): a failure, cancellation, deadline, or an
//! INPUT_REQUIRED/AUTH_REQUIRED report freezes the round, stops new
//! dispatches and requests cancellation of every in-flight attempt. No
//! terminal is produced while any attempt is still in flight or unverified;
//! after all stop evidence lands exactly one terminal is committed. The
//! designated final node is the only source of the round product.

use bcs_dist_model::*;
use bcs_domain::collaboration::StateMachineDefinition;
use bcs_service_api::core::dist_runner::{
    ActionIntent, Decision, DistRunnerCoreService, EvaluationContext, FsmCapabilities,
    FsmValidatorCoreService, RunnerError, RunnerInput, ValidatedWorkflow,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest as Sha256Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

/// Runner state envelope format written by this reducer.
const FSM_STATE_FORMAT: u32 = 1;

/// Capabilities are injected so the pinned profile stays testable; `d0()`
/// builds the D0 threshold runner used by production wiring. Validation is
/// delegated to the shared [`crate::validator::FsmValidator`] so the group
/// directory and the runner enforce one publication gate.
pub struct FsmReducerRunner {
    capabilities: FsmCapabilities,
    validator: crate::validator::FsmValidator,
}

impl FsmReducerRunner {
    pub fn new(capabilities: FsmCapabilities) -> Self {
        Self {
            capabilities,
            validator: crate::validator::FsmValidator,
        }
    }

    pub fn d0() -> Self {
        Self::new(crate::validator::d0_capabilities())
    }

    /// Stable content digest of a resolved definition. BTreeMap-ordered
    /// serialization (\`serde_json\` without \`preserve_order\`) makes this a
    /// pure function of the definition content.
    fn content_digest(workflow: &StateMachineDefinition) -> Result<String, RunnerError> {
        let bytes = serde_json::to_vec(workflow)
            .map_err(|_| RunnerError::CorruptState { code: "workflow serialization".into() })?;
        Ok(hex::encode(Sha256::digest(bytes)))
    }

    /// Stable digest over the admitted round input, used to recognize the
    /// same request on replay and to refuse a different round in this state.
    fn input_digest(input: &RoundInput) -> Result<String, RunnerError> {
        let bytes = serde_json::to_vec(input)
            .map_err(|_| RunnerError::CorruptState { code: "round input serialization".into() })?;
        Ok(hex::encode(Sha256::digest(bytes)))
    }


    fn round_error(code: impl Into<String>) -> RunnerError {
        RunnerError::InvalidWorkflow { code: code.into() }
    }
}

/// Persisted reducer state: the pinned workflow view plus at most one
/// installed round. The reducer trusts only this pinned copy: reduce refuses
/// a context whose spec or definition digest no longer matches.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct CoreState {
    spec_digest: String,
    definition_digest: String,
    workflow: ValidatedWorkflow,
    round: Option<RoundCore>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct RoundCore {
    key: RoundKey,
    parts: Vec<RoundPart>,
    input_digest: String,
    execution_deadline: TimestampMillis,
    phase: Phase,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    frozen_reason: Option<String>,
    /// Dispatches whose own stop evidence has not landed yet.
    stop_pending: BTreeSet<String>,
    nodes: BTreeMap<String, NodeRun>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    terminal: Option<TerminalRecord>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum Phase {
    Running,
    /// Frozen; the terminal status is already decided and waits only for
    /// stop evidence. New dispatches are stopped.
    Stopping { status: TerminalStatus },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct NodeRun {
    status: NodeStatus,
    attempts_used: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    artifact: Option<Payload>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
enum NodeStatus {
    Pending,
    InFlight { did: String, attempt: u32 },
    /// Cancellation requested on a dispatch-timeout attempt; retry or
    /// failure only after its own stop evidence lands.
    Canceling { did: String, attempt: u32 },
    Succeeded,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct TerminalRecord {
    status: TerminalStatus,
    result: Payload,
}

impl DistRunnerCoreService for FsmReducerRunner {
    fn init(&self, context: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        let workflow = self
            .validator
            .validate(context.spec, context.workflow, &self.capabilities)?;
        let core = CoreState {
            spec_digest: context.spec.digest.0.clone(),
            definition_digest: Self::content_digest(context.workflow)?,
            workflow,
            round: None,
        };
        Ok(RunnerState {
            format_version: FSM_STATE_FORMAT,
            value: serde_json::to_value(core)
                .map_err(|_| RunnerError::CorruptState { code: "state serialization".into() })?,
        })
    }

    fn reduce(
        &self,
        context: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        if state.format_version != FSM_STATE_FORMAT {
            return Err(RunnerError::UnsupportedFormat {
                format: state.format_version,
            });
        }
        let mut core: CoreState = serde_json::from_value(state.value.clone())
            .map_err(|_| RunnerError::CorruptState { code: "unrecognized runner state".into() })?;
        // Pinning: an in-flight round keeps the session spec and workflow
        // revision it was frozen with. A later published revision cannot
        // leak into its decisions; only a fresh context uses the new one.
        if core.spec_digest != context.spec.digest.0 {
            return Err(Self::round_error(
                "session spec replaced under an in-flight round",
            ));
        }
        if core.definition_digest != Self::content_digest(context.workflow)? {
            return Err(Self::round_error(
                "workflow revision changed under an in-flight round",
            ));
        }
        if core.round.as_ref().is_some_and(|round| round.terminal.is_some()) {
            // The terminal committed exactly once; later inputs stay inert.
            return Ok(Decision {
                next_state: state.clone(),
                actions: Vec::new(),
                next_deadlines: Vec::new(),
            });
        }

        let mut actions: Vec<ActionIntent> = Vec::new();
        let mut deadlines: BTreeMap<String, Deadline> = BTreeMap::new();
        for input in inputs {
            self.apply(context, &mut core, input, &mut actions, &mut deadlines)?;
        }
        let next_state = RunnerState {
            format_version: FSM_STATE_FORMAT,
            value: serde_json::to_value(&core).map_err(|_| RunnerError::CorruptState {
                code: "state serialization".into(),
            })?,
        };
        Ok(Decision {
            next_state,
            actions,
            next_deadlines: deadlines.into_values().collect(),
        })
    }
}

impl FsmReducerRunner {
    fn apply(
        &self,
        context: &EvaluationContext<'_>,
        core: &mut CoreState,
        input: &RunnerInput,
        actions: &mut Vec<ActionIntent>,
        deadlines: &mut BTreeMap<String, Deadline>,
    ) -> Result<(), RunnerError> {
        let Some(round) = core.round.as_mut() else {
            // Without an installed round only a GroupStart installs one;
            // anything else has no round to act on.
            let RunnerInput::GroupStart { input } = input else {
                return Ok(());
            };
            return self.install_round(context, core, input, actions, deadlines);
        };
        if round.terminal.is_some() {
            return Ok(());
        }
        match input {
            RunnerInput::GroupStart { input } => {
                let digest = Self::input_digest(input)?;
                if round.input_digest == digest {
                    // Same request replay: the installed round already owns
                    // the dispatches; nothing may be re-created.
                    return Ok(());
                }
                return Err(Self::round_error(
                    "a different round cannot replace the one in this runner state",
                ));
            }
            RunnerInput::Agent { tid, event } => {
                if tid.0 != round.key.tid.0 {
                    return Ok(());
                }
                self.on_agent(context, round, &core.workflow.clone(), event, actions, deadlines)?;
            }
            RunnerInput::Cancel { tid } => {
                if tid.0 != round.key.tid.0 {
                    return Ok(());
                }
                if matches!(round.phase, Phase::Running) {
                    self.freeze_round(round, TerminalStatus::Canceled, "canceled by request", actions);
                }
            }
            RunnerInput::Timeout(deadline) => {
                if let DeadlineKind::Task { tid } = &deadline.kind
                    && tid.0 != round.key.tid.0
                {
                    return Ok(());
                }
                self.on_timeout(round, deadline, actions);
            }
            RunnerInput::Start { tid, .. } => {
                if tid.0 != round.key.tid.0 {
                    return Ok(());
                }
                return Err(Self::round_error(
                    "legacy start is not a strict group round input",
                ));
            }
            RunnerInput::HumanReply { tid, .. } => {
                if tid.0 != round.key.tid.0 {
                    return Ok(());
                }
                return Err(Self::round_error(
                    "strict group rounds never wait for human input; a reply is not admitted",
                ));
            }
        }
        Ok(())
    }

    /// Install the admitted round and dispatch the entry node; an admission
    /// whose execution window already elapsed fails without creating work.
    fn install_round(
        &self,
        context: &EvaluationContext<'_>,
        core: &mut CoreState,
        input: &RoundInput,
        actions: &mut Vec<ActionIntent>,
        deadlines: &mut BTreeMap<String, Deadline>,
    ) -> Result<(), RunnerError> {
        if input.key.sid.0 != context.spec.sid.0 || input.key.scope != context.spec.scope {
            return Err(Self::round_error(
                "round key does not match the frozen session scope",
            ));
        }
        if input.workflow_ref != context.spec.workflow {
            return Err(Self::round_error(
                "round input does not carry the pinned workflow revision",
            ));
        }
        let mut nodes = BTreeMap::new();
        for id in context.workflow.nodes.keys() {
            nodes.insert(
                id.clone(),
                NodeRun {
                    status: NodeStatus::Pending,
                    attempts_used: 0,
                    artifact: None,
                },
            );
        }
        let mut round = RoundCore {
            key: input.key.clone(),
            parts: input.ordered_parts.clone(),
            input_digest: Self::input_digest(input)?,
            execution_deadline: input.execution_deadline,
            phase: Phase::Running,
            frozen_reason: None,
            stop_pending: BTreeSet::new(),
            nodes,
            terminal: None,
        };
        let deadline_id = DeadlineId(format!(
            "fsm:task:{}:{}",
            input.key.tid.0, input.key.generation.get()
        ));
        deadlines.insert(
            deadline_id.0.clone(),
            Deadline {
                id: deadline_id,
                at: input.execution_deadline,
                kind: DeadlineKind::Task { tid: input.key.tid.clone() },
            },
        );
        if context.now >= round.execution_deadline {
            let generation = round.key.generation.get();
            self.commit(
                &mut round,
                TerminalStatus::Failed,
                Payload::Inline(serde_json::json!({
                    "frozen_reason": "execution deadline elapsed before dispatch",
                    "generation": generation,
                })),
                actions,
            );
            core.round = Some(round);
            return Ok(());
        }
        let workflow = core.workflow.clone();
        self.schedule_ready(context, &mut round, &workflow, actions, deadlines)?;
        core.round = Some(round);
        Ok(())
    }

    /// Dispatch every pending node whose predecessors all succeeded. The
    /// phase gates new dispatches once the round is stopping.
    fn schedule_ready(
        &self,
        context: &EvaluationContext<'_>,
        round: &mut RoundCore,
        workflow: &ValidatedWorkflow,
        actions: &mut Vec<ActionIntent>,
        deadlines: &mut BTreeMap<String, Deadline>,
    ) -> Result<(), RunnerError> {
        if !matches!(round.phase, Phase::Running) {
            return Ok(());
        }
        let mut ready: Vec<String> = Vec::new();
        for id in round.nodes.keys() {
            if !matches!(round.nodes[id].status, NodeStatus::Pending) {
                continue;
            }
            if workflow_predecessors_ready(context.workflow, round, id) {
                ready.push(id.clone());
            }
        }
        for id in ready {
            let bound = workflow.max_attempts.get(&id).copied().unwrap_or(1).max(1);
            if round.nodes[&id].attempts_used >= bound {
                continue;
            }
            let (attempt, did) = {
                let Some(run) = round.nodes.get_mut(&id) else { continue };
                run.attempts_used += 1;
                let attempt = run.attempts_used;
                let did = attempt_did(&round.key, &id, attempt);
                run.status = NodeStatus::InFlight {
                    did: did.0.clone(),
                    attempt,
                };
                (attempt, did)
            };
            let request = self.request_payload(context, round, workflow, &id, attempt)?;
            let deadline_id = DeadlineId(format!(
                "fsm:dispatch:{}:{}:{id}:{attempt}",
                round.key.tid.0,
                round.key.generation.get()
            ));
            let timeout = workflow.node_timeout_millis.get(&id).copied().unwrap_or(
                context.spec.limits.task_timeout_millis,
            );
            let target = participant_of(context, &id)?;
            actions.push(ActionIntent::StartDispatch(DispatchSpec {
                schema_version: 1,
                sid: context.spec.sid.clone(),
                tid: round.key.tid.clone(),
                did: did.clone(),
                node: NodeId(id.clone()),
                attempt: attempt,
                target,
                pool: pool_of(context, &id)?,
                capabilities: capabilities_of(context, &id)?,
                request,
                deadline: TimestampMillis(context.now.0 + timeout as i64),
            }));
            deadlines.insert(
                deadline_id.0.clone(),
                Deadline {
                    id: deadline_id,
                    at: TimestampMillis(context.now.0 + timeout as i64),
                    kind: DeadlineKind::Dispatch {
                        tid: round.key.tid.clone(),
                        did: did.clone(),
                    },
                },
            );
        }
        Ok(())
    }

    /// Deterministic dispatch body: pinned round parts plus the verified
    /// upstream artifacts, keyed and ordered by node name.
    fn request_payload(
        &self,
        context: &EvaluationContext<'_>,
        round: &RoundCore,
        _workflow: &ValidatedWorkflow,
        node: &str,
        attempt: u32,
    ) -> Result<Payload, RunnerError> {
        let mut upstream = serde_json::Map::new();
        for (id, run) in &round.nodes {
            if let Some(artifact) = &run.artifact
                && is_predecessor(context.workflow, node, id)
            {
                let product = match artifact {
                    Payload::Inline(value) => predecessor_product(value).map(Payload::Inline),
                    Payload::Ref(_) => None,
                }.ok_or_else(|| Self::round_error("invalid_upstream_product"))?;
                upstream.insert(id.clone(), serde_json::json!(product));
            }
        }
        Ok(Payload::Inline(serde_json::json!({
            "schema_version": 1,
            "generation": round.key.generation.get(),
            "node": node,
            "attempt": attempt,
            "parts": &round.parts,
            "upstream": upstream,
        })))
    }

    fn on_agent(
        &self,
        context: &EvaluationContext<'_>,
        round: &mut RoundCore,
        workflow: &ValidatedWorkflow,
        event: &AgentEvent,
        actions: &mut Vec<ActionIntent>,
        deadlines: &mut BTreeMap<String, Deadline>,
    ) -> Result<(), RunnerError> {
        if matches!(round.phase, Phase::Stopping { .. }) {
            // Stop evidence: a terminal-kind report from a canceled attempt.
            // Only an Error or Final proves this attempt stopped.
            if !matches!(event.kind, AgentEventKind::Error | AgentEventKind::Final) {
                return Ok(());
            }
            if round.stop_pending.remove(&event.did.0)
                && round.stop_pending.is_empty()
            {
                let reason = round
                    .frozen_reason
                    .clone()
                    .unwrap_or_else(|| "stopped".into());
                let status = match round.phase {
                    Phase::Stopping { status } => status,
                    Phase::Running => TerminalStatus::Failed,
                };
                self.commit(
                    round,
                    status,
                    Payload::Inline(serde_json::json!({
                        "frozen_reason": reason,
                        "generation": round.key.generation.get(),
                    })),
                    actions,
                );
            }
            return Ok(());
        }
        let Some(node_id) = alive_node_of(round, &event.did.0) else {
            // Duplicate reports and foreign dispatch ids stay inert.
            return Ok(());
        };
        match event.kind {
            AgentEventKind::Final => match round.nodes[&node_id].status.clone() {
                NodeStatus::InFlight { .. } => {
                    if workflow.final_node.0 == node_id {
                        // Designated final node success: the validated DAG
                        // guarantees a quiet tree here.
                        let still_alive = round
                            .nodes
                            .iter()
                            .any(|(id, run)| {
                                id != &node_id
                                    && matches!(
                                        run.status,
                                        NodeStatus::InFlight { .. } | NodeStatus::Canceling { .. }
                                    )
                            });
                        if still_alive {
                            // Unreachable for validated DAGs; refuse to
                            // commit a terminal above unverified work.
                            self.freeze_round(
                                round,
                                TerminalStatus::Failed,
                                "final node success raced unfinished work",
                                actions,
                            );
                            return Ok(());
                        }
                        self.complete_final(round, &workflow.final_node, &event.payload, actions);
                    } else if let Some(run) = round.nodes.get_mut(&node_id) {
                        run.artifact = Some(event.payload.clone());
                        run.status = NodeStatus::Succeeded;
                        self.schedule_ready(context, round, workflow, actions, deadlines)?;
                    }
                }
                NodeStatus::Canceling { .. } => {
                    // A timed-out attempt raced to verified success: its
                    // own stop fact is the Final report.
                    if workflow.final_node.0 == node_id {
                        self.complete_final(round, &workflow.final_node, &event.payload, actions);
                    } else if let Some(run) = round.nodes.get_mut(&node_id) {
                        run.artifact = Some(event.payload.clone());
                        run.status = NodeStatus::Succeeded;
                        self.schedule_ready(context, round, workflow, actions, deadlines)?;
                    }
                }
                _ => {}
            },
            AgentEventKind::Error => {
                let attempt = match &round.nodes[&node_id].status {
                    NodeStatus::InFlight { attempt, .. } | NodeStatus::Canceling { attempt, .. } => {
                        *attempt
                    }
                    _ => return Ok(()),
                };
                let bound = workflow.max_attempts.get(&node_id).copied().unwrap_or(1).max(1);
                if attempt >= bound {
                    if let Some(run) = round.nodes.get_mut(&node_id) {
                        run.status = NodeStatus::Failed;
                    }
                    let reason = format!("node {node_id} exhausted its attempt bound of {bound}");
                    self.freeze_round(round, TerminalStatus::Failed, &reason, actions);
                } else {
                    // Bounded retry: the definitive Error is the old
                    // attempt's own stop fact, so the slot is free.
                    let (new_attempt, new_did) = {
                        let Some(run) = round.nodes.get_mut(&node_id) else { return Ok(()) };
                        run.attempts_used = attempt + 1;
                        let new_attempt = run.attempts_used;
                        let new_did = attempt_did(&round.key, &node_id, new_attempt);
                        run.status = NodeStatus::InFlight {
                            did: new_did.0.clone(),
                            attempt: new_attempt,
                        };
                        (new_attempt, new_did)
                    };
                    let request = self.request_payload(context, round, workflow, &node_id, new_attempt)?;
                    let deadline_id = DeadlineId(format!(
                        "fsm:dispatch:{}:{}:{node_id}:{new_attempt}",
                        round.key.tid.0,
                        round.key.generation.get()
                    ));
                    let timeout = workflow
                        .node_timeout_millis
                        .get(&node_id)
                        .copied()
                        .unwrap_or(context.spec.limits.task_timeout_millis);
                    actions.push(ActionIntent::StartDispatch(DispatchSpec {
                        schema_version: 1,
                        sid: context.spec.sid.clone(),
                        tid: round.key.tid.clone(),
                        did: Did(new_did.0.clone()),
                        node: NodeId(node_id.clone()),
                        attempt: new_attempt,
                        target: participant_of(context, &node_id)?,
                        pool: pool_of(context, &node_id)?,
                        capabilities: capabilities_of(context, &node_id)?,
                        request,
                        deadline: TimestampMillis(context.now.0 + timeout as i64),
                    }));
                    deadlines.insert(
                        deadline_id.0.clone(),
                        Deadline {
                            id: deadline_id,
                            at: TimestampMillis(context.now.0 + timeout as i64),
                            kind: DeadlineKind::Dispatch {
                                tid: round.key.tid.clone(),
                                did: Did(new_did.0.clone()),
                            },
                        },
                    );
                }
            }
            AgentEventKind::InputRequired { .. } | AgentEventKind::AuthRequired { .. } => {
                let code = match event.kind {
                    AgentEventKind::InputRequired { .. } => "input_required",
                    _ => "auth_required",
                };
                // Strict source: freeze immediately; the remote attempt is
                // still alive (it waits for a reply), so it is also canceled
                // and its own stop evidence is required. No wait-for-input
                // path is entered.
                if !matches!(round.nodes[&node_id].status, NodeStatus::InFlight { .. }) {
                    // Already canceling after a timeout: the cancel stands.
                    return Ok(());
                }
                let reason = format!("{code} on node {node_id}");
                self.freeze_round(round, TerminalStatus::Failed, &reason, actions);
            }
            AgentEventKind::Delta | AgentEventKind::Thinking | AgentEventKind::Tool
            | AgentEventKind::Snapshot => {
                // Streaming facts do not drive policy and do not change state.
            }
        }
        Ok(())
    }

    fn on_timeout(
        &self,
        round: &mut RoundCore,
        deadline: &Deadline,
        actions: &mut Vec<ActionIntent>,
    ) {
        match &deadline.kind {
            DeadlineKind::Task { tid } => {
                if tid.0 != round.key.tid.0 || !matches!(round.phase, Phase::Running) {
                    return;
                }
                if round.terminal.is_some() {
                    return;
                }
                self.freeze_round(
                    round,
                    TerminalStatus::Failed,
                    "execution deadline exceeded before all work completed",
                    actions,
                );
            }
            DeadlineKind::Dispatch { tid, did } => {
                if tid.0 != round.key.tid.0 || !matches!(round.phase, Phase::Running) {
                    return;
                }
                let Some(node_id) = alive_node_of(round, &did.0) else {
                    return; // stale deadline for an already-stopped attempt
                };
                if matches!(round.nodes[&node_id].status, NodeStatus::InFlight { .. }) {
                    let (did_string, attempt) = match round.nodes[&node_id].status.clone() {
                        NodeStatus::InFlight { did, attempt } => (did, attempt),
                        _ => return,
                    };
                    // The timeout alone proves nothing about the remote: the
                    // attempt is unverified, so cancel it and keep the slot
                    // occupied until its own stop evidence lands.
                    if let Some(run) = round.nodes.get_mut(&node_id) {
                        run.status = NodeStatus::Canceling {
                            did: did_string,
                            attempt,
                        };
                    }
                    actions.push(ActionIntent::CancelDispatch {
                        did: did.clone(),
                        intent: CancelIntent {
                            request_id: RequestId(format!(
                                "fsm:cancel:{}:{}:{}",
                                round.key.tid.0,
                                round.key.generation.get(),
                                did.0
                            )),
                            reason: Some("dispatch timeout; stop evidence required".into()),
                        },
                    });
                }
            }
            DeadlineKind::Snapshot { .. } => {}
        }
    }

    /// Freeze the round: stop dispatching, cancel every alive attempt (the
    /// triggering attempt's own stop evidence still has to land), and commit
    /// exactly one terminal when the last evidence arrives.
    fn freeze_round(
        &self,
        round: &mut RoundCore,
        status: TerminalStatus,
        reason: &str,
        actions: &mut Vec<ActionIntent>,
    ) {
        round.phase = Phase::Stopping { status };
        round.frozen_reason = Some(reason.to_string());
        let alive: BTreeSet<String> = round
            .nodes
            .values()
            .filter_map(|run| match &run.status {
                NodeStatus::InFlight { did, .. } | NodeStatus::Canceling { did, .. } => {
                    Some(did.clone())
                }
                _ => None,
            })
            .collect();
        for did in &alive {
            actions.push(ActionIntent::CancelDispatch {
                did: Did(did.clone()),
                intent: CancelIntent {
                    request_id: RequestId(format!(
                        "fsm:cancel:{}:{}:{did}",
                        round.key.tid.0,
                        round.key.generation.get()
                    )),
                    reason: Some(reason.to_string()),
                },
            });
        }
        round.stop_pending = alive;
        if round.stop_pending.is_empty() {
            let reason = round.frozen_reason.clone().unwrap_or_else(|| "stopped".into());
            self.commit(
                round,
                status,
                Payload::Inline(serde_json::json!({
                    "frozen_reason": reason,
                    "generation": round.key.generation.get(),
                })),
                actions,
            );
        }
    }

    fn complete_final(
        &self,
        round: &mut RoundCore,
        node: &NodeId,
        result: &Payload,
        actions: &mut Vec<ActionIntent>,
    ) {
        let Some(artifacts) = final_artifacts(result) else {
            // A definitive Final proves the attempt stopped, but unusable
            // content is not a successful public product.
            self.commit(
                round,
                TerminalStatus::Failed,
                Payload::Inline(serde_json::json!({"code": "invalid_final_artifact"})),
                actions,
            );
            return;
        };
        let budget = bcs_service_api::port::repo::distributed::group_round::RoundBudgetProfile::v1();
        let parts = artifacts.iter()
            .filter_map(|answer| answer.get("parts").unwrap_or(answer).as_array()).flatten();
        if parts.clone().count() > budget.max_page_rows as usize
            || parts.into_iter().any(|part| serde_json::to_vec(part)
                .map_or(true, |encoded| encoded.len() > budget.feed_event_bytes))
        {
            self.commit(round, TerminalStatus::Failed,
                Payload::Inline(serde_json::json!({"code": "PUBLIC_RESULT_TOO_LARGE"})), actions);
            return;
        }
        for (index, content) in artifacts.into_iter().enumerate() {
            actions.push(ActionIntent::Show {
                tid: round.key.tid.clone(),
                node: node.clone(),
                artifact_id: ArtifactId(format!("final-answer-{index}")),
                class: OutputClass::FinalAnswer,
                update: ArtifactUpdate::Upsert { content: Payload::Inline(content) },
            });
        }
        self.commit(round, TerminalStatus::Completed, result.clone(), actions);
    }

    /// Commit the round terminal exactly once.
    fn commit(
        &self,
        round: &mut RoundCore,
        status: TerminalStatus,
        result: Payload,
        actions: &mut Vec<ActionIntent>,
    ) {
        if round.terminal.is_some() {
            return;
        }
        round.stop_pending.clear();
        round.terminal = Some(TerminalRecord {
            status,
            result: result.clone(),
        });
        round.phase = Phase::Stopping { status };
        actions.push(ActionIntent::FinishTask {
            tid: round.key.tid.clone(),
            status,
            result,
        });
    }
}

/// Project the complete A2A answer into the neutral text/data profile.
/// Validate every part before emitting any action; never drop unsupported
/// content or let a partial artifact become the round product.
fn final_artifacts(result: &Payload) -> Option<Vec<serde_json::Value>> {
    let Payload::Inline(value) = result else { return None };
    if value.get("state")?.as_str()? != "completed" {
        return None;
    }
    fn parts_of(artifact: &serde_json::Value) -> Option<serde_json::Value> {
        let parts = artifact.get("parts")?.as_array()?;
        if parts.is_empty() {
            return None;
        }
        let parts: Vec<RoundPart> = parts.iter().map(|part| {
            // The neutral profile does not represent file output or part
            // properties; rejecting them is safer than losing information.
            let content = part.as_object()?;
            if content.len() != 1 {
                return None;
            }
            if let Some(text) = content.get("text") {
                return Some(RoundPart::Text { text: text.as_str()?.to_owned() });
            }
            Some(RoundPart::Data { data: content.get("data")?.clone() })
        }).collect::<Option<_>>()?;
        serde_json::to_value(parts).ok()
    }
    if let Some(artifacts) = value.get("artifacts") {
        let artifacts = artifacts.as_array()?;
        if artifacts.is_empty() {
            return None;
        }
        let mut ids = BTreeSet::new();
        artifacts.iter().map(|artifact| {
            let id = artifact.get("artifactId")?.as_str()?;
            if id.is_empty() || !ids.insert(id) { return None; }
            Some(serde_json::json!({"artifact_id": id, "parts": parts_of(artifact)?}))
        }).collect()
    } else {
        Some(vec![parts_of(value.get("message")?)?])
    }
}

/// Dispatch identity derives from the round key, node name and attempt
/// counter — never from time or randomness — so replays reproduce identical
/// identities.
fn attempt_did(key: &RoundKey, node: &str, attempt: u32) -> Did {
    Did(format!(
        "{}.fsm:{}:{node}:{attempt}",
        key.tid.0,
        key.generation.get()
    ))
}

/// The node currently holding `did`, if any attempt is alive on it.
fn alive_node_of(round: &RoundCore, did: &str) -> Option<String> {
    round
        .nodes
        .iter()
        .find_map(|(id, run)| match &run.status {
            NodeStatus::InFlight { did: live, .. } | NodeStatus::Canceling { did: live, .. }
                if live == did =>
            {
                Some(id.clone())
            }
            _ => None,
        })
}

/// True when every `complete`-transition predecessor of `node` succeeded.
fn workflow_predecessors_ready(
    workflow: &StateMachineDefinition,
    round: &RoundCore,
    node: &str,
) -> bool {
    for (id, definition) in &workflow.nodes {
        let Some(transition) = definition.transitions.get("complete") else {
            continue;
        };
        if transition.targets.iter().any(|target| target == node)
            && !matches!(round.nodes[id].status, NodeStatus::Succeeded)
        {
            return false;
        }
    }
    true
}

/// True when `node` completes into `upstream`.
fn is_predecessor(workflow: &StateMachineDefinition, node: &str, upstream: &str) -> bool {
    workflow
        .nodes
        .get(upstream)
        .and_then(|definition| definition.transitions.get("complete"))
        .map(|transition| transition.targets.iter().any(|target| target == node))
        .unwrap_or(false)
}

fn participant_of(
    context: &EvaluationContext<'_>,
    node: &str,
) -> Result<AgentBinding, RunnerError> {
    context
        .spec
        .participants
        .iter()
        .find(|participant| participant.node.0 == node)
        .map(|participant| participant.target.clone())
        .ok_or_else(|| RunnerError::CorruptState {
            code: format!("no participant binding for node {node}"),
        })
}

fn pool_of(context: &EvaluationContext<'_>, node: &str) -> Result<PoolId, RunnerError> {
    context
        .spec
        .participants
        .iter()
        .find(|participant| participant.node.0 == node)
        .map(|participant| participant.pool.clone())
        .ok_or_else(|| RunnerError::CorruptState {
            code: format!("no participant binding for node {node}"),
        })
}

fn capabilities_of(
    context: &EvaluationContext<'_>,
    node: &str,
) -> Result<DownstreamCapabilities, RunnerError> {
    context
        .spec
        .participants
        .iter()
        .find(|participant| participant.node.0 == node)
        .map(|participant| participant.capabilities.clone())
        .ok_or_else(|| RunnerError::CorruptState {
            code: format!("no participant binding for node {node}"),
        })
}
