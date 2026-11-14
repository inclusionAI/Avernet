//! Real consumer/producer wire compatibility, without a live coding engine or BCS server.
use std::{path::PathBuf, sync::Arc};
use a2a::{SendMessageResponse, StreamResponse, TaskState};
use bcs_bridge_core::{config::ProviderConfig, engine::{Engine, EngineFactory, EngineRegistry, TurnRequest, TurnOutcome, TurnError}, AppState};
use bcs_a2a_http::{client::bind_rest_client, jsonrpc::JsonRpcClient, projection::{Output, Projection}};
use bcs_service_api::port::a2a_card::A2aCardPort;
use futures::StreamExt;
use serde_json::json;

struct Echo;
impl EngineFactory for Echo {
    fn id(&self) -> &str { "echo" }
    fn default_bin(&self) -> &str { "unused" }
    fn requires_bin(&self) -> bool { false }
    fn build(&self, _: PathBuf, _: &toml::Table) -> Result<Arc<dyn Engine>, String> { Ok(Arc::new(Echo)) }
}
#[async_trait::async_trait]
impl Engine for Echo {
    async fn run_turn(&self, request: TurnRequest, events: tokio::sync::mpsc::Sender<bcs_protocol::stream::StreamEvent>,
        abort: tokio_util::sync::CancellationToken) -> Result<TurnOutcome, TurnError> {
        events.send(bcs_bridge_core::sse::chat_delta(&request.run_id, "Hello ")).await.map_err(|_| TurnError::Aborted)?;
        if request.prompt == "wait" { abort.cancelled().await; return Err(TurnError::Aborted); }
        Ok(TurnOutcome { engine_session_id: None, final_text: Some("Hello from bridge".into()) })
    }
}

#[tokio::test]
async fn bcs_card_fetch_rest_rpc_projection_query_subscribe_and_cancel_interoperate() {
    let dir = tempfile::tempdir().unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let root = format!("http://{}", listener.local_addr().unwrap());
    let config = ProviderConfig::from_toml(&format!("provider_id='bridge'\nlisten='127.0.0.1:0'\nstate_path='state.db'\nbcs_to_provider_token='test-token'\n[a2a]\npublic_url='{root}'\n[[bot]]\nprovider_bot_ref='coder'\nengine='echo'\ncwd='{}'",dir.path().display()), &dir.path().join("bridge.toml")).unwrap();
    let mut engines = EngineRegistry::new(); engines.register(Echo);
    let state = Arc::new(AppState::new(config, &engines).unwrap());
    let app = bcs_bridge_core::webhook::router(state.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
    let fetcher = bcs_a2a_http::card_fetch::HttpA2aCardFetcher::new(bcs_route_security::OutboundUrlGuard::new(true, true), vec![]);
    let card = fetcher.fetch(&format!("{root}/.well-known/agent-card.json"), true).await.unwrap();
    assert_eq!(card.raw["supportedInterfaces"][0]["protocolBinding"], "HTTP+JSON");
    let selected = bcs_a2a_http::card::select_card_with_bindings(card.raw, &[], true, &["JSONRPC"], |_| true).unwrap();
    let endpoint = selected.interface.url;
    let client = || {
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert("authorization", "Bearer test-token".parse().unwrap());
        bcs_network_tracing::ClientWithDefaults::new(reqwest::Client::builder().no_proxy().timeout(std::time::Duration::from_secs(5)).build().unwrap(), headers)
    };
    let rest = bind_rest_client(client(), endpoint.clone(), None);
    let rpc = JsonRpcClient::new(client(), endpoint, None);
    for binding in ["rest", "rpc"] {
        let request = serde_json::from_value(json!({"message":{"messageId":binding,"role":"ROLE_USER","parts":[{"text":"hello"}]}})).unwrap();
        let stream = if binding == "rest" { rest.send_streaming_message(&request).await } else { rpc.send_streaming_message(&request).await }.unwrap();
        let mut projection = Projection::default(); let mut text = String::new(); let mut completed = false;
        for event in stream.collect::<Vec<_>>().await {
            let (next, outputs) = projection.stage(event.unwrap()).unwrap(); projection = next;
            for output in outputs { match output {
                Output::Delta(delta) => text.push_str(&delta),
                Output::Status(TaskState::Completed) => completed = true,
                _ => {},
            } }
        }
        assert_eq!(text, "Hello from bridge"); assert!(completed);
        let (id, _) = projection.identity().unwrap();
        let get = serde_json::from_value(json!({"id":id})).unwrap();
        assert_eq!(rest.get_task(&get).await.unwrap().status.state, TaskState::Completed);
        assert_eq!(rpc.get_task(&get).await.unwrap().status.state, TaskState::Completed);
        let subscribe = serde_json::from_value(json!({"id":id})).unwrap();
        let replay = rest.subscribe_to_task(&subscribe).await.unwrap().collect::<Vec<_>>().await;
        assert!(matches!(replay[0].as_ref().unwrap(), StreamResponse::Task(task) if task.status.state == TaskState::Completed));
        let cancel = serde_json::from_value(json!({"id":id})).unwrap();
        assert_eq!(rest.cancel_task(&cancel).await.unwrap_err().code, a2a::error_code::TASK_NOT_CANCELABLE);
        assert_eq!(rpc.cancel_task(&cancel).await.unwrap_err().code, a2a::error_code::TASK_NOT_CANCELABLE);
    }
    for binding in ["rest-cancel", "rpc-cancel"] {
        let request = serde_json::from_value(json!({"message":{"messageId":binding,"role":"ROLE_USER","parts":[{"text":"wait"}]},"configuration":{"returnImmediately":true}})).unwrap();
        let reply = if binding == "rest-cancel" { rest.send_message(&request).await } else { rpc.send_message(&request).await }.unwrap();
        let SendMessageResponse::Task(task) = reply else { panic!("Task expected") };
        let subscribe = serde_json::from_value(json!({"id":task.id})).unwrap();
        let events = rpc.subscribe_to_task(&subscribe).await.unwrap();
        let cancel = serde_json::from_value(json!({"id":task.id})).unwrap();
        let canceled = if binding == "rest-cancel" { rest.cancel_task(&cancel).await } else { rpc.cancel_task(&cancel).await }.unwrap();
        assert_eq!(canceled.status.state, TaskState::Canceled);
        let events = events.collect::<Vec<_>>().await;
        assert!(matches!(events.last().unwrap().as_ref().unwrap(), StreamResponse::StatusUpdate(update) if update.status.state == TaskState::Canceled));
    }
    let missing = serde_json::from_value(json!({"id":"missing"})).unwrap();
    assert_eq!(rest.get_task(&missing).await.unwrap_err().code, a2a::error_code::TASK_NOT_FOUND);
    assert_eq!(rpc.get_task(&missing).await.unwrap_err().code, a2a::error_code::TASK_NOT_FOUND);
    state.shutdown_engines().await; server.abort(); let _ = server.await;
}
