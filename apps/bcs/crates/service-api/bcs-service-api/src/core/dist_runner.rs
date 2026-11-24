//! Pure reduction contract; resolved workflow uses the existing definition model.
use bcs_dist_model::*;
use bcs_domain::collaboration::{StateMachineDefinition, StateMachineNodeKind};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub struct EvaluationContext<'a> {
    pub spec: &'a SessionSpec,
    pub workflow: &'a StateMachineDefinition,
    pub now: TimestampMillis,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum RunnerInput {
    /// Strict group source: identity, deadline and pinned history are explicit.
    /// Existing Start retains its legacy meaning; consumers must opt in.
    GroupStart {
        input: RoundInput,
    },
    Start {
        tid: Tid,
        payload: Payload,
    },
    HumanReply {
        tid: Tid,
        interaction: InteractionId,
        payload: Payload,
    },
    Cancel {
        tid: Tid,
    },
    Agent {
        tid: Tid,
        event: AgentEvent,
    },
    Timeout(Deadline),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ActionIntent {
    Show {
        tid: Tid,
        node: NodeId,
        artifact_id: ArtifactId,
        class: OutputClass,
        update: ArtifactUpdate,
    },
    SetStatus {
        tid: Tid,
        /// Nonterminal only. Terminal states require FinishTask + durable archive.
        status: TaskStatus,
        interactions: Vec<Interaction>,
    },
    StartDispatch(DispatchSpec),
    CancelDispatch {
        did: Did,
        intent: CancelIntent,
    },
    ContinueDispatch {
        did: Did,
        input: PendingInput,
    },
    FinishTask {
        tid: Tid,
        status: TerminalStatus,
        result: Payload,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Decision {
    pub next_state: RunnerState,
    /// At most one SetStatus per Decision. Status interaction metadata is not
    /// externalized, so this bound keeps every admitted batch finalizable.
    pub actions: Vec<ActionIntent>,
    pub next_deadlines: Vec<Deadline>,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum RunnerError {
    #[error("corrupt runner state: {code}")]
    CorruptState { code: String },
    #[error("unsupported runner format: {format}")]
    UnsupportedFormat { format: u32 },
    #[error("invalid workflow: {code}")]
    InvalidWorkflow { code: String },
}

/// Deterministic, no IO, clock reads or random IDs. Thinking cannot drive policy.
pub trait DistRunnerCoreService: Send + Sync {
    fn init(&self, context: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError>;
    fn reduce(
        &self,
        context: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError>;
}

/// Executor-declared bounds for the strict FSM plane (D0 decision threshold:
/// BotTask-only acyclic DAGs with a single designated final output node,
/// serial or fan-out-join wiring and bounded per-node retry).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FsmCapabilities {
    /// Node kinds this runtime can dispatch. A workflow containing any node
    /// outside this set is rejected at validate() time, never accepted and
    /// suspended. Judge/Loop/HumanInput are excluded at D0.
    pub dispatchable_node_kinds: Vec<StateMachineNodeKind>,
    /// Highest per-node attempt count admitted. A node asking for more is
    /// clamped down to this ceiling; a missing bound means one attempt.
    pub max_attempts: u32,
    /// Maximum parallel transition targets admitted on one node (fan-out).
    pub max_fan_out: u32,
}

/// Frozen, normalized view of a publishable workflow. Reducers and the group
/// directory consume this instead of re-deriving graph rules per publication.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidatedWorkflow {
    /// The pinned revision this validation was derived from. An in-flight
    /// round keeps this revision even after a newer one is published.
    pub pinned: WorkflowRef,
    /// The single zero in-degree source node.
    pub entry: NodeId,
    /// The designated single final node: the only source of round product.
    pub final_node: NodeId,
    /// Clamped per-node attempt bounds (final node included).
    pub max_attempts: BTreeMap<String, u32>,
    /// Per-node dispatch timeouts in milliseconds (node override, then the
    /// definition defaults, then the session task timeout).
    pub node_timeout_millis: BTreeMap<String, u64>,
}

/// Shared publication gate for strict-FSM workflows: a pure function over a
/// frozen session spec, a workflow definition and the executor's declared
/// capabilities. Callable with no store, network, clock or randomness. A
/// rejection here happens before any Task exists. The strict group directory
/// reuses this same trait (do not re-derive node whitelists at the Gateway).
pub trait FsmValidatorCoreService: Send + Sync {
    fn validate(
        &self,
        frozen: &SessionSpec,
        workflow: &StateMachineDefinition,
        supported: &FsmCapabilities,
    ) -> Result<ValidatedWorkflow, RunnerError>;
}
