use std::pin::Pin;
use async_trait::async_trait;
use futures::Stream;
use tokio::sync::watch;
use bcn_runtime_model::*;

pub type EventStream = Pin<Box<dyn Stream<Item = RuntimeResult<TaskEvent>> + Send + 'static>>;
/// true or sender closure requests shutdown. Cancellation is explicit, never global.
pub type CancellationReceiver = watch::Receiver<bool>;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimePhase { Starting, Running, Draining, Stopped }
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkerStatus { pub node: NodeIdentity, pub phase: RuntimePhase, pub active_sessions: u32, pub owned_shards: u32 }
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConnectorStatus { pub node: NodeIdentity, pub phase: RuntimePhase, pub active_dispatches: u32 }
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DrainReport { pub completed: u32, pub remaining: u32, pub deadline_reached: bool }

/// All operations authorize authenticated caller + target + session/task. IDs grant no access.
#[async_trait]
pub trait GatewayService: Send + Sync {
    async fn submit(&self, ctx: CallerContext, request: SubmitRequest) -> RuntimeResult<TaskView>;
    /// Reply-aware submission. The selected ID must come from durable acceptance evidence,
    /// including when the client omitted it. Unsupported implementations reject before submit.
    async fn submit_with_receipt(&self, ctx: CallerContext, request: SubmitRequest) -> RuntimeResult<SubmitOutcome> {
        if request.message.task_id.is_some() {return Err(RuntimeError::Unsupported("reply receipt unavailable".into()))}
        Ok(SubmitOutcome{task:self.submit(ctx,request).await?,reply_interaction_id:None})
    }
    async fn get(&self, ctx: CallerContext, task_id: TaskId) -> RuntimeResult<TaskView>;
    async fn list(&self, ctx: CallerContext, query: TaskQuery) -> RuntimeResult<TaskPage>;
    /// Starts with authoritative snapshot/cursor; terminal tasks are queried, not subscribed.
    async fn subscribe(&self, ctx: CallerContext, task_id: TaskId, cursor: Option<EventCursor>) -> RuntimeResult<EventStream>;
    /// Records intent; must not report cancellation success without downstream evidence.
    async fn cancel(&self, ctx: CallerContext, command: CancelCommand) -> RuntimeResult<TaskView>;
}
#[async_trait]
pub trait WorkerRuntime: Send + Sync {
    async fn run(&self, shutdown: CancellationReceiver) -> RuntimeResult<()>;
    /// Absolute server-clock deadline in milliseconds. Stops new claims, preserves recovery data.
    async fn drain(&self, deadline_ms: u64) -> RuntimeResult<DrainReport>;
    async fn status(&self) -> RuntimeResult<WorkerStatus>;
}
#[async_trait]
pub trait ConnectorRuntime: Send + Sync {
    async fn run(&self, shutdown: CancellationReceiver) -> RuntimeResult<()>;
    async fn drain(&self, deadline_ms: u64) -> RuntimeResult<DrainReport>;
    async fn status(&self) -> RuntimeResult<ConnectorStatus>;
}
