//! Conformance entry: `A2aDistAgentClient` against the shared port harness
//! (definite pre-send failure, ambiguous send, cancel without an answer).
use async_trait::async_trait;
use axum::Router;
use axum::routing::post;
use bcs_a2a_http::dist_agent::{A2aDistAgentClient, DistAgentResolver, ResolvedAgent};
use bcs_dist_model::*;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::port::dist_agent::StableSendRequest;
use bcs_service_api::types::a2a::A2aUpdateMode;
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aInterface};
use bcs_test_support::contract::distributed::services::dist_agent_client_port_contract_tests;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

/// The first resolution fails (nothing can be sent); later ones succeed.
struct FailsOnce {
    url: String,
    failed: AtomicBool,
}

#[async_trait]
impl DistAgentResolver for FailsOnce {
    async fn resolve(&self, _: &AgentId) -> Result<ResolvedAgent, String> {
        if !self.failed.swap(true, Ordering::SeqCst) {
            return Err("target_unresolved".into());
        }
        Ok(ResolvedAgent {
            interface: A2aInterface {
                url: self.url.clone(),
                tenant: None,
                protocol_version: "1.0".into(),
                binding: A2aBinding::JsonRpc,
                update_mode: A2aUpdateMode::Stream,
            },
            bearer: None,
            extensions: Vec::new(),
        })
    }
}

#[tokio::test]
async fn conformance_a2a_dist_agent_client() {
    // An agent that answers every request with 503: nothing can be concluded.
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = Router::new().route("/rpc", post(|| async { axum::http::StatusCode::SERVICE_UNAVAILABLE }));
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let resolver = FailsOnce { url: format!("http://{address}/rpc"), failed: AtomicBool::new(false) };
    let client = A2aDistAgentClient::new(Arc::new(resolver), OutboundUrlGuard::new(false, true), None);

    let target = AgentId("agent-analyst".into());
    let request = |id: &str| StableSendRequest {
        request_id: RequestId(id.into()),
        context_id: None,
        payload: Payload::Inline(serde_json::json!({"parts": [{"text": "hi"}]})),
    };
    let remote = RemoteTaskRef { task_id: "rt-1".into(), context_id: None };
    dist_agent_client_port_contract_tests(
        &client,
        &target,
        request("not-sent"),
        request("ambiguous"),
        &remote,
        RequestId("cancel-1".into()),
    )
    .await
    .unwrap();
}
