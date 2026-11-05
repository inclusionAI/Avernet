//! Opt-in bridge from existing OTel business spans to the vendor-neutral SPI.
use bcs_tracing_api::{Carrier, NetworkTracer, ParentContext, RequestInfo, StartedTrace};
use opentelemetry::{global, propagation::Injector, trace::TraceContextExt};
use tracing_opentelemetry::OpenTelemetrySpanExt;

pub struct OtelContextBridge<T>(pub T);

impl<T: NetworkTracer> NetworkTracer for OtelContextBridge<T> {
    fn start(&self, info: &RequestInfo<'_>, incoming: &Carrier, parent: ParentContext<'_>) -> StartedTrace {
        self.0.start(info, incoming, parent)
    }

    fn capture_ambient(&self) -> Carrier {
        let span = tracing::Span::current().context();
        let context = if span.span().span_context().is_valid() { span } else { opentelemetry::Context::current() };
        if !context.span().span_context().is_valid() { return self.0.capture_ambient(); }
        struct Headers(Carrier);
        impl Injector for Headers {
            fn set(&mut self, key: &str, value: String) { self.0.push((key.to_owned(), value)); }
        }
        let mut headers = Headers(Vec::new());
        global::get_text_map_propagator(|propagator| propagator.inject_context(&context, &mut headers));
        headers.0
    }
}
