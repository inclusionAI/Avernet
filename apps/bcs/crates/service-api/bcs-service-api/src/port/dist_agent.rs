//! Semantic outbound boundary, implemented by adapting the existing A2A client.
//! This crate never imports that client's plugin or wire SDK.
use async_trait::async_trait;
use bcs_dist_model::*;
use futures::stream::BoxStream;

use super::repo::distributed::node_history::PinnedHistory;

/// The one trusted operation vocabulary of a downstream call (plan T6): a
/// fresh start, a resend under a recorded dedupe proof, a continuation of
/// the remote task, and the three observing operations. Adapters and
/// resolvers authorize per operation, never per connection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallOperation {
    Start,
    Resend,
    Continue,
    Query,
    Subscribe,
    Cancel,
}

/// Authority of one downstream operation, built by the Connector from its
/// held dispatch — never from client metadata, and never a bare
/// `AgentBinding` callback. Every send, resend, continuation, query,
/// subscription and cancel carries the FSM round, node and attempt that
/// own it, so the resolver re-checks the current authorization and every
/// reply is tied back to the node that earned it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallAuthority {
    /// The FSM round identity `<tid>.fsm:<generation>` — the prefix of the
    /// authoritative dispatch did
    /// `<tid>.fsm:<generation>:<node>:<attempt>` (the reducer's
    /// `attempt_did`). Empty only for non-FSM dispatches.
    pub round: String,
    /// The logical node inside the round that owns this call; distinct
    /// nodes of the same bot never share one.
    pub node: NodeId,
    /// The node's original attempt; an adapter retry never renumbers it.
    pub attempt: u32,
    /// The approved target binding this operation resolves through, with
    /// the current grants, by the resolver.
    pub binding: AgentBinding,
    pub operation: CallOperation,
    /// The remote task this operation acts on; `None` on a first start,
    /// which reuses no prior conversation.
    pub remote_ref: Option<RemoteTaskRef>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StableSendRequest {
    pub request_id: RequestId,
    pub context_id: Option<String>,
    pub payload: Payload,
    /// The sending node identity and its original attempt: adapters bind
    /// every send to one node so two nodes of one bot are never served as
    /// one conversation.
    pub node: NodeId,
    pub attempt: u32,
    /// The node's frozen history input (plan T6/T7): the exact pinned
    /// version the Worker froze for this dispatch, carried as its own
    /// field so no side ever concatenates a second copy of the history
    /// into the request body. `None` while the dispatch carries no
    /// pinned history (T7 loads it).
    pub frozen_history: Option<PinnedHistory>,
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
    /// Every operation resolves and authorizes through its `CallAuthority`.
    async fn start(&self, authority: &CallAuthority, request: StableSendRequest) -> SendOutcome;
    async fn send_input(
        &self,
        authority: &CallAuthority,
        remote: &RemoteTaskRef,
        request: StableSendRequest,
    ) -> SendOutcome;
    async fn query(&self, authority: &CallAuthority, query: RemoteQuery) -> QueryOutcome;
    async fn subscribe(
        &self,
        authority: &CallAuthority,
        remote: &RemoteTaskRef,
        after: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError>;
    /// RequestAccepted is not ConfirmedCanceled. Unknown remains supervised.
    async fn cancel(
        &self,
        authority: &CallAuthority,
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
