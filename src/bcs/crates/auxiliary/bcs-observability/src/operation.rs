//! Log-only operation observations. Never creates spans or records metrics/payloads.
use std::future::Future;
use std::time::{Duration, Instant};

#[derive(Default, serde::Serialize)]
struct Totals {
    count: u64,
    total_ms: f64,
    max_ms: f64,
    outcomes: std::collections::BTreeMap<&'static str, u64>,
}
#[derive(Clone)]
struct RequestContext {
    id: String,
    totals: std::sync::Arc<std::sync::Mutex<std::collections::BTreeMap<&'static str, Totals>>>,
}
tokio::task_local! {
    static REQUEST_CONTEXT: RequestContext;
    static CURRENT_OPERATION: uuid::Uuid;
    static TRACE_ID: String;
}

pub fn current_operation_id() -> String {
    CURRENT_OPERATION.try_with(ToString::to_string).unwrap_or_default()
}

pub fn current_request_id() -> String {
    REQUEST_CONTEXT.try_with(|context| context.id.clone()).unwrap_or_default()
}

/// Returns only the correlation ID supplied by an adapter, or an empty string.
/// No tracing SDK or active span is consulted.
pub fn current_trace_id() -> String {
    TRACE_ID.try_with(Clone::clone).unwrap_or_default()
}

/// Scopes an adapter-supplied trace ID as log data, without creating or entering a span.
/// The ID is opaque: validation/extraction belongs to the adapter. Empty means absent.
/// Nested scopes restore the outer ID; completion or cancellation removes the scope.
/// Spawned work inherits it only through an explicit `in_current_context` wrapper.
pub async fn with_trace_id<T>(trace_id: String, future: impl Future<Output = T>) -> T {
    TRACE_ID.scope(trace_id, future).await
}

fn accumulate(context: &RequestContext, name: &'static str, outcome: &'static str, duration_ms: f64) {
    let mut totals = context.totals.lock().unwrap_or_else(|error| error.into_inner());
    let total = totals.entry(name).or_default();
    total.count += 1;
    total.total_ms += duration_ms;
    total.max_ms = total.max_ms.max(duration_ms);
    *total.outcomes.entry(outcome).or_default() += 1;
}

pub fn count(name: &'static str, outcome: &'static str) {
    let _ = REQUEST_CONTEXT.try_with(|context| accumulate(context, name, outcome, 0.0));
}

pub async fn with_request_context<T>(request_id: String, future: impl Future<Output = T>) -> T {
    let context = RequestContext { id: request_id, totals: Default::default() };
    let result = REQUEST_CONTEXT.scope(context.clone(), future).await;
    let totals = context.totals.lock().unwrap_or_else(|error| error.into_inner());
    let observations = serde_json::to_string(&*totals).expect("finite operation durations");
    drop(totals);
    tracing::info!(target: "bcs_observation", request_id = %context.id,
        trace_id = %current_trace_id(), observations = %observations, "http.request.operations");
    result
}

/// Carry log correlation into detached work without retaining or entering spans.
pub fn in_current_context<T>(future: impl Future<Output = T>) -> impl Future<Output = T> {
    use tracing::instrument::WithSubscriber;
    let future = Box::pin(future);
    let context = REQUEST_CONTEXT.try_with(Clone::clone).ok();
    let operation_id = CURRENT_OPERATION.try_with(|id| *id).ok();
    let trace_id = current_trace_id();
    let future = async move {
        match operation_id {
            Some(id) => CURRENT_OPERATION.scope(id, future).await,
            None => future.await,
        }
    };
    let future = TRACE_ID.scope(trace_id, future).with_current_subscriber();
    async move {
        match context {
            Some(context) => REQUEST_CONTEXT.scope(context, future).await,
            None => future.await,
        }
    }
}

