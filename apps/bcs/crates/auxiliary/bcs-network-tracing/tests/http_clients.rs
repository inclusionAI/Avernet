//! Exercise the public client extensions against a real loopback HTTP server.
#![allow(clippy::unwrap_used)]
use std::{sync::{Arc, Mutex}, time::Duration};
use axum::{Router, body::Bytes, http::{HeaderMap, StatusCode}, routing::post};
use bcs_tracing_api::{Carrier, Kind, NetworkTracer, Outcome, Record, RequestInfo, StartedTrace, with_context, with_provider};

#[derive(Debug, PartialEq)]
struct RequestMetadata {
    method: String,
    endpoint: String,
    size: Option<u64>,
    parent: Option<String>,
    existing_trace: Option<String>,
}

#[derive(Default)]
struct Spy {
    requests: Mutex<Vec<RequestMetadata>>,
    completions: Mutex<Vec<(Outcome, Option<u64>)>>,
}

struct Completion(Arc<Spy>);
impl Record for Completion {
    fn finish(self: Box<Self>, outcome: Outcome, size: Option<u64>) {
        self.0.completions.lock().unwrap().push((outcome, size));
    }
}

struct Provider(Arc<Spy>);
impl NetworkTracer for Provider {
    fn start(&self, info: &RequestInfo<'_>, incoming: &Carrier, parent: bcs_tracing_api::ParentContext<'_>) -> StartedTrace {
        assert_eq!(info.kind, Kind::HttpClient);
        assert!(info.target_app.is_empty());
        assert!(info.route.is_none());
        self.0.requests.lock().unwrap().push(RequestMetadata {
            method: info.method.to_owned(), endpoint: info.endpoint.to_owned(), size: info.request_size,
            parent: parent.local.and_then(|context| context.downcast_ref::<String>()).cloned(),
            existing_trace: incoming.iter().find(|(name, _)| name == "x-trace-id").map(|(_, value)| value.clone()),
        });
        StartedTrace {
            remove_headers: vec![], context: Arc::new(String::from("child")),
            headers: vec![
                ("x-trace-id".into(), "child".into()),
                ("invalid header".into(), "ignored".into()),
                ("x-invalid-value".into(), "invalid\r\nvalue".into()),
            ],
            record: Box::new(Completion(self.0.clone())),
        }
    }
}

struct Server {
    url: String,
    task: tokio::task::JoinHandle<()>,
}

