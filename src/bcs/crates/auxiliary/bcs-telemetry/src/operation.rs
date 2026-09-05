//! Transport-neutral operation observations. Never records arguments or errors.
use std::future::Future;
use std::time::{Duration, Instant};
use opentelemetry::trace::TraceContextExt;
use tracing::{Instrument, Span};
use tracing_opentelemetry::OpenTelemetrySpanExt;

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
}

pub fn current_operation_id() -> String {
    CURRENT_OPERATION.try_with(ToString::to_string).unwrap_or_default()
}

pub fn current_request_id() -> String {
    REQUEST_CONTEXT.try_with(|context| context.id.clone()).unwrap_or_default()
}

pub fn current_trace_id() -> String {
    let context = Span::current().context();
    let context_span = context.span();
    if context_span.span_context().is_valid() {
        context_span.span_context().trace_id().to_string()
    } else { String::new() }
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
    metrics::counter!("bcs_observation_total", "operation" => name, "outcome" => outcome).increment(1);
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

pub fn in_current_context<T>(future: impl Future<Output = T>) -> impl Future<Output = T> {
    use tracing::instrument::WithSubscriber;
    let future = Box::pin(future);
    let context = REQUEST_CONTEXT.try_with(Clone::clone).ok();
    let operation_id = CURRENT_OPERATION.try_with(|id| *id).ok();
    let future = async move {
        match operation_id {
            Some(id) => CURRENT_OPERATION.scope(id, future).await,
            None => future.await,
        }
    };
    let future = future.instrument(Span::current()).with_current_subscriber();
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
    span: Span,
    finished: bool,
}

impl Operation {
    pub fn new(name: &'static str) -> Self {
        let id = uuid::Uuid::new_v4();
        let parent_operation_id = current_operation_id();
        let request_id = current_request_id();
        let context_data = REQUEST_CONTEXT.try_with(Clone::clone).ok();
        let span = tracing::info_span!(target: "bcn_otel", "bcs.operation",
            operation = name, operation_id = %id, parent_operation_id = %parent_operation_id, request_id = %request_id,
            trace_id = tracing::field::Empty,
            outcome = tracing::field::Empty, duration_ms = tracing::field::Empty);
        let context = span.context();
        let context_span = context.span();
        let trace_id = if context_span.span_context().is_valid() { context_span.span_context().trace_id().to_string() } else { String::new() };
        if context_span.span_context().is_valid() {
            span.record("trace_id", context_span.span_context().trace_id().to_string());
        }
        metrics::gauge!("bcs_operation_inflight", "operation" => name).increment(1.0);
        Self { name, id, parent_operation_id, request_id, context: context_data, trace_id, started: Instant::now(), span, finished: false }
    }

    pub fn span(&self) -> Span { self.span.clone() }

    pub fn elapsed_ms(&self) -> f64 { self.started.elapsed().as_secs_f64() * 1000.0 }

    pub fn finish(mut self, outcome: &'static str) {
        self.record(outcome);
    }

    fn record(&mut self, outcome: &'static str) {
        if self.finished { return; }
        self.finished = true;
        let duration_ms = self.elapsed_ms();
        if let Some(context) = &self.context { accumulate(context, self.name, outcome, duration_ms); }
        self.span.record("outcome", outcome);
        if matches!(outcome, "error" | "panicked") {
            self.span.set_status(opentelemetry::trace::Status::error(outcome));
        }
        self.span.record("duration_ms", duration_ms);
        metrics::gauge!("bcs_operation_inflight", "operation" => self.name).decrement(1.0);
        metrics::counter!("bcs_operation_total", "operation" => self.name, "outcome" => outcome).increment(1);
        metrics::histogram!("bcs_operation_duration_ms", "operation" => self.name).record(duration_ms);
        self.span.in_scope(|| {
            if matches!(outcome, "error" | "cancelled" | "panicked") || duration_ms >= 100.0 {
                tracing::warn!(target: "bcs_observation", operation = self.name, operation_id = %self.id, parent_operation_id = %self.parent_operation_id, request_id = %self.request_id, trace_id = %self.trace_id, outcome,
                    duration_ms, "bcs.operation.finished");
            } else {
                tracing::debug!(target: "bcs_observation", operation = self.name, operation_id = %self.id, parent_operation_id = %self.parent_operation_id, request_id = %self.request_id, trace_id = %self.trace_id, outcome,
                    duration_ms, "bcs.operation.finished");
            }
        });
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
    let future = future.instrument(operation.span());
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
            operation.span.in_scope(|| tracing::warn!(target: "bcs_observation",
                operation = operation.name, operation_id = %operation.id, parent_operation_id = %operation.parent_operation_id, request_id = %operation.request_id, trace_id = %operation.trace_id, duration_ms = operation.elapsed_ms(),
                "bcs.operation.stalled"));
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
