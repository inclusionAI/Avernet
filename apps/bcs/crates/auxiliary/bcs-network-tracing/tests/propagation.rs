//! Propagation replacement is protocol-owned; unrelated namespaces survive.
#![allow(clippy::unwrap_used)]
use std::sync::Arc;
use axum::{Router, http::HeaderMap, routing::get};
use bcs_network_tracing::ClientWithDefaults;
use bcs_tracing_api::{Carrier, NetworkTracer, Outcome, ParentContext, Record, RequestInfo, StartedTrace, with_provider};

const OLD_TRACE: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const NEW_TRACE: &str = "4bf92f3577b34da6a3ce929d0e0e4736";
const INPUT: &[(&str, &str)] = &[
    ("b3", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-1"),
    ("B3", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-1"),
    ("x-b3-traceid", OLD_TRACE), ("x-b3-spanid", "bbbbbbbbbbbbbbbb"),
    ("x-b3-sampled", "0"), ("x-b3-flags", "1"), ("x-b3-parentspanid", "cccccccccccccccc"),
    ("traceparent", "00-11111111111111111111111111111111-2222222222222222-01"),
    ("sofatraceid", "independent"), ("authorization", "Bearer preserved"), ("x-tenant", "tenant"),
];

fn default_headers() -> HeaderMap {
    let mut headers = HeaderMap::new();
    for (name, value) in INPUT { headers.append(*name, value.parse().unwrap()); }
    headers
}

struct B3Multi;
struct Completion;
impl Record for Completion {
    fn finish(self: Box<Self>, _: Outcome, _: Option<u64>) {}
}
impl NetworkTracer for B3Multi {
    fn start(&self, _: &RequestInfo<'_>, incoming: &Carrier, _: ParentContext<'_>) -> StartedTrace {
        // Extraction sees the complete original carrier, before any deletion.
        assert_eq!(incoming.iter().filter(|(name, _)| name.eq_ignore_ascii_case("b3")).count(), 2);
        StartedTrace {
            context: Arc::new(()),
            remove_headers: ["B3", "x-b3-traceid", "x-b3-spanid", "x-b3-sampled", "x-b3-flags", "x-b3-parentspanid", "invalid header"]
                .into_iter().map(String::from).collect(),
            headers: vec![("x-b3-traceid".into(), NEW_TRACE.into()),
                ("x-b3-spanid".into(), "00f067aa0ba902b7".into()), ("x-b3-sampled".into(), "1".into())],
            record: Box::new(Completion),
        }
    }
}

struct Peer { url: String, received: tokio::sync::mpsc::Receiver<HeaderMap>, task: tokio::task::JoinHandle<()> }
impl Peer {
    async fn new() -> Self {
        let (sent, received) = tokio::sync::mpsc::channel(1);
        let router = Router::new().route("/", get(move |headers: HeaderMap| {
            let sent = sent.clone();
            async move { sent.send(headers).await.unwrap(); "ok" }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/", listener.local_addr().unwrap());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
        Self { url, received, task }
    }
}
impl Drop for Peer { fn drop(&mut self) { self.task.abort(); } }

fn assert_wire(headers: &HeaderMap, enabled: bool) {
    // B3 receivers prefer the single header. Any retained old b3 would win.
    let extracted_trace = headers.get("b3").map(|value| value.to_str().unwrap().split('-').next().unwrap())
        .unwrap_or_else(|| headers["x-b3-traceid"].to_str().unwrap());
    assert_eq!(extracted_trace, if enabled { NEW_TRACE } else { OLD_TRACE });
    if enabled {
        assert!(!headers.contains_key("b3"));
        assert!(!headers.contains_key("x-b3-flags"));
        assert!(!headers.contains_key("x-b3-parentspanid"));
        assert_eq!(headers["x-b3-spanid"], "00f067aa0ba902b7");
        assert_eq!(headers["x-b3-sampled"], "1");
    } else {
        for (name, value) in INPUT { assert_eq!(headers[*name], *value); }
        assert_eq!(headers.get_all("b3").iter().count(), 2);
    }
    for (name, value) in &INPUT[7..] { assert_eq!(headers[*name], *value); }
}

macro_rules! async_clients {
    ($module:ident, $client:ident) => {
        mod $module {
            use super::*;
            use bcs_network_tracing::{ClientExt, RequestBuilderExt};
            #[tokio::test]
            async fn replaces_owned_fields_and_preserves_disabled_requests() {
                let mut peer = Peer::new().await;
                let client = ClientWithDefaults::new($client::Client::builder().no_proxy().build().unwrap(), default_headers());
                for enabled in [true, false] {
                    for (defaults_only, execute) in [(false, true), (true, true), (false, false), (true, false)] {
                        let request = client.request(|http| {
                            let mut request = http.get(&peer.url);
                            if !defaults_only {
                                for (name, value) in INPUT { request = request.header(*name, *value); }
                            }
                            request
                        });
                        let provider = enabled.then(|| Arc::new(B3Multi) as Arc<dyn NetworkTracer>);
                        with_provider(provider, async {
                            let response = if execute {
                                let mut raw = $client::Request::new($client::Method::GET, peer.url.parse().unwrap());
                                if !defaults_only { *raw.headers_mut() = default_headers(); }
                                client.execute_traced(raw).await.unwrap()
                            }
                                else { request.send_traced().await.unwrap() };
                            assert_eq!(response.text().await.unwrap(), "ok");
                        }).await;
                        assert_wire(&peer.received.recv().await.unwrap(), enabled);
                    }
                }
            }

            #[tokio::test]
            async fn defaults_preserve_overrides_sensitive_values_and_transport_policy() {
                let mut peer = Peer::new().await;
                let mut defaults = default_headers();
                let mut authorization = $client::header::HeaderValue::from_static("Bearer default");
                authorization.set_sensitive(true);
                defaults.insert("authorization", authorization);
                defaults.insert("x-opaque", $client::header::HeaderValue::from_bytes(&[0x80]).unwrap());
                let client = ClientWithDefaults::new($client::Client::builder().no_proxy().build().unwrap(), defaults);
                let built = client.request(|http| http.get(&peer.url)).build().unwrap();
                assert!(built.headers()["authorization"].is_sensitive());
                assert_eq!(built.headers()["x-opaque"].as_bytes(), &[0x80]);
                assert_eq!(built.headers().get_all("b3").iter().count(), 2);
                with_provider(None, client.request(|http| http.get(&peer.url)
                    .header("authorization", "Bearer override").header("x-tenant", "override"))
                    .map(|request| request.header("x-tenant", "second"))
                    .send_traced()).await.unwrap();
                let wire = peer.received.recv().await.unwrap();
                assert_eq!(wire["authorization"], "Bearer override");
                assert_eq!(wire.get_all("authorization").iter().count(), 1);
                assert_eq!(wire.get_all("x-tenant").iter().map(|v| v.to_str().unwrap()).collect::<Vec<_>>(), ["override", "second"]);
                assert_eq!(wire["x-opaque"].as_bytes(), &[0x80]);

                // A failed request build must not start the provider or reach the peer.
                let error = with_provider(Some(Arc::new(B3Multi)), client.request(|http| http.get(&peer.url)
                    .header("invalid header", "value")).send_traced()).await.unwrap_err();
                assert!(error.is_builder());

                let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
                let url = format!("http://{}", listener.local_addr().unwrap());
                let router = Router::new().route("/", get(|| async {
                    (axum::http::StatusCode::FOUND, [("location", "/slow")])
                })).route("/slow", get(|| async {
                    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                    "late"
                }));
                let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
                let client = ClientWithDefaults::new($client::Client::builder().no_proxy()
                    .redirect($client::redirect::Policy::none()).timeout(std::time::Duration::from_millis(100))
                    .build().unwrap(), default_headers());
                assert_eq!(client.request(|http| http.get(&url)).send().await.unwrap().status().as_u16(), 302);
                let error = with_provider(None, client.request(|http| http.get(format!("{url}/slow"))).send_traced()).await.unwrap_err();
                assert!(error.is_timeout());
                task.abort();
            }
        }
    };
}
async_clients!(v12, reqwest);
#[cfg(feature = "reqwest13")]
async_clients!(v13, reqwest13);

#[cfg(feature = "blocking")]
#[tokio::test]
async fn blocking_replaces_owned_fields_and_preserves_disabled_requests() {
    use bcs_network_tracing::{BlockingRequestBuilderExt, in_current_context_blocking};
    let mut peer = Peer::new().await;
    for (enabled, defaults_only) in [(true, false), (true, true), (false, false), (false, true)] {
        let url = peer.url.clone();
        let provider = enabled.then(|| Arc::new(B3Multi) as Arc<dyn NetworkTracer>);
        with_provider(provider, async {
            tokio::task::spawn_blocking(in_current_context_blocking(move || {
                let client = ClientWithDefaults::new(reqwest::blocking::Client::builder().no_proxy().build().unwrap(), default_headers());
                let request = client.request(|http| {
                    let mut request = http.get(url);
                    if !defaults_only {
                        for (name, value) in INPUT { request = request.header(*name, *value); }
                    }
                    request
                });
                assert_eq!(request.send_traced().unwrap().text().unwrap(), "ok");
            })).await.unwrap();
        }).await;
        assert_wire(&peer.received.recv().await.unwrap(), enabled);
    }
}
