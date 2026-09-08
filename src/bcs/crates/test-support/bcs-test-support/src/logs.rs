use std::{future::Future, io::Write, sync::{Arc, Mutex}};

use tracing::instrument::WithSubscriber;

#[derive(Clone, Default)]
struct LogBuffer(Arc<Mutex<Vec<u8>>>);

impl Write for LogBuffer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}

/// Captures real JSON events emitted by a future under a request-ID scope.
/// The subscriber is scoped to each poll, so concurrent tests do not share logs.
/// Spawned work must inherit its subscriber through the production boundary and
/// finish before the supplied future returns.
pub async fn capture_request_logs<T>(request_id: &str, future: impl Future<Output = T>) -> (T, Vec<serde_json::Value>) {
    let buffer = LogBuffer::default();
    let writer = buffer.clone();
    let subscriber = tracing_subscriber::fmt().json().with_max_level(tracing::Level::TRACE)
        .with_writer(move || writer.clone()).finish();
    let result = bcs_observability::with_request_id(request_id.to_owned(), future)
        .with_subscriber(subscriber).await;
    let bytes = buffer.0.lock().unwrap().clone();
    let text = String::from_utf8(bytes).expect("UTF-8 log output");
    let events = text.lines().map(|line| serde_json::from_str(line).expect("JSON log event")).collect();
    (result, events)
}
