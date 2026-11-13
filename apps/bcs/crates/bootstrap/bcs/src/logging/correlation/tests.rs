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
    let duplicate = Buffer::default();
    let a = original.clone(); let b = enriched.clone(); let c = duplicate.clone();
    let subscriber = tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer().with_ansi(false).with_timer(FixedTime).with_writer(move || a.clone()))
        .with(tracing_subscriber::fmt::layer().with_ansi(false).event_format(text(FixedTime)).with_writer(move || b.clone()))
        .with(tracing_subscriber::fmt::layer().with_ansi(false).event_format(text(FixedTime)).with_writer(move || c.clone()));
    tracing::subscriber::with_default(subscriber, || {
        bcs_tracing_api::with_log_correlation(ids(), || {
            tracing::info!(target: "business", count = 3, "ordinary");
            tracing::info!(target: "business", log_class = "business_key", count = 3, "key");
        });
        tracing::info!(target: "business", log_class = "business_key", "no context");
    });
    let original = original.text(); let enriched = enriched.text();
    assert_eq!(enriched, duplicate.text());
    let a: Vec<_> = original.lines().collect(); let b: Vec<_> = enriched.lines().collect();
    assert_eq!(a[0], b[0]);
    assert_eq!(format!("{} network_trace=trace-42 network_span=0.7", a[1]), b[1]);
    assert_eq!(a[2], b[2]);
}

#[test]
fn text_preserves_ansi_spans_multiline_utf8_and_repeated_messages() {
    for ansi in [false, true] {
        let original = Buffer::default(); let enriched = Buffer::default();
        let a = original.clone(); let b = enriched.clone();
        let subscriber = tracing_subscriber::registry()
            .with(tracing_subscriber::fmt::layer().with_ansi(ansi).with_timer(FixedTime).with_writer(move || a.clone()))
            .with(tracing_subscriber::fmt::layer().with_ansi(ansi).event_format(text(FixedTime)).with_writer(move || b.clone()));
        tracing::subscriber::with_default(subscriber, || bcs_tracing_api::with_log_correlation(ids(), || {
            let span = tracing::info_span!("parent", field = "value");
            let _entered = span.enter();
            for payload in ["x".repeat(8192), "中文\n\nnext\r\n".into(), String::new(), "short".into()] {
                original.0.lock().unwrap().clear(); enriched.0.lock().unwrap().clear();
                tracing::info!(log_class = "business_key", count = 7, "{payload}");
                let before = original.text();
                assert_eq!(enriched.text(), format!("{} network_trace=trace-42 network_span=0.7\n", before.strip_suffix('\n').unwrap()), "ansi={ansi}");
            }
        }));
    }
}

#[test]
fn text_reuses_scratch_capacity_and_releases_oversized_lines() {
    let subscriber = tracing_subscriber::registry().with(tracing_subscriber::fmt::layer()
        .with_ansi(false).event_format(text(FixedTime)).with_writer(std::io::sink));
    tracing::subscriber::with_default(subscriber, || bcs_tracing_api::with_log_correlation(ids(), || {
        let emit = |payload: &str| tracing::info!(log_class = "business_key", "{payload}");
        emit("warmup");
        let (pointer, capacity) = TEXT_BUFFER.with(|buffer| {
            let buffer = buffer.borrow();
            assert!(buffer.is_empty());
            (buffer.as_ptr(), buffer.capacity())
        });
        assert!(capacity > 0);
        for _ in 0..100 {
            emit("warmup");
            TEXT_BUFFER.with(|buffer| {
                let buffer = buffer.borrow();
                assert!(buffer.is_empty());
                assert_eq!((buffer.as_ptr(), buffer.capacity()), (pointer, capacity));
            });
        }
        emit(&"x".repeat(MAX_RETAINED_TEXT_CAPACITY * 2));
        TEXT_BUFFER.with(|buffer| {
            let buffer = buffer.borrow();
            assert!(buffer.is_empty());
            assert!(buffer.capacity() <= MAX_RETAINED_TEXT_CAPACITY);
        });
        emit("after oversized line");
        TEXT_BUFFER.with(|buffer| assert!(buffer.borrow().is_empty()));
    }));
}

