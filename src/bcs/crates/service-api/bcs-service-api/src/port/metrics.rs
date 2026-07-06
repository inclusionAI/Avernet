//! Metrics extension ports.
//!
//! These contracts keep observability integration explicit without adding
//! Prometheus-specific calls to business services or delivery adapters.

use async_trait::async_trait;
use bcs_domain::{ActorKind, ActorStatus, GroupKind, GroupStatus, GroupStrategy, SessionKind, SessionStatus};

use crate::ServiceResult;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum MetricsResult {
    Success,
    Error,
}

/// WebSocket peer category for the WS lifecycle hook.
///
/// This is transport vocabulary owned by the WS metrics contract. If BCS adds
/// non-WS streaming adapters such as gRPC, revisit whether these variants still
/// belong in this shared port module.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum WsPeer {
    Bot,
    Frontend,
}

/// WebSocket close reason for low-cardinality lifecycle metrics.
///
/// This is transport vocabulary owned by the WS metrics contract. If BCS adds
/// non-WS streaming adapters, revisit this enum's ownership.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum WsCloseReason {
    ClientClose,
    ServerClose,
    IdleTimeout,
    SendError,
    ProtocolError,
    Unknown,
}

/// WebSocket error event category for low-cardinality lifecycle metrics.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum WsErrorKind {
    RegisterRejected,
    DispatchError,
    ProtocolError,
    SendError,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DirectChatRunState {
    Pending,
    Submitted,
    Running,
    Completed,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DirectChatClientKind {
    None,
    HttpChat,
    HttpChatAsync,
    BcsCli,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DirectChatRunEvent {
    Created,
    Submitted,
    Running,
    Completed,
    Failed,
    Cancelled,
    Expired,
    Dropped,
    CapacityRejected,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DirectChatRunReason {
    None,
    Timeout,
    BotNotConnected,
    Blocked,
    StoreCapacity,
    InternalError,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DeliveryMetricTarget {
    Unknown,
    Bot,
    Frontend,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DeliveryMetricKind {
    Send,
    Inject,
    Abort,
    TaskDispatch,
    TaskMessage,
    TaskResult,
    WorkbenchEvent,
    RunEvent,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DeliveryBlockSurface {
    GroupMessage,
    Task,
    DirectChat,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DeliveryBlockReason {
    PolicyBlocked,
    Unknown,
}

#[derive(Debug, Clone)]
pub struct DeliveryBlockContext {
    pub target: DeliveryMetricTarget,
    pub delivery_kind: DeliveryMetricKind,
    pub surface: DeliveryBlockSurface,
    pub reason: DeliveryBlockReason,
}

impl Default for DeliveryBlockContext {
    fn default() -> Self {
        Self {
            target: DeliveryMetricTarget::Unknown,
            delivery_kind: DeliveryMetricKind::Unknown,
            surface: DeliveryBlockSurface::Unknown,
            reason: DeliveryBlockReason::Unknown,
        }
    }
}

#[derive(Debug, Clone)]
pub struct GroupMetricCount {
    pub status: GroupStatus,
    pub kind: GroupKind,
    pub group_strategy: GroupStrategy,
    pub service_mode: Option<String>,
    pub count: u64,
}

#[derive(Debug, Clone)]
pub struct GroupSessionMetricCount {
    pub status: SessionStatus,
    pub session_kind: SessionKind,
    pub count: u64,
}

#[derive(Debug, Clone)]
pub struct BotMetricCount {
    pub actor_kind: ActorKind,
    pub status: ActorStatus,
    pub visibility: Option<String>,
    pub count: u64,
}

#[derive(Debug, Clone)]
pub struct ChatRunMetricCount {
    pub state: DirectChatRunState,
    pub client_kind: DirectChatClientKind,
    pub count: u64,
}

#[async_trait]
pub trait WsLifecycleInstrumentationHook: Send + Sync {
    async fn accepted(&self, peer: WsPeer, endpoint: &'static str);
    async fn registered(&self, peer: WsPeer, endpoint: &'static str);
    async fn error(&self, peer: WsPeer, endpoint: &'static str, kind: WsErrorKind);
    async fn closed(
        &self,
        peer: WsPeer,
        endpoint: &'static str,
        close_reason: WsCloseReason,
        duration: std::time::Duration,
    );
}

#[async_trait]
pub trait GroupMetricsSnapshotPort: Send + Sync {
    async fn group_counts(&self) -> ServiceResult<Vec<GroupMetricCount>>;
}

#[async_trait]
pub trait GroupSessionMetricsSnapshotPort: Send + Sync {
    async fn group_session_counts(&self) -> ServiceResult<Vec<GroupSessionMetricCount>>;
}

#[async_trait]
pub trait BotMetricsSnapshotPort: Send + Sync {
    async fn bot_counts(&self) -> ServiceResult<Vec<BotMetricCount>>;
}

#[async_trait]
pub trait DirectChatRunSnapshotPort: Send + Sync {
    async fn direct_chat_run_counts(&self) -> ServiceResult<Vec<ChatRunMetricCount>>;
}

#[async_trait]
pub trait DirectChatRunLifecycleHook: Send + Sync {
    async fn event(
        &self,
        event: DirectChatRunEvent,
        result: MetricsResult,
        client_kind: DirectChatClientKind,
        reason: DirectChatRunReason,
    );
}

#[async_trait]
pub trait DeliveryPolicyBlockInstrumentationHook: Send + Sync {
    async fn blocked(&self, context: DeliveryBlockContext);
}
