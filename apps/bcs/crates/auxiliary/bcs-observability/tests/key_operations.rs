use std::{sync::{Arc, Mutex}, time::Duration};
use bcs_tracing_api::{Context, LogCorrelation, NetworkTracer, with_context, with_provider};
use tracing::{Event, Subscriber};
use tracing::instrument::WithSubscriber;
use tracing_subscriber::{Layer, layer::Context as LayerContext, prelude::*};

struct Provider;
impl NetworkTracer for Provider {
    fn start(&self, _: &bcs_tracing_api::RequestInfo<'_>, _: &bcs_tracing_api::Carrier, _: bcs_tracing_api::ParentContext<'_>) -> bcs_tracing_api::StartedTrace { panic!("logs never start spans") }
    fn log_correlation(&self, context: &Context) -> Option<LogCorrelation> { context.downcast_ref::<LogCorrelation>().cloned() }
}
fn ids(trace: &str) -> LogCorrelation { LogCorrelation::new("network_trace", trace, "network_span", "0.7").unwrap() }
#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<(serde_json::Value, Option<LogCorrelation>)>>>);
impl<S: Subscriber> Layer<S> for Capture {
    fn on_event(&self, event: &Event<'_>, _: LayerContext<'_, S>) {
        struct Fields(serde_json::Map<String, serde_json::Value>);
        impl tracing::field::Visit for Fields {
            fn record_str(&mut self, field: &tracing::field::Field, value: &str) { self.0.insert(field.name().into(), value.into()); }
            fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) { self.0.insert(field.name().into(), format!("{value:?}").into()); }
        }
        let mut fields = Fields(Default::default());
        event.record(&mut fields);
        self.0.lock().unwrap().push((serde_json::json!({"fields": fields.0}), bcs_tracing_api::current_log_correlation()));
    }
}

#[tokio::test(start_paused = true)]
async fn only_key_failures_and_one_stall_are_selected_and_drop_uses_original_identity() {
    let capture = Capture::default(); let output = capture.clone();
    async {
        with_provider(Some(Arc::new(Provider)), with_context(Some(Arc::new(ids("first"))), async {
            assert_eq!(bcs_observability::observe_key_result("test.success", async { Ok::<_, ()>(42) }).await, Ok(42));
            assert_eq!(bcs_observability::observe_key_result("test.error", async { Err::<(), _>("unchanged") }).await, Err("unchanged"));
            let _ = bcs_observability::observe_result("test.phase", async { Err::<(), _>(()) }).await;
            assert!(tokio::time::timeout(Duration::from_secs(12), bcs_observability::observe_key_result("test.cancel", std::future::pending::<Result<(), ()>>())).await.is_err());
            assert!(std::panic::catch_unwind(|| {
                let _guard = bcs_observability::Operation::new_key("test.panic");
                panic!("expected operation unwind");
            }).is_err());
            let guard = bcs_observability::Operation::new_key("test.guard");
            with_context(Some(Arc::new(ids("second"))), async { drop(guard); }).await;
        })).await;
    }.with_subscriber(tracing_subscriber::registry().with(capture)).await;
    let rows = output.0.lock().unwrap();
    let selected: Vec<_> = rows.iter().filter(|(event, _)| event["fields"]["log_class"] == "business_key").collect();
    assert_eq!(selected.len(), 5, "error, one stall, cancel, panic, guard: {rows:?}");
    for (_, identity) in selected { assert_eq!(identity.as_ref(), Some(&ids("first"))); }
    for name in ["test.success", "test.phase"] {
        let row = rows.iter().find(|(event, _)| event["fields"]["operation"] == name).unwrap();
        assert!(row.0["fields"].get("log_class").is_none());
    }
    assert!(bcs_tracing_api::current_log_correlation().is_none());
}
