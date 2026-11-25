//! Semantic outbound boundary, implemented by adapting the existing A2A client.
//! This crate never imports that client's plugin or wire SDK.
use async_trait::async_trait;
use bcs_dist_model::*;
use futures::stream::BoxStream;

#[derive(Debug, Clone, PartialEq)]
pub struct StableSendRequest {
    pub request_id: RequestId,
    pub context_id: Option<String>,
    pub payload: Payload,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RemoteEvent {
    pub remote: RemoteTaskRef,
    pub event_id: Option<String>,
    pub cursor: Option<ReplayToken>,
    pub kind: AgentEventKind,
    pub payload: Payload,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("remote read failed: {code}")]
pub struct AgentReadError {
    pub code: String,
}

pub type RemoteEventStream = BoxStream<'static, Result<RemoteEvent, AgentReadError>>;

pub enum SendOutcome {
    Accepted {
        remote: RemoteTaskRef,
        events: Option<RemoteEventStream>,
    },
    Completed {
        result: Payload,
    },
    Rejected {
        code: String,
    },
    NotSent {
        code: String,
    },
    OutcomeUnknown {
        code: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RemoteQuery {
    ByRequest(RequestId),
    ByTask(RemoteTaskRef),
}

#[derive(Debug, Clone, PartialEq)]
pub enum QueryOutcome {
    Found {
        remote: RemoteTaskRef,
        status: TaskStatus,
        snapshot: Payload,
    },
    NotFound,
    Unsupported,
    Unavailable {
        code: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CancelOutcome {
    RequestAccepted,
    ConfirmedCanceled,
    AlreadyFinished(FinalDispatchOutcome),
    Rejected { code: String },
    OutcomeUnknown { code: String },
}

#[async_trait]
pub trait DistAgentClientPort: Send + Sync {
    /// No blind retry. Once submission may have happened, errors are OutcomeUnknown.
    async fn start(&self, target: &AgentBinding, request: StableSendRequest) -> SendOutcome;
    async fn send_input(
        &self,
        target: &AgentBinding,
        remote: &RemoteTaskRef,
        request: StableSendRequest,
    ) -> SendOutcome;
    async fn query(&self, target: &AgentBinding, query: RemoteQuery) -> QueryOutcome;
    async fn subscribe(
        &self,
        target: &AgentBinding,
        remote: &RemoteTaskRef,
        after: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError>;
    /// RequestAccepted is not ConfirmedCanceled. Unknown remains supervised.
    async fn cancel(
        &self,
        target: &AgentBinding,
        remote: &RemoteTaskRef,
        request_id: RequestId,
    ) -> CancelOutcome;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrozenGroupDefinition {
    pub group: GroupId,
    pub workflow: WorkflowRef,
    pub participants: Vec<ParticipantBinding>,
    pub limits: SessionLimits,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum GroupDirectoryError {
    #[error("group not found or not accessible")]
    NotFound,
    #[error("group directory unavailable")]
    Unavailable,
    #[error("frozen workflow revision or digest mismatch")]
    InvalidRevision,
}

#[async_trait]
pub trait DistGroupDirectoryPort: Send + Sync {
    /// Resolve/freeze only for a new session, scoped by the trusted caller identity.
    /// Success makes the exact workflow revision retrievable for session recovery.
    async fn resolve(
        &self,
        scope: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError>;
    /// Load the exact immutable revision and verify its digest, never current group config.
    async fn load_workflow(
        &self,
        scope: &TaskScope,
        reference: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError>;
}