impl Server {
    async fn start() -> Self {
        let router = Router::new()
            .route("/echo", post(|headers: HeaderMap, body: Bytes| async move {
                assert_eq!(headers["x-original"], "kept");
                assert_eq!(body, "payload");
                assert!(!headers.contains_key("x-invalid-value"));
                let trace = headers.get("x-trace-id").unwrap().clone();
                (StatusCode::SERVICE_UNAVAILABLE, [("x-trace-id", trace)], "reply")
            }))
            .route("/pending", post(|| async {
                std::future::pending::<()>().await;
                "never"
            }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
        Self { url, task }
    }

    fn private_url(&self) -> String {
        let mut url = reqwest::Url::parse(&format!("{}/echo", self.url)).unwrap();
        url.set_username("user").unwrap();
        url.set_password(Some("password")).unwrap();
        url.query_pairs_mut().append_pair("secret", "hidden");
        url.set_fragment(Some("private"));
        url.to_string()
    }
}

impl Drop for Server {
    fn drop(&mut self) { self.task.abort(); }
}

fn assert_parent_restored() {
    assert_eq!(bcs_tracing_api::current_context().unwrap().downcast_ref::<String>().unwrap(), "parent");
}

// Both supported reqwest versions must satisfy the same transport contract.
macro_rules! async_client_tests {
    ($module:ident, $client:ident) => {
        mod $module {
            use super::*;
            use bcs_network_tracing::{ClientExt, RequestBuilderExt};

            #[tokio::test]
            async fn sends_and_executes_preserve_http_errors_and_sanitize_trace_metadata() {
                let server = Server::start().await;
                let spy = Arc::new(Spy::default());
                let client = $client::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap();
                with_provider(Some(Arc::new(Provider(spy.clone()))), with_context(Some(Arc::new(String::from("parent"))), async {
                    for execute in [false, true] {
                        let request = client.post(server.private_url()).header("x-original", "kept")
                            .header("x-trace-id", "stale").body("payload");
                        let response = if execute {
                            client.execute_traced(request.build().unwrap()).await.unwrap()
                        } else {
                            request.send_traced().await.unwrap()
                        };
                        assert_eq!(response.status().as_u16(), 503);
                        assert_eq!(response.headers()["x-trace-id"], "child");
                        assert_eq!(response.text().await.unwrap(), "reply");
                        assert_parent_restored();
                    }
                })).await;
                let requests = spy.requests.lock().unwrap();
                assert_eq!(requests.len(), 2);
                for request in requests.iter() {
                    assert_eq!(request, &RequestMetadata {
                        method: "POST".into(), endpoint: format!("{}/echo", server.url),
                        size: Some(7), parent: Some("parent".into()), existing_trace: Some("stale".into()),
                    });
                }
                assert_eq!(*spy.completions.lock().unwrap(), vec![(Outcome::Http(503), Some(5)); 2]);
            }

            #[tokio::test]
            async fn no_provider_preserves_headers_and_build_errors_do_not_start_traces() {
                let server = Server::start().await;
                let client = $client::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap();
                with_provider(None, async {
                    let response = client.post(format!("{}/echo", server.url)).header("x-original", "kept")
                        .header("x-trace-id", "original").body("payload").send_traced().await.unwrap();
                    assert_eq!(response.status().as_u16(), 503);
                    assert_eq!(response.headers()["x-trace-id"], "original");
                    assert_eq!(response.text().await.unwrap(), "reply");
                }).await;
                let spy = Arc::new(Spy::default());
                with_provider(Some(Arc::new(Provider(spy.clone()))), async {
                    let error = client.post(&server.url).header("bad header", "value").send_traced().await.unwrap_err();
                    assert!(error.is_builder());
                }).await;
                assert!(spy.requests.lock().unwrap().is_empty());
                assert!(spy.completions.lock().unwrap().is_empty());
            }

            #[tokio::test]
            async fn transport_errors_timeouts_and_cancellation_finish_once() {
                let server = Server::start().await;
                let spy = Arc::new(Spy::default());
                let client = $client::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap();
                with_provider(Some(Arc::new(Provider(spy.clone()))), with_context(Some(Arc::new(String::from("parent"))), async {
                    // Use a freshly released loopback port instead of assuming
                    // a fixed port is unused on the test host.
                    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
                    let url = format!("http://{}", listener.local_addr().unwrap());
                    drop(listener);
                    let error = client.post(url).send_traced().await.unwrap_err();
                    assert!(error.is_connect());
                    assert_parent_restored();
                    let error = client.post(format!("{}/pending", server.url)).timeout(Duration::from_millis(50))
                        .send_traced().await.unwrap_err();
                    assert!(error.is_timeout());
                    assert_parent_restored();
                    let result = tokio::time::timeout(Duration::from_millis(50),
                        client.post(format!("{}/pending", server.url)).send_traced()).await;
                    assert!(result.is_err());
                    assert_parent_restored();
                })).await;
                assert_eq!(spy.requests.lock().unwrap().len(), 3);
                assert_eq!(*spy.completions.lock().unwrap(), vec![
                    (Outcome::TransportError, None), (Outcome::Timeout, None), (Outcome::Cancelled, None),
                ]);
            }
        }
    };
}

async_client_tests!(v12, reqwest);
#[cfg(feature = "reqwest13")]
async_client_tests!(v13, reqwest13);

#[cfg(feature = "blocking")]
mod blocking {
    use super::*;
    use bcs_network_tracing::{BlockingRequestBuilderExt, in_current_context_blocking};

    #[tokio::test]
    async fn blocking_client_preserves_responses_and_propagates_context() {
        let server = Server::start().await;
        let spy = Arc::new(Spy::default());
        let url = server.private_url();
        with_provider(Some(Arc::new(Provider(spy.clone()))), with_context(Some(Arc::new(String::from("parent"))), async {
            tokio::task::spawn_blocking(in_current_context_blocking(move || {
                let client = reqwest::blocking::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap();
                let response = client.post(url).header("x-original", "kept")
                    .header("x-trace-id", "stale").body("payload").send_traced().unwrap();
                assert_eq!(response.status().as_u16(), 503);
                assert_eq!(response.headers()["x-trace-id"], "child");
                assert_eq!(response.text().unwrap(), "reply");
                assert_parent_restored();
            })).await.unwrap();
            assert_parent_restored();
        })).await;
        assert_eq!(*spy.requests.lock().unwrap(), vec![RequestMetadata {
            method: "POST".into(), endpoint: format!("{}/echo", server.url),
            size: Some(7), parent: Some("parent".into()), existing_trace: Some("stale".into()),
        }]);
        assert_eq!(*spy.completions.lock().unwrap(), vec![(Outcome::Http(503), Some(5))]);
    }

    #[tokio::test]
    async fn blocking_client_handles_no_provider_build_and_transport_errors_and_timeouts() {
        let server = Server::start().await;
        let url = server.url.clone();
        with_provider(None, async {
            tokio::task::spawn_blocking(in_current_context_blocking(move || {
                let client = reqwest::blocking::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap();
                let response = client.post(format!("{url}/echo")).header("x-original", "kept")
                    .header("x-trace-id", "original").body("payload").send_traced().unwrap();
                assert_eq!(response.status().as_u16(), 503);
                assert_eq!(response.headers()["x-trace-id"], "original");
                assert_eq!(response.text().unwrap(), "reply");
            })).await.unwrap();
        }).await;
        let spy = Arc::new(Spy::default());
        let recorded = spy.clone();
        with_provider(Some(Arc::new(Provider(spy.clone()))), async {
            tokio::task::spawn_blocking(in_current_context_blocking(move || {
                let client = reqwest::blocking::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap();
                let error = client.post(&server.url).header("bad header", "value").send_traced().unwrap_err();
                assert!(error.is_builder());
                assert!(recorded.requests.lock().unwrap().is_empty());
                let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
                let url = format!("http://{}", listener.local_addr().unwrap());
                drop(listener);
                assert!(client.post(url).send_traced().unwrap_err().is_connect());
                assert!(client.post(format!("{}/pending", server.url)).timeout(Duration::from_millis(50))
                    .send_traced().unwrap_err().is_timeout());
            })).await.unwrap();
        }).await;
        assert_eq!(spy.requests.lock().unwrap().len(), 2);
        assert_eq!(*spy.completions.lock().unwrap(), vec![(Outcome::TransportError, None), (Outcome::Timeout, None)]);
    }
}
