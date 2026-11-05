//! Exercise the public SPI with an SDK-backed W3C provider and real transports.
#![allow(clippy::unwrap_used)]
use std::sync::{Arc, Mutex};
use axum::{Router, body::Body, http::{HeaderMap, Request, StatusCode}, middleware, routing::post};
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_network_tracing::HttpServerFilter;
use bcs_protocol::{BcsFrame, BotDeliveryKind, RequestFrame};
use bcs_service_api::{BotDeliveryCommand, BotDeliveryPort};
use bcs_tracing_api::{Carrier, Kind, NetworkTracer, Outcome, ParentContext, Record, RequestInfo, StartedTrace, in_current_context, with_context, with_provider};
use opentelemetry::{Context, KeyValue, propagation::{Extractor, Injector, TextMapPropagator}, trace::{SpanKind, TraceContextExt, Tracer, TracerProvider}};
use opentelemetry_sdk::{propagation::TraceContextPropagator, trace::{InMemorySpanExporter, Sampler, SdkTracer, SdkTracerProvider}};
use tower::ServiceExt;
use tower_http::trace::TraceLayer;
use tracing::{Instrument, instrument::WithSubscriber};
use tracing_subscriber::prelude::*;

const REMOTE: &str = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
const STALE: &str = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01";

struct Headers(Carrier);
impl Extractor for Headers {
    fn get(&self, key: &str) -> Option<&str> {
        self.0.iter().find(|(name, _)| name.eq_ignore_ascii_case(key)).map(|(_, value)| value.as_str())
    }
    fn keys(&self) -> Vec<&str> { self.0.iter().map(|(name, _)| name.as_str()).collect() }
}
impl Injector for Headers {
    fn set(&mut self, key: &str, value: String) { self.0.push((key.into(), value)); }
}
fn extract(carrier: &Carrier) -> Context {
    TraceContextPropagator::new().extract_with_context(&Context::new(), &Headers(carrier.clone()))
}
fn assert_identity(actual: &Context, expected: &Context) {
    let actual_span = actual.span();
    let expected_span = expected.span();
    let actual = actual_span.span_context();
    let expected = expected_span.span_context();
    assert_eq!(actual.trace_id(), expected.trace_id());
    assert_eq!(actual.span_id(), expected.span_id());
    assert_eq!(actual.trace_flags(), expected.trace_flags());
    assert_eq!(actual.trace_state(), expected.trace_state());
}
fn wire_context(headers: &HeaderMap) -> Context {
    extract(&headers.iter().map(|(name, value)| (name.to_string(), value.to_str().unwrap().into())).collect())
}

#[derive(Clone)]
struct Start { kind: Kind, context: Context, parent: Context }
struct W3cTracer { tracer: SdkTracer, starts: Arc<Mutex<Vec<Start>>> }
struct Completion(Context);
impl Record for Completion {
    fn finish(self: Box<Self>, outcome: Outcome, _: Option<u64>) {
        self.0.span().set_attribute(KeyValue::new("network.outcome", format!("{outcome:?}")));
        self.0.span().end();
    }
}
impl NetworkTracer for W3cTracer {
    fn capture_ambient(&self) -> Carrier {
        // SDK integration belongs to the provider. Context activation unifies
        // native and tracing scopes without comparing unrelated current spans.
        let mut headers = Headers(vec![]);
        TraceContextPropagator::new().inject_context(&Context::current(), &mut headers);
        headers.0
    }

    fn start(&self, info: &RequestInfo<'_>, incoming: &Carrier, parent: ParentContext<'_>) -> StartedTrace {
        // The provider knows only SPI inputs and its own SDK, never BCS internals.
        let ambient = extract(parent.ambient);
        let base = if info.kind == Kind::HttpServer {
            extract(incoming)
        } else if ambient.span().span_context().is_valid() {
            ambient
        } else if let Some(local) = parent.local.and_then(|context| context.downcast_ref::<Context>()) {
            local.clone()
        } else {
            extract(incoming)
        };
        let server = info.kind == Kind::HttpServer;
        let mut builder = self.tracer.span_builder(if server { "network.server" } else { "network.client" })
            .with_kind(if server { SpanKind::Server } else { SpanKind::Client });
        if let Some(route) = info.route { builder = builder.with_attributes([KeyValue::new("http.route", route.to_owned())]); }
        let context = Context::new().with_span(self.tracer.build_with_context(builder, &base));
        self.starts.lock().unwrap().push(Start { kind: info.kind, context: context.clone(), parent: base });
        let mut headers = Headers(vec![]);
        TraceContextPropagator::new().inject_context(&context, &mut headers);
        StartedTrace { remove_headers: vec!["traceparent".into(), "tracestate".into()],
            context: Arc::new(context.clone()), headers: headers.0, record: Box::new(Completion(context)) }
    }
}

