//! Inbound distributed use cases. HTTP/A2A DTOs stay in delivery adapters.
use super::principal::CallerContext;
use async_trait::async_trait;
use bcs_dist_model::*;
use futures::stream::BoxStream;

/// Built by trusted authentication/tenant resolution, never deserialized from body.
#[derive(Debug, Clone)]
pub struct GatewayCaller {
    pub tenant: TenantId,
    pub caller: CallerContext,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SubmitInput {
    pub group: GroupId,
    pub message_id: RequestId,
    pub session: Option<Sid>,
    pub task: Option<Tid>,
    pub interaction: Option<InteractionId>,
    pub content: Payload,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CancelTask {
    pub tid: Tid,
    pub request_id: RequestId,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubscribeMode {
    Snapshot,
    Resume(TaskCursor),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubscribeTask {
    pub tid: Tid,
    pub mode: SubscribeMode,
}

#[derive(Debug, Clone, PartialEq)]
pub enum TaskStreamItem {
    Snapshot {
        view: TaskView,
        cursor: Option<TaskCursor>,
    },
    ResumeAccepted {
        cursor: TaskCursor,
    },
    Events {
        events: Vec<OutputEvent>,
        cursor: TaskCursor,
    },
}

pub type TaskSubscription = BoxStream<'static, Result<TaskStreamItem, GatewayError>>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListTasks {
    pub session: Option<Sid>,
    pub statuses: Vec<TaskStatus>,
    pub after: Option<PageToken>,
    pub limit: std::num::NonZeroU32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TaskPage {
    pub tasks: Vec<TaskView>,
    pub next: Option<PageToken>,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum GatewayError {
    #[error("access denied")]
    AccessDenied,
    #[error("task not found")]
    NotFound,
    #[error("request identity/content conflict")]
    Conflict,
    #[error("session quarantined")]
    Quarantined,
    #[error("invalid input: {code}")]
    Invalid { code: String },
    #[error("dependency unavailable: {code}")]
    Unavailable { code: String },
    #[error("admission outcome unknown; retry the same request")]
    OutcomeUnknown { request_id: RequestId },
}

#[async_trait]
pub trait DistGatewayService: Send + Sync {
    /// Resolve authenticated scope, route, derive stable identity and admit.
    async fn submit(
        &self,
        caller: &GatewayCaller,
        request: SubmitInput,
    ) -> Result<AdmissionReceipt, GatewayError>;
    async fn get_task(&self, caller: &GatewayCaller, tid: &Tid) -> Result<TaskView, GatewayError>;
    /// Snapshot@H continues at H; resume@C never follows an unrelated newer snapshot.
    async fn subscribe(
        &self,
        caller: &GatewayCaller,
        request: SubscribeTask,
    ) -> Result<TaskSubscription, GatewayError>;
    /// Receipt acknowledges a durable cancellation request, not remote cancellation.
    async fn cancel(
        &self,
        caller: &GatewayCaller,
        request: CancelTask,
    ) -> Result<AdmissionReceipt, GatewayError>;
    async fn list_tasks(
        &self,
        caller: &GatewayCaller,
        request: ListTasks,
    ) -> Result<TaskPage, GatewayError>;
}
