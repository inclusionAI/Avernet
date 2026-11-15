use std::sync::{Arc, Mutex};
use bcs_storage_api::{StorageHandle, StoragePlugin};
use bcs_storage_baas::{config::BaasConfig, BaasReadyHandle, BaasStoragePlugin};
use bcs_tracing_api::{Carrier, Kind, NetworkTracer, Outcome, Record, RequestInfo, StartedTrace, with_context, with_provider};
use wiremock::{Mock, MockServer, ResponseTemplate, matchers::{method, path}};

struct Provider(Arc<Mutex<Vec<Outcome>>>);
struct Completion(Arc<Mutex<Vec<Outcome>>>);
impl Record for Completion {
    fn finish(self: Box<Self>, outcome: Outcome, _: Option<u64>) {
        self.0.lock().unwrap().push(outcome);
    }
}
impl NetworkTracer for Provider {
    fn start(&self, info: &RequestInfo<'_>, _: &Carrier, parent: bcs_tracing_api::ParentContext<'_>) -> StartedTrace {
        assert_eq!(info.kind, Kind::HttpClient);
        assert_eq!(info.method, "DELETE");
        assert_eq!(parent.local.unwrap().downcast_ref::<String>().unwrap(), "business-parent");
        StartedTrace { remove_headers: vec![], context: Arc::new(()), headers: vec![("sofatraceid".into(), "business-parent".into())],
            record: Box::new(Completion(self.0.clone())) }
    }
}

#[tokio::test]
async fn health_uses_plain_http_while_file_operations_keep_the_parent_trace() {
    let server = MockServer::start().await;
    Mock::given(method("GET")).and(path("/"))
        .respond_with(ResponseTemplate::new(200)).expect(2).mount(&server).await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/transfers/tid"))
        .respond_with(ResponseTemplate::new(204)).expect(1).mount(&server).await;
    let plugin = BaasStoragePlugin::new(BaasConfig {
        endpoint: server.uri(), tenant: "t".into(), ..Default::default()
    }, 1024);
    let records = Arc::new(Mutex::new(Vec::new()));
    with_provider(Some(Arc::new(Provider(records.clone()))),
        with_context(Some(Arc::new("business-parent".to_string())), async {
            assert!(plugin.health_check().await.unwrap().ok);
            plugin.delete(&StorageHandle { backend: "baas".into(), key: "session-files/prod/sid/f/f".into(),
                backend_handle: serde_json::to_value(BaasReadyHandle { transfer_id: "tid".into() }).unwrap()
            }).await.unwrap();
            assert!(plugin.health_check().await.unwrap().ok);
        })
    ).await;
    assert_eq!(*records.lock().unwrap(), vec![Outcome::Http(204)]);
    let requests = server.received_requests().await.unwrap();
    assert_eq!(requests.len(), 3);
    for request in requests {
        if request.method == "GET" {
            assert!(!request.headers.contains_key("sofatraceid"));
        } else {
            assert_eq!(request.headers["sofatraceid"], "business-parent");
        }
    }
}
