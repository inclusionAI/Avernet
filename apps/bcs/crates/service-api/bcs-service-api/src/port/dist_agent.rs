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
    /// Current transport capabilities for a logical agent, used for safe retry and recovery.
    async fn capabilities(&self, target: &AgentId) -> Result<DownstreamCapabilities, AgentReadError>;

    /// No blind retry. Once submission may have happened, errors are OutcomeUnknown.
    async fn start(&self, target: &AgentId, request: StableSendRequest) -> SendOutcome;
    async fn send_input(
        &self,
        target: &AgentId,
        remote: &RemoteTaskRef,
        request: StableSendRequest,
    ) -> SendOutcome;
    async fn query(&self, target: &AgentId, query: RemoteQuery) -> QueryOutcome;
    async fn subscribe(
        &self,
        target: &AgentId,
        remote: &RemoteTaskRef,
        after: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError>;
    /// RequestAccepted is not ConfirmedCanceled. Unknown remains supervised.
    async fn cancel(
        &self,
        target: &AgentId,
        remote: &RemoteTaskRef,
        request_id: RequestId,
    ) -> CancelOutcome;
}

#[derive(Debug, Clone, PartialEq)]
pub struct FrozenSessionProposal {
    pub target: SessionTarget,
    pub mode_envelope: ModeConfigEnvelope,
    pub authorized_agents: Vec<AuthorizedAgent>,
    pub limits: SessionLimits,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DirectoryError {
    #[error("session target not found or not accessible")]
    NotFound,
    #[error("access denied")]
    AccessDenied,
    #[error("session directory unavailable")]
    Unavailable,
    #[error("frozen mode configuration revision or digest mismatch")]
    InvalidRevision,
}

#[async_trait]
pub trait DistSessionAuthorizerPort: Send + Sync {
    /// Host-supplied authorization. The caller identity and target are passed
    /// unchanged; this interface defines no Bot, friendship or organization rules.
    async fn authorize(
        &self,
        scope: &TaskScope,
        target: &SessionTarget,
    ) -> Result<(), DirectoryError>;
}

#[async_trait]
pub trait DistSessionDirectoryPort: Send + Sync {
    /// Gateway checks every external send before durable admission, including replies.
    /// The host supplies the policy; the Gateway passes its existing authenticated
    /// scope unchanged. No authorization policy or default allow is provided here.
    async fn authorize(
        &self,
        scope: &TaskScope,
        target: &SessionTarget,
    ) -> Result<(), DirectoryError>;

    /// Freeze target configuration once, after caller authorization.
    async fn resolve(
        &self,
        scope: &TaskScope,
        target: &SessionTarget,
    ) -> Result<FrozenSessionProposal, DirectoryError>;

    /// A referenced configuration needs a directory that can load the exact revision.
    async fn load_mode_config(
        &self,
        _scope: &TaskScope,
        envelope: &ModeConfigEnvelope,
    ) -> Result<Payload, DirectoryError> {
        match &envelope.payload {
            Payload::Inline(_) => Ok(envelope.payload.clone()),
            _ => Err(DirectoryError::InvalidRevision),
        }
    }
}
