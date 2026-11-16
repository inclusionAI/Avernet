#![allow(dead_code)]
use bcn_runtime_model::*;
use axum::{Router, routing::post, extract::State, Json, http::HeaderMap, response::{Response, IntoResponse}};
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};
pub fn binding() -> FrozenTargetBinding {
    FrozenTargetBinding { target: AgentId::new(scope(), "agent"), binding_id: "bound".into(), binding_version: "v1".into(), credential_ref: None,
        capabilities: ConnectorCapabilities { streaming:true, query:true, subscribe:true, input_continuation:true,
            cancellation:CancellationSupport::SingleTask, idempotency:IdempotencySupport::Unsupported } }
}
pub fn scope() -> Scope { Scope::new("test", "tenant", "runtime") }
pub fn request() -> AgentSendRequest {
    AgentSendRequest { dispatch_id:DispatchId::new(scope(), "dispatch"), operation_id:OperationId::new(scope(), "operation"), binding:binding(),
        message:Message { message_id:MessageId::new(scope(), "message"), context_id:Some(SessionId::new(scope(), "session")), task_id:None,
            parts:vec![MessagePart::Text{text:"hello".into()}] }, handle:None, expires_at_ms:None }
}
pub fn handle() -> ExecutionHandle { ExecutionHandle { target:binding().target, binding_version:"v1".into(), remote_task_id:Some("task".into()), remote_context_id:Some("context".into()) } }
pub fn http() -> bcs_network_tracing::ClientWithDefaults<reqwest::Client> {
    let mut headers = HeaderMap::new(); headers.insert("authorization", "Bearer fixture".parse().unwrap());
    bcs_network_tracing::ClientWithDefaults::new(reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).build().unwrap(), headers)
}
pub fn card(url: String, protocol: &str) -> bcs_a2a_http::card::SelectedCard {
    bcs_a2a_http::card::select_card_with_bindings(json!({"name":"Fixture","description":"local test","version":"1",
      "supportedInterfaces":[{"url":url,"protocolBinding":protocol,"protocolVersion":"1.0"}],
      "capabilities":{"streaming":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"skills":[]}),
      &[], true, &[protocol], |_|true).unwrap()
}
pub struct Fixture { pub url:String, pub calls:Arc<Mutex<Vec<(HeaderMap,Value)>>>, task:tokio::task::JoinHandle<()> }
impl Drop for Fixture { fn drop(&mut self) { self.task.abort(); } }
#[derive(Clone)]
struct StateData { calls:Arc<Mutex<Vec<(HeaderMap,Value)>>>, body:String, mime:String, rpc:bool, fragments:bool }
async fn reply(State(s):State<StateData>, headers:HeaderMap, Json(request):Json<Value>) -> Response {
    s.calls.lock().unwrap().push((headers,request.clone()));
    if s.rpc { return Json(json!({"jsonrpc":"2.0","id":request["id"],"result":serde_json::from_str::<Value>(&s.body).unwrap()})).into_response(); }
    let chunks:Vec<Result<Vec<u8>, std::io::Error>> = if s.fragments { s.body.as_bytes().chunks(if s.body.len()>4096 {1024} else {1}).map(|b|Ok(b.to_vec())).collect() } else { vec![Ok(s.body.as_bytes().to_vec())] };
    Response::builder().header("content-type",s.mime).body(axum::body::Body::from_stream(futures::stream::iter(chunks))).unwrap()
}
pub async fn fixture(body:String, mime:&str, rpc:bool) -> Fixture {
    let calls=Arc::new(Mutex::new(vec![]));
    let state=StateData {calls:calls.clone(),body,mime:mime.into(),rpc,fragments:true};
    let router=Router::new().fallback(post(reply)).with_state(state);
    let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url=format!("http://{}",listener.local_addr().unwrap());
    let task=tokio::spawn(async move {axum::serve(listener,router).await.unwrap();});
    Fixture{url,calls,task}
}