#[test]
fn text_clears_partial_lines_after_formatting_errors_and_panics() {
    struct Fails(bool);
    impl fmt::Debug for Fails {
        fn fmt(&self, writer: &mut fmt::Formatter<'_>) -> fmt::Result {
            writer.write_str("partial-field")?;
            if self.0 { panic!("field formatter panic"); }
            Err(fmt::Error)
        }
    }
    let output = Buffer::default(); let writer = output.clone();
    let subscriber = tracing_subscriber::registry().with(tracing_subscriber::fmt::layer()
        .with_ansi(false).event_format(text(FixedTime)).with_writer(move || writer.clone()));
    tracing::subscriber::with_default(subscriber, || bcs_tracing_api::with_log_correlation(ids(), || {
        tracing::info!(log_class = "business_key", value = ?Fails(false), "failed");
        TEXT_BUFFER.with(|buffer| assert!(buffer.borrow().is_empty()));
        let panic = std::panic::catch_unwind(|| {
            tracing::info!(log_class = "business_key", value = ?Fails(true), "panicked");
        });
        assert!(panic.is_err());
        TEXT_BUFFER.with(|buffer| assert!(buffer.borrow().is_empty()));
        output.0.lock().unwrap().clear();
        tracing::info!(target: "business", log_class = "business_key", "recovered");
    }));
    assert_eq!(output.text(), "2026-11-12 12:00:00.123  INFO business: recovered log_class=\"business_key\" network_trace=trace-42 network_span=0.7\n");
}

#[test]
fn text_uses_local_scratch_when_shared_buffer_is_already_borrowed() {
    let output = Buffer::default(); let writer = output.clone();
    let subscriber = tracing_subscriber::registry().with(tracing_subscriber::fmt::layer()
        .with_ansi(false).event_format(text(FixedTime)).with_writer(move || writer.clone()));
    TEXT_BUFFER.with(|buffer| {
        let mut outer = buffer.borrow_mut();
        outer.clear();
        outer.push_str("outer scratch");
        tracing::subscriber::with_default(subscriber, || bcs_tracing_api::with_log_correlation(ids(), || {
            tracing::info!(log_class = "business_key", "nested");
        }));
        // A nested formatter must neither overwrite nor clear its caller's line.
        assert_eq!(*outer, "outer scratch");
        outer.clear();
    });
    assert!(output.text().contains("nested log_class=\"business_key\" network_trace=trace-42 network_span=0.7\n"));
    assert!(!output.text().contains("outer scratch"));
}

#[tokio::test]
async fn selection_fast_paths_skip_provider_and_missing_context_falls_back() {
    use bcs_tracing_api::{Context, NetworkTracer};
    use std::sync::atomic::{AtomicUsize, Ordering};
    struct Provider(Arc<AtomicUsize>);
    impl NetworkTracer for Provider {
        fn start(&self, _: &bcs_tracing_api::RequestInfo<'_>, _: &bcs_tracing_api::Carrier, _: bcs_tracing_api::ParentContext<'_>) -> bcs_tracing_api::StartedTrace {
            panic!("formatting never starts a trace")
        }
        fn log_correlation(&self, context: &Context) -> Option<LogCorrelation> {
            self.0.fetch_add(1, Ordering::Relaxed);
            context.downcast_ref::<LogCorrelation>().cloned()
        }
    }
    let calls = Arc::new(AtomicUsize::new(0));
    let output = Buffer::default(); let writer = output.clone();
    let dispatch = tracing::Dispatch::new(tracing_subscriber::registry().with(tracing_subscriber::fmt::layer()
        .json().event_format(json(FixedTime)).with_writer(move || writer.clone())));
    bcs_tracing_api::with_provider(Some(Arc::new(Provider(calls.clone()))), async {
        bcs_tracing_api::with_context(Some(Arc::new(ids().unwrap())), async {
            tracing::dispatcher::with_default(&dispatch, || {
                tracing::info!("no marker");
                tracing::info!(log_class = Option::<&str>::None, "empty marker");
                tracing::info!(log_class = "ordinary", "other marker");
                tracing::info!(target: "bcn_otel", log_class = "business_key", "otel");
                assert_eq!(calls.load(Ordering::Relaxed), 0);
                tracing::info!(log_class = "business_key", "selected");
                assert_eq!(calls.load(Ordering::Relaxed), 1);
            });
            bcs_tracing_api::with_provider(None, async {
                tracing::dispatcher::with_default(&dispatch, || tracing::info!(log_class = "business_key", "disabled"));
            }).await;
        }).await;
        tracing::dispatcher::with_default(&dispatch, || tracing::info!(log_class = "business_key", "no context"));
    }).await;
    assert_eq!(calls.load(Ordering::Relaxed), 1);
    let rows = output.rows();
    assert_eq!(rows.len(), 7);
    for (index, row) in rows.iter().enumerate() {
        assert_eq!(row.get("network_trace").is_some(), index == 4);
        assert_eq!(row.get("network_span").is_some(), index == 4);
    }
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
