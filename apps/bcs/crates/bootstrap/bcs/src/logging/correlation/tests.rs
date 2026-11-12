use super::*;
use std::sync::{Arc, Mutex};
use tracing_subscriber::prelude::*;

#[derive(Clone)]
struct FixedTime;
impl FormatTime for FixedTime {
    fn format_time(&self, writer: &mut Writer<'_>) -> fmt::Result { writer.write_str("2026-11-12 12:00:00.123") }
}
#[derive(Clone, Default)]
struct Buffer(Arc<Mutex<Vec<u8>>>);
impl std::io::Write for Buffer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> { self.0.lock().unwrap().write(bytes) }
    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}
impl Buffer {
    fn text(&self) -> String { String::from_utf8(self.0.lock().unwrap().clone()).unwrap() }
    fn rows(&self) -> Vec<serde_json::Value> { self.text().lines().map(|l| serde_json::from_str(l).unwrap()).collect() }
}
fn ids() -> Option<LogCorrelation> { LogCorrelation::new("network_trace", "trace-42", "network_span", "0.7") }

#[test]
fn text_preserves_base_format_and_appends_only_selected_complete_pair() {
    let original = Buffer::default();
    let enriched = Buffer::default();
    let a = original.clone(); let b = enriched.clone();
    let subscriber = tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer().with_ansi(false).with_timer(FixedTime).with_writer(move || a.clone()))
        .with(tracing_subscriber::fmt::layer().with_ansi(false).event_format(text(FixedTime)).with_writer(move || b.clone()));
    tracing::subscriber::with_default(subscriber, || {
        bcs_tracing_api::with_log_correlation(ids(), || {
            tracing::info!(target: "business", count = 3, "ordinary");
            tracing::info!(target: "business", log_class = "business_key", count = 3, "key");
        });
        tracing::info!(target: "business", log_class = "business_key", "no context");
    });
    let original = original.text(); let enriched = enriched.text();
    let a: Vec<_> = original.lines().collect(); let b: Vec<_> = enriched.lines().collect();
    assert_eq!(a[0], b[0]);
    assert_eq!(format!("{} network_trace=trace-42 network_span=0.7", a[1]), b[1]);
    assert_eq!(a[2], b[2]);
}

#[test]
fn json_preserves_types_and_escaping_and_skips_noise_otel_and_collisions() {
    let original = Buffer::default(); let enriched = Buffer::default(); let duplicate = Buffer::default();
    let a = original.clone(); let b = enriched.clone(); let c = duplicate.clone();
    let subscriber = tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer().json().flatten_event(true).with_current_span(false).with_span_list(false)
            .with_timer(FixedTime).with_writer(move || a.clone()))
        .with(tracing_subscriber::fmt::layer().json().event_format(json(FixedTime)).with_writer(move || b.clone()))
        .with(tracing_subscriber::fmt::layer().json().event_format(json(FixedTime)).with_writer(move || c.clone()));
    tracing::subscriber::with_default(subscriber, || bcs_tracing_api::with_log_correlation(ids(), || {
        for _ in 0..1000 {
            tracing::info!(log_class = Option::<&str>::None, "delta");
        }
        tracing::warn!(log_class = "business_key", count = 7u64, signed = -1i64, duration = 1.25, ok = false, payload = "quote\"\n中文", "terminal");
        tracing::info!(log_class = "business_key", network_trace = "existing", "collision");
        tracing::info!(log_class = "business_key", network_span = Option::<&str>::None, "declared collision");
        tracing::info!(target: "bcn_otel", log_class = "business_key", "otel");
    }));
    assert_eq!(enriched.text(), duplicate.text());
    let original_lines = original.text(); let enriched_lines = enriched.text();
    let a = original.rows(); let b = enriched.rows();
    assert_eq!(b.len(), 1004);
    for (index, (before, after)) in original_lines.lines().zip(enriched_lines.lines()).enumerate() {
        if index != 1000 { assert_eq!(before, after, "fallback must be byte compatible"); }
    }
    let mut row = b[1000].clone();
    assert_eq!(row.as_object_mut().unwrap().remove("network_trace").unwrap(), "trace-42");
    assert_eq!(row.as_object_mut().unwrap().remove("network_span").unwrap(), "0.7");
    assert_eq!(row, a[1000]);
    assert_eq!(b.iter().filter(|row| row.get("network_span").is_some()).count(), 1);
    // Exactly one occurrence per key, not merely last-value-wins JSON parsing.
    assert_eq!(enriched_lines.lines().nth(1000).unwrap().matches("\"network_trace\":").count(), 1);
}

#[test]
fn raw_csv_is_unchanged_even_for_marked_event() {
    let output = Buffer::default(); let writer = output.clone();
    let subscriber = tracing_subscriber::registry().with(tracing_subscriber::fmt::layer()
        .event_format(super::super::RawMessageFormat).with_writer(move || writer.clone()));
    tracing::subscriber::with_default(subscriber, || bcs_tracing_api::with_log_correlation(ids(), || {
        tracing::info!(log_class = "business_key", "2026-11-12,request,accepted");
    }));
    assert_eq!(output.text(), "2026-11-12,request,accepted\n");
}

#[tokio::test]
async fn nonblocking_writer_receives_identity_before_request_scopes_end() {
    use tracing::instrument::WithSubscriber;
    let output = Buffer::default();
    let (writer, guard) = tracing_appender::non_blocking(output.clone());
    let subscriber = tracing_subscriber::registry().with(tracing_subscriber::fmt::layer()
        .json().event_format(json(FixedTime)).with_writer(writer));
    let dispatch = tracing::Dispatch::new(subscriber);
    let mut tasks = Vec::new();
    for trace in ["request-a", "request-b"] {
        let ids = LogCorrelation::new("network_trace", trace, "network_span", "0.7").unwrap();
        let future = bcs_tracing_api::with_log_correlation(Some(ids), || bcs_tracing_api::in_current_log_context(async move {
            for _ in 0..20 {
                tokio::task::yield_now().await;
                tracing::info!(log_class = "business_key", request = trace, "result");
            }
        }));
        tasks.push(tokio::spawn(future.with_subscriber(dispatch.clone())));
    }
    for task in tasks { task.await.unwrap(); }
    assert!(current_log_correlation().is_none());
    drop(dispatch); drop(guard);
    let rows = output.rows(); assert_eq!(rows.len(), 40);
    for row in rows {
        assert_eq!(row["network_trace"], row["request"]);
        assert_eq!(row["network_span"], "0.7");
    }
}