/// An observation ends exactly once, including when its future is cancelled.
/// It does not set a deadline or change the result of the observed operation.
pub struct Operation {
    name: &'static str,
    id: uuid::Uuid,
    parent_operation_id: String,
    request_id: String,
    context: Option<RequestContext>,
    trace_id: String,
    started: Instant,
    finished: bool,
}

impl Operation {
    pub fn new(name: &'static str) -> Self {
        let id = uuid::Uuid::new_v4();
        let parent_operation_id = current_operation_id();
        let request_id = current_request_id();
        let context_data = REQUEST_CONTEXT.try_with(Clone::clone).ok();
        let trace_id = current_trace_id();
        Self { name, id, parent_operation_id, request_id, context: context_data, trace_id, started: Instant::now(), finished: false }
    }

    pub fn elapsed_ms(&self) -> f64 { self.started.elapsed().as_secs_f64() * 1000.0 }

    pub fn finish(mut self, outcome: &'static str) {
        self.record(outcome);
    }

    fn record(&mut self, outcome: &'static str) {
        if self.finished { return; }
        self.finished = true;
        let duration_ms = self.elapsed_ms();
        if let Some(context) = &self.context { accumulate(context, self.name, outcome, duration_ms); }
        if matches!(outcome, "error" | "cancelled" | "panicked") || duration_ms >= 100.0 {
            tracing::warn!(target: "bcs_observation", operation = self.name, operation_id = %self.id, parent_operation_id = %self.parent_operation_id, request_id = %self.request_id, trace_id = %self.trace_id, outcome,
                duration_ms, "bcs.operation.finished");
        } else {
            tracing::debug!(target: "bcs_observation", operation = self.name, operation_id = %self.id, parent_operation_id = %self.parent_operation_id, request_id = %self.request_id, trace_id = %self.trace_id, outcome,
                duration_ms, "bcs.operation.finished");
        }
    }
}

impl Drop for Operation {
    fn drop(&mut self) {
        if !self.finished {
            self.record(if std::thread::panicking() { "panicked" } else { "cancelled" });
        }
    }
}

async fn wait<T>(operation: &Operation, future: impl Future<Output = T>) -> T {
    CURRENT_OPERATION.scope(operation.id, wait_inner(operation, future)).await
}

async fn wait_inner<T>(operation: &Operation, future: impl Future<Output = T>) -> T {
    tokio::pin!(future);
    // One warning per operation, with no per-operation spawned watchdog task.
    // This timer is diagnostic only: the original future still owns its deadline.
    let warning = tokio::time::sleep(Duration::from_secs(5));
    tokio::pin!(warning);
    let mut warned = false;
    std::future::poll_fn(|cx| {
        if let std::task::Poll::Ready(value) = future.as_mut().poll(cx) {
            return std::task::Poll::Ready(value);
        }
        if !warned && warning.as_mut().poll(cx).is_ready() {
            warned = true;
            tracing::warn!(target: "bcs_observation",
                operation = operation.name, operation_id = %operation.id, parent_operation_id = %operation.parent_operation_id, request_id = %operation.request_id, trace_id = %operation.trace_id, duration_ms = operation.elapsed_ms(),
                "bcs.operation.stalled");
        }
        std::task::Poll::Pending
    }).await
}

/// Box the caller future before constructing our state machine: dependency chains
/// can otherwise multiply already large adapter futures on the executor stack.
pub fn observe_result<T, E>(name: &'static str, future: impl Future<Output = Result<T, E>>) -> impl Future<Output = Result<T, E>> {
    let future = Box::pin(future);
    async move {
        let operation = Operation::new(name);
        let result = wait(&operation, future).await;
        operation.finish(if result.is_ok() { "success" } else { "error" });
        result
    }
}

/// Records completion, not business success; callers retain their result semantics.
pub fn observe_value<T>(name: &'static str, future: impl Future<Output = T>) -> impl Future<Output = T> {
    let future = Box::pin(future);
    async move {
        let operation = Operation::new(name);
        let result = wait(&operation, future).await;
        operation.finish("completed");
        result
    }
}
