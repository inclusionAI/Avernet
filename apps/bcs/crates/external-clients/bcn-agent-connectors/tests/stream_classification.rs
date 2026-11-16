mod common;
use common::*;
use bcn_agent_connectors::a2a::A2aConnector;
use bcn_runtime_api::port::AgentConnector;
use bcn_runtime_model::*;
use futures::StreamExt;
use serde_json::{json,Value};

#[tokio::test]
async fn malformed_identity_budget_and_peer_error_are_not_transport_retries() {
 for protocol in ["HTTP+JSON","JSONRPC"] {for failure in ["malformed","identity","budget","peer-error"] {
  let rpc=protocol=="JSONRPC";
  let handler=move |axum::Json(req):axum::Json<Value>|async move {
   let task=json!({"task":{"id":"task","contextId":"context","status":{"state":"TASK_STATE_WORKING"},"artifacts":[{"artifactId":"a","parts":[{"text":"partial"}]}]}});
   let wrap=|event:Value|if rpc{json!({"jsonrpc":"2.0","id":req["id"],"result":event})}else{event};
   let first=wrap(task);
   let suffix=match failure {
    "malformed"=>"{broken JSON".into(),
    "identity"=>wrap(json!({"task":{"id":"other","contextId":"context","status":{"state":"TASK_STATE_WORKING"}}})).to_string(),
    "budget"=>wrap(json!({"artifactUpdate":{"taskId":"task","contextId":"context","artifact":{"artifactId":"a","parts":[{"text":"x".repeat(65536)}]},"append":true}})).to_string(),
    // Even the exact local diagnostic supplied by a peer must not become a
    // transport discriminator: JSON-RPC decoding normalizes peer diagnostics.
    _=>json!({"jsonrpc":"2.0","id":req["id"],"error":{"code":-32603,"message":"A2A subscription interrupted"}}).to_string(),
   };
   axum::response::Response::builder().header("content-type","text/event-stream").body(axum::body::Body::from(format!("data: {first}\n\ndata: {suffix}\n\n"))).unwrap()
  };
  let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();let url=format!("http://{}",listener.local_addr().unwrap());
  let server=tokio::spawn(async move{axum::serve(listener,axum::Router::new().fallback(axum::routing::post(handler))).await.unwrap();});
  let c=A2aConnector::new(binding(),card(url,protocol),http()).unwrap();
  let events=c.send_streaming_message(request()).await.unwrap().collect::<Vec<_>>().await;server.abort();
  assert!(matches!(events.first(),Some(Ok(AgentEvent::Accepted{handle:h})) if h==&handle()));
  assert!(events.iter().all(Result::is_ok),"{protocol}/{failure}: {events:?}");
  assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{output,..})) if output==&vec![MessagePart::Text{text:"partial".into()}]),"{protocol}/{failure}: {events:?}");
 }}
}
