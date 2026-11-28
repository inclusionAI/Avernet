//! Distributed execution contract and typed, versioned mode plugin boundary.
use bcs_dist_model::*;
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use std::{any::Any, sync::Arc};

pub struct EvaluationContext<'a> {
    pub spec: &'a SessionSpec,
    pub config: &'a (dyn Any + Send + Sync),
    pub tid: &'a Tid,
    pub downstream_generation: u64,
    pub now: TimestampMillis,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum RunnerInput {
    Start {
        tid: Tid,
        payload: Payload,
    },
    HumanReply {
        tid: Tid,
        interaction: InteractionId,
        responder: PrincipalId,
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
        source: CorrelationKey,
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
#[async_trait]
pub trait DistRunnerCoreService: Send + Sync {
    fn shares_session_downstream_context(&self, _spec: &SessionSpec) -> bool { false }

    /// Bind the immutable session configuration once for the lifetime of a held
    /// slot. Implementations must explicitly validate their supported mode/config.
    fn validate_session(
        &self, _spec: &SessionSpec, _raw: &serde_json::Value,
    ) -> Result<Arc<dyn Any + Send + Sync>, RunnerError>;
    async fn init(&self, context: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError>;
    async fn reduce(
        &self,
        context: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError>;
}

/// Mode-local state and the effects the Worker commits with it.
#[derive(Debug, Clone, PartialEq)]
pub struct TypedDecision<S> {
    pub next_state: S,
    pub actions: Vec<ActionIntent>,
    pub next_deadlines: Vec<Deadline>,
}

/// Worker plugin for one version of a collaboration mode.
#[async_trait::async_trait]
pub trait CollaborationModeRunnerService: Send + Sync {
    type Config: Send + Sync + std::fmt::Debug + 'static;
    type State: Serialize + for<'de> Deserialize<'de> + Send + Sync + Clone;

    const STATE_FORMAT_VERSION: u32 = 1;
    fn mode(&self) -> CollaborationMode;
    fn mode_version(&self) -> u32 { 1 }
    fn shares_session_downstream_context(&self) -> bool { false }

    fn parse_and_validate_config(
        &self,
        raw: &serde_json::Value,
        authorized_agents: &[AuthorizedAgent],
        limits: &SessionLimits,
    ) -> Result<Self::Config, RunnerError>;

    async fn init_task(
        &self,
        spec: &SessionSpec,
        config: &Self::Config,
        tid: &Tid,
        downstream_generation: u64,
        now: TimestampMillis,
    ) -> Result<Self::State, RunnerError>;

    async fn reduce_task(
        &self,
        spec: &SessionSpec,
        config: &Self::Config,
        tid: &Tid,
        downstream_generation: u64,
        state: &Self::State,
        inputs: &[RunnerInput],
        now: TimestampMillis,
    ) -> Result<TypedDecision<Self::State>, RunnerError>;
}

/// Name used by the distributed runner SPI specification.
pub use CollaborationModeRunnerService as CollaborationModeRunner;