struct Fixture {
    sdk: SdkTracerProvider,
    exporter: InMemorySpanExporter,
    dispatch: tracing::Dispatch,
    plugin: Arc<dyn NetworkTracer>,
    starts: Arc<Mutex<Vec<Start>>>,
}
impl Fixture {
    fn new() -> Self {
        opentelemetry::global::set_text_map_propagator(TraceContextPropagator::new());
        let exporter = InMemorySpanExporter::default();
        let sdk = SdkTracerProvider::builder().with_simple_exporter(exporter.clone())
            .with_sampler(Sampler::ParentBased(Box::new(Sampler::AlwaysOn))).build();
        let dispatch = tracing::Dispatch::new(tracing_subscriber::registry()
            .with(tracing_opentelemetry::layer().with_tracer(sdk.tracer("business")).with_context_activation(true)));
        let starts = Arc::new(Mutex::new(vec![]));
        let plugin = Arc::new(W3cTracer { tracer: sdk.tracer("network"), starts: starts.clone() });
        Self { sdk, exporter, dispatch, plugin, starts }
    }
}

struct Peer { url: String, headers: Arc<Mutex<Vec<HeaderMap>>>, task: tokio::task::JoinHandle<()> }
impl Peer {
    async fn new() -> Self {
        let headers = Arc::new(Mutex::new(vec![]));
        let capture = headers.clone();
        let router = Router::new().route("/webhook", post(move |headers: HeaderMap| {
            capture.lock().unwrap().push(headers);
            async { axum::Json(serde_json::json!({"ok":true})) }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/webhook", listener.local_addr().unwrap());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
        Self { url, headers, task }
    }
}
impl Drop for Peer { fn drop(&mut self) { self.task.abort(); } }

async fn deliver(url: String) {
    bcs_provider_http::HttpProviderTransport::allowing_private_networks_for_tests()
        .deliver(BotDeliveryCommand {
            target: BotDeliveryTarget::HttpProvider {
                bot_id: "bot-provider".into(), provider_id: "provider".into(), provider_bot_ref: "bot".into(),
                webhook_url: url, bcs_to_provider_token: RedactedToken::new("test-fixture"), protocol_version: "1.0".into(),
            },
            run_id: "trace-test".into(),
            frame: BcsFrame::Request(RequestFrame::new("trace-test", "chat.send", Some(serde_json::json!({"message":{"text":"hello"}})))),
            delivery_kind: BotDeliveryKind::Send, provider_transport: Default::default(), provider_bypass_headers: vec![],
        }).await.unwrap();
}

#[tokio::test]
async fn server_gateway_business_and_provider_form_one_parent_chain() {
    for incoming in [Some(REMOTE), None] {
        for spawn in [false, true] {
            let fixture = Fixture::new();
            let peer = Peer::new().await;
            let url = peer.url.clone();
            let router = Router::new().route("/bots/{id}/chat", post(move |headers: HeaderMap| {
                let url = url.clone();
                async move {
                    // Middleware must not replace the original ingress.
                    assert_eq!(headers.get("traceparent").map(|v| v.to_str().unwrap()), incoming);
                    let work = async move {
                        if spawn { tokio::spawn(in_current_context(deliver(url))).await.unwrap(); }
                        else { deliver(url).await; }
                    };
                    work.instrument(tracing::info_span!("business.work")).await;
                    StatusCode::OK
                }
            }))
                .layer(TraceLayer::new_for_http().make_span_with(bcs_http::gateway_trace::BcnMakeSpan))
                .layer(middleware::from_fn_with_state(HttpServerFilter::default(), bcs_network_tracing::http_server));
            let mut request = Request::post("/bots/one/chat");
            if let Some(incoming) = incoming { request = request.header("traceparent", incoming); }
            with_provider(Some(fixture.plugin.clone()), router.oneshot(request.body(Body::empty()).unwrap()))
                .with_subscriber(fixture.dispatch.clone()).await.unwrap();
            fixture.sdk.force_flush().unwrap();
            let starts = fixture.starts.lock().unwrap();
            assert_eq!(starts.len(), 2);
            let network = &starts[0];
            let client = &starts[1];
            assert_eq!(network.kind, Kind::HttpServer);
            assert_eq!(client.kind, Kind::HttpClient);
            let spans = fixture.exporter.get_finished_spans().unwrap();
            let gateway = spans.iter().find(|span| span.name == "bcn.gateway.dispatch").unwrap();
            let business = spans.iter().find(|span| span.name == "business.work").unwrap();
            let server = spans.iter().find(|span| span.name == "network.server").unwrap();
            assert_eq!(spans.len(), 4);
            assert_eq!(gateway.parent_span_id, server.span_context.span_id());
            assert_eq!(business.parent_span_id, gateway.span_context.span_id());
            assert_eq!(client.parent.span().span_context().span_id(), business.span_context.span_id());
            let trace = server.span_context.trace_id();
            assert!(spans.iter().all(|span| span.span_context.trace_id() == trace));
            if incoming.is_some() {
                assert_eq!(trace.to_string(), REMOTE.split('-').nth(1).unwrap());
                assert_eq!(server.parent_span_id.to_string(), REMOTE.split('-').nth(2).unwrap());
            }
            assert!(server.attributes.contains(&KeyValue::new("http.route", "/bots/{id}/chat")));
            let headers = peer.headers.lock().unwrap();
            let downstream = wire_context(&headers[0]);
            assert_identity(&downstream, &client.context);
            assert_eq!(headers[0]["authorization"], "Bearer test-fixture");
            assert!(bcs_tracing_api::current_context().is_none());
        }
    }
}

#[tokio::test]
async fn provider_without_spi_parent_inherits_existing_gateway_span() {
    use tracing_opentelemetry::OpenTelemetrySpanExt;
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    with_provider(Some(fixture.plugin.clone()), async {
        let gateway = tracing::info_span!("gateway");
        let expected = gateway.context();
        with_context(None, deliver(peer.url.clone())).instrument(gateway).await;
        let starts = fixture.starts.lock().unwrap();
        assert_eq!(starts.len(), 1);
        assert_identity(&starts[0].parent, &expected);
    }).with_subscriber(fixture.dispatch.clone()).await;
}

#[tokio::test]
async fn ambient_business_context_wins_over_opaque_ancestor_and_stale_headers() {
    use bcs_network_tracing::RequestBuilderExt;
    use tracing_opentelemetry::OpenTelemetrySpanExt;
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    with_provider(Some(fixture.plugin.clone()), async {
        let business = tracing::info_span!("business");
        let expected = business.context();
        with_context(Some(Arc::new(extract(&vec![("traceparent".into(), STALE.into())]))),
            reqwest::Client::builder().no_proxy().build().unwrap().post(&peer.url)
                .header("traceparent", STALE).header("tracestate", "old=value").send_traced()).instrument(business).await.unwrap();
        assert_identity(&fixture.starts.lock().unwrap()[0].parent, &expected);
        assert_eq!(peer.headers.lock().unwrap()[0]["tracestate"], "");
    }).with_subscriber(fixture.dispatch.clone()).await;
}

#[tokio::test]
async fn unsampled_remote_trace_is_propagated_through_both_layers() {
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    let url = peer.url.clone();
    let router = Router::new().route("/bots/{id}/chat", post(move || {
        let url = url.clone();
        async move { deliver(url).await; StatusCode::OK }
    }))
        .layer(TraceLayer::new_for_http().make_span_with(bcs_http::gateway_trace::BcnMakeSpan))
        .layer(middleware::from_fn_with_state(HttpServerFilter::default(), bcs_network_tracing::http_server));
    with_provider(Some(fixture.plugin.clone()), router.oneshot(Request::post("/bots/one/chat")
        .header("traceparent", REMOTE.replace("-01", "-00")).body(Body::empty()).unwrap()))
        .with_subscriber(fixture.dispatch.clone()).await.unwrap();
    fixture.sdk.force_flush().unwrap();
    assert!(fixture.exporter.get_finished_spans().unwrap().is_empty());
    let starts = fixture.starts.lock().unwrap();
    assert_eq!(starts.len(), 2);
    let headers = peer.headers.lock().unwrap();
    let context = wire_context(&headers[0]);
    assert_eq!(context.span().span_context().trace_id().to_string(), REMOTE.split('-').nth(1).unwrap());
    assert!(!context.span().span_context().is_sampled());
    assert_identity(&context, &starts[1].context);
}

#[tokio::test]
async fn existing_headers_are_used_when_no_in_process_parent_exists() {
    use bcs_network_tracing::RequestBuilderExt;
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    with_provider(Some(fixture.plugin.clone()),
        reqwest::Client::builder().no_proxy().build().unwrap().post(&peer.url)
            .header("traceparent", REMOTE).send_traced()).await.unwrap();
    let starts = fixture.starts.lock().unwrap();
    assert_identity(&starts[0].parent, &extract(&vec![("traceparent".into(), REMOTE.into())]));
    assert_ne!(starts[0].context.span().span_context().span_id(), starts[0].parent.span().span_context().span_id());
    assert_identity(&wire_context(&peer.headers.lock().unwrap()[0]), &starts[0].context);
}

#[tokio::test]
async fn native_sdk_parent_is_captured_for_spawn_and_spawn_blocking() {
    use bcs_network_tracing::{BlockingRequestBuilderExt, RequestBuilderExt};
    use opentelemetry::trace::FutureExt;
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    let native = extract(&vec![("traceparent".into(), REMOTE.into())]);
    let expected = native.clone();
    let url = peer.url.clone();
    let (work, blocking) = with_provider(Some(fixture.plugin.clone()), async move {
        let blocking_url = url.clone();
        let work = in_current_context(async move {
            reqwest::Client::builder().no_proxy().build().unwrap().post(url).send_traced().await.unwrap();
        });
        let blocking = bcs_tracing_api::in_current_context_blocking(move || {
            reqwest::blocking::Client::builder().no_proxy().build().unwrap()
                .post(blocking_url).send_traced().unwrap();
        });
        (work, blocking)
    }.with_context(native)).await;
    // No native guard is retained while either child runs on another thread.
    assert!(!Context::current().span().span_context().is_valid());
    tokio::spawn(work).await.unwrap();
    tokio::task::spawn_blocking(blocking).await.unwrap();
    let starts = fixture.starts.lock().unwrap();
    assert_eq!(starts.len(), 2);
    for start in starts.iter() { assert_identity(&start.parent, &expected); }
    assert!(bcs_tracing_api::current_context().is_none());
    assert!(!Context::current().span().span_context().is_valid());
}

#[tokio::test]
async fn non_w3c_provider_preserves_ingress_w3c_despite_unrelated_native_context() {
    use opentelemetry::trace::FutureExt;
    struct SofaLike;
    struct NoopRecord;
    impl Record for NoopRecord {
        fn finish(self: Box<Self>, _: Outcome, _: Option<u64>) {}
    }
    impl NetworkTracer for SofaLike {
        fn start(&self, _: &RequestInfo<'_>, _: &Carrier, _: ParentContext<'_>) -> StartedTrace {
            StartedTrace { remove_headers: vec![], context: Arc::new(()), headers: vec![("sofatraceid".into(), "separate-sofa-id".into())], record: Box::new(NoopRecord) }
        }
    }
    let fixture = Fixture::new();
    let router = Router::new().route("/bots/{id}/chat", post(|| async { StatusCode::OK }))
        .layer(TraceLayer::new_for_http().make_span_with(bcs_http::gateway_trace::BcnMakeSpan))
        .layer(middleware::from_fn_with_state(HttpServerFilter::default(), bcs_network_tracing::http_server));
    with_provider(Some(Arc::new(SofaLike)), router.oneshot(Request::post("/bots/one/chat")
        .header("traceparent", REMOTE).body(Body::empty()).unwrap()))
        .with_context(extract(&vec![("traceparent".into(), STALE.into())]))
        .with_subscriber(fixture.dispatch.clone()).await.unwrap();
    fixture.sdk.force_flush().unwrap();
    let spans = fixture.exporter.get_finished_spans().unwrap();
    let gateway = spans.iter().find(|span| span.name == "bcn.gateway.dispatch").unwrap();
    assert_eq!(gateway.span_context.trace_id().to_string(), REMOTE.split('-').nth(1).unwrap());
    assert_eq!(gateway.parent_span_id.to_string(), REMOTE.split('-').nth(2).unwrap());
}

#[tokio::test]
async fn concurrent_children_share_business_parent_and_have_distinct_span_ids() {
    use tracing_opentelemetry::OpenTelemetrySpanExt;
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    with_provider(Some(fixture.plugin.clone()), async {
        let business = tracing::info_span!("business");
        let expected = business.context();
        async {
            let first = tokio::spawn(in_current_context(deliver(peer.url.clone())));
            let second = tokio::spawn(in_current_context(deliver(peer.url.clone())));
            first.await.unwrap();
            second.await.unwrap();
        }.instrument(business).await;
        let starts = fixture.starts.lock().unwrap();
        assert_eq!(starts.len(), 2);
        for start in starts.iter() { assert_identity(&start.parent, &expected); }
        assert_ne!(starts[0].context.span().span_context().span_id(), starts[1].context.span().span_context().span_id());
        assert_eq!(peer.headers.lock().unwrap().len(), 2);
    }).with_subscriber(fixture.dispatch.clone()).await;
}

#[tokio::test]
async fn current_native_context_overrides_spawn_snapshot_and_restores_after_scope() {
    use opentelemetry::trace::FutureExt;
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    let captured = extract(&vec![("traceparent".into(), REMOTE.into())]);
    let newer = extract(&vec![("traceparent".into(), STALE.into())]);
    let url = peer.url.clone();
    let expected = newer.clone();
    let work = with_provider(Some(fixture.plugin.clone()), async {
        in_current_context(async move {
            deliver(url.clone()).with_context(newer).await;
            deliver(url).await;
        })
    }.with_context(captured.clone())).await;
    tokio::spawn(work).await.unwrap();
    let starts = fixture.starts.lock().unwrap();
    assert_eq!(starts.len(), 2);
    assert_identity(&starts[0].parent, &expected);
    assert_identity(&starts[1].parent, &captured);
    assert!(!Context::current().span().span_context().is_valid());
}

#[tokio::test]
async fn sdk_provider_follows_mixed_scope_nesting_and_restoration() {
    use opentelemetry::trace::FutureExt;
    use tracing_opentelemetry::OpenTelemetrySpanExt;
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    with_provider(Some(fixture.plugin.clone()), async {
        let gateway = tracing::info_span!("gateway");
        let gateway_context = gateway.context();
        let tracer = fixture.sdk.tracer("native");
        let native = Context::new().with_span(tracer.start_with_context("native", &gateway_context));
        let inner_context = async {
            let inner_context = async {
                deliver(peer.url.clone()).await;
                let inner = tracing::info_span!("inner.tracing");
                let expected = inner.context();
                deliver(peer.url.clone()).instrument(inner).await;
                deliver(peer.url.clone()).await;
                expected
            }.with_context(native.clone()).await;
            deliver(peer.url.clone()).await;
            inner_context
        }.instrument(gateway).await;
        native.span().end();
        let starts = fixture.starts.lock().unwrap();
        assert_eq!(starts.len(), 4);
        for (start, expected) in starts.iter().zip([&native, &inner_context, &native, &gateway_context]) {
            assert_identity(&start.parent, expected);
        }
    }).with_subscriber(fixture.dispatch.clone()).await;
    fixture.sdk.force_flush().unwrap();
    let spans = fixture.exporter.get_finished_spans().unwrap();
    let native = spans.iter().find(|span| span.name == "native").unwrap();
    let inner = spans.iter().find(|span| span.name == "inner.tracing").unwrap();
    assert_eq!(inner.parent_span_id, native.span_context.span_id());
    assert!(!Context::current().span().span_context().is_valid());
}

#[tokio::test]
async fn default_no_provider_preserves_original_gateway_otel_and_outbound_headers() {
    let fixture = Fixture::new();
    let peer = Peer::new().await;
    let url = peer.url.clone();
    let router = Router::new().route("/bots/{id}/chat", post(move |headers: HeaderMap| {
        let url = url.clone();
        async move {
            assert_eq!(headers["traceparent"], REMOTE);
            assert!(bcs_tracing_api::current_context().is_none());
            deliver(url).await;
            StatusCode::OK
        }
    }))
        .layer(TraceLayer::new_for_http().make_span_with(bcs_http::gateway_trace::BcnMakeSpan))
        .layer(middleware::from_fn_with_state(HttpServerFilter::default(), bcs_network_tracing::http_server));
    with_provider(None, router.oneshot(Request::post("/bots/one/chat")
        .header("traceparent", REMOTE).body(Body::empty()).unwrap()))
        .with_subscriber(fixture.dispatch.clone()).await.unwrap();
    fixture.sdk.force_flush().unwrap();
    assert!(fixture.starts.lock().unwrap().is_empty());
    let spans = fixture.exporter.get_finished_spans().unwrap();
    let gateway = spans.iter().find(|span| span.name == "bcn.gateway.dispatch").unwrap();
    assert_eq!(gateway.span_context.trace_id().to_string(), REMOTE.split('-').nth(1).unwrap());
    assert_eq!(gateway.parent_span_id.to_string(), REMOTE.split('-').nth(2).unwrap());
    let headers = peer.headers.lock().unwrap();
    // Provider transport's pre-existing OTel injection still carries gateway G,
    // even though the optional network tracing SPI is disabled.
    let outbound = wire_context(&headers[0]);
    assert_eq!(outbound.span().span_context().trace_id(), gateway.span_context.trace_id());
    assert_eq!(outbound.span().span_context().span_id(), gateway.span_context.span_id());
    assert!(!headers[0].contains_key("sofatraceid"));
    assert_eq!(headers[0]["authorization"], "Bearer test-fixture");
}
