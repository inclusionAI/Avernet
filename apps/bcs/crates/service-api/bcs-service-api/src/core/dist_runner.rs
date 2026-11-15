//! Pure reduction contract; resolved workflow uses the existing definition model.
use bcs_dist_model::*;
use bcs_domain::collaboration::StateMachineDefinition;

pub struct EvaluationContext<'a> {
    pub spec: &'a SessionSpec,
    pub workflow: &'a StateMachineDefinition,
    pub now: TimestampMillis,
}

#[derive(Debug, Clone, PartialEq)]
pub enum RunnerInput {
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

#[derive(Debug, Clone, PartialEq)]
pub enum ActionIntent {
    Show {
        tid: Tid,
        node: NodeId,
        class: OutputClass,
        content: Payload,
    },
    SetStatus {
        tid: Tid,
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

#[derive(Debug, Clone, PartialEq)]
pub struct Decision {
    pub next_state: RunnerState,
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
