//! Bounded log identity, independent of network propagation and exporter lifetime.
use std::{future::Future, sync::Arc};

/// An atomic pair of provider-owned ID fields, never a propagation carrier.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LogCorrelation(Arc<Fields>);

#[derive(Debug, PartialEq, Eq)]
struct Fields {
    trace_name: &'static str,
    trace_id: String,
    span_name: &'static str,
    span_id: String,
}

impl LogCorrelation {
    pub fn new(trace_name: &'static str, trace_id: &str, span_name: &'static str, span_id: &str) -> Option<Self> {
        if trace_name == span_name || !valid_name(trace_name) || !valid_name(span_name)
            || !valid_id(trace_id) || !valid_id(span_id) {
            return None;
        }
        Some(Self(Arc::new(Fields {
            trace_name, trace_id: trace_id.to_owned(), span_name, span_id: span_id.to_owned(),
        })))
    }

    pub fn fields(&self) -> [(&'static str, &str); 2] {
        [(self.0.trace_name, &self.0.trace_id), (self.0.span_name, &self.0.span_id)]
    }
}

fn valid_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 64 && name.as_bytes()[0].is_ascii_alphabetic()
        && name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
        && !matches!(name, "timestamp" | "level" | "target" | "message" | "fields" | "span" | "spans"
            | "filename" | "line_number" | "threadName" | "threadId" | "log_class" | "event_code"
            | "request_id" | "trace_id" | "span_id" | "otel_trace_id" | "otel_span_id")
}

fn valid_id(value: &str) -> bool {
    !value.is_empty() && value.len() <= 256
        && value.bytes().all(|c| c.is_ascii_alphanumeric() || b"._-:".contains(&c))
}

tokio::task_local! {
    static SNAPSHOT: Option<LogCorrelation>;
    static OVERRIDE: Option<LogCorrelation>;
}

pub fn current_log_correlation() -> Option<LogCorrelation> {
    // Explicit suppression wins even over a previously captured snapshot.
    if crate::SCOPED_PROVIDER.try_with(Option::is_none).unwrap_or(false) { return None; }
    // A cancellation guard belongs to its captured operation, even when dropped
    // by another task that happens to have a different network context.
    if let Ok(value) = OVERRIDE.try_with(Clone::clone) { return value; }
    if let Some(context) = crate::current_context() {
        return crate::provider().and_then(|provider| provider.log_correlation(&context));
    }
    SNAPSHOT.try_with(Clone::clone).ok().flatten()
}

/// Capture at the caller, before spawn. This restores logs only, never a network
/// parent/provider or an SDK span. None shadows any enclosing log snapshot.
pub fn in_current_log_context<F: Future>(future: F) -> impl Future<Output = F::Output> {
    let snapshot = current_log_correlation();
    async move { SNAPSHOT.scope(snapshot, future).await }
}

/// Emit a guard's final log with its captured identity; restores the previous
/// value on normal return or panic. No guard may be held across await.
pub fn with_log_correlation<T>(snapshot: Option<LogCorrelation>, work: impl FnOnce() -> T) -> T {
    OVERRIDE.sync_scope(snapshot, work)
}

#[cfg(test)]
mod tests;
