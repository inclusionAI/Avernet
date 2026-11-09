//! Target-bound A2A 1.0 JSON-RPC delivery adapter. Only calls GatewayService.
use std::sync::Arc;
use async_trait::async_trait;
use axum::{Router, http::HeaderMap};
use bcn_runtime_api::application::GatewayService;
use bcn_runtime_model::*;
#[async_trait]
pub trait Authenticator: Send + Sync {
    async fn authenticate(&self, headers: &HeaderMap) -> RuntimeResult<CallerContext>;
}
#[derive(Clone)]
pub struct ApiState {
    pub gateway: Arc<dyn GatewayService>,
    pub auth: Arc<dyn Authenticator>,
    pub target: ExecutionTarget,
    pub card: a2a::AgentCard,
}
pub const MAX_BODY_BYTES: usize = 96 * 1024;
mod jsonrpc;
mod sse;
pub mod mapping;
pub fn router(mut state: ApiState) -> Router {
    state.card.default_input_modes=vec!["text/plain".into()];
    state.card.default_output_modes=vec!["text/plain".into()];
    state.card.capabilities.streaming=Some(true);
    state.card.capabilities.push_notifications=Some(false);
    state.card.capabilities.extended_agent_card=Some(false);
    for skill in &mut state.card.skills {
        skill.input_modes=Some(vec!["text/plain".into()]);
        skill.output_modes=Some(vec!["text/plain".into()]);
    }
    state.card.capabilities.extensions=Some(vec![
        a2a::AgentExtension { uri:mapping::INTERACTION.into(), required:Some(false), description:Some("Message.metadata key carries a full scoped {scope:{environment,tenant,namespace},value} interaction ID. Omission requires exactly one active question; status.message parts carry the same metadata key per question. Artifacts contain output only.".into()), params:None },
        a2a::AgentExtension { uri:mapping::STREAM.into(), required:Some(false), description:Some("Opt in with A2A-Extensions: urn:avernet:bcn:cursor; response acknowledges the same URI. Last-Event-ID is epoch:sequence and requires client state already applied at that cursor. Negotiated valid replay may start with updates and no Task (extension replay mode). Without negotiation, or with a fresh/expired cursor, start from current Task. Later Reset becomes artifact replacement/status updates. Clearing prior text uses one empty Text Part; never emit zero-part artifacts. Resume only within this authenticated target/task.".into()), params:None },
    ]);
    let card=state.card.clone();
    Router::new().route("/",axum::routing::post(jsonrpc::handle))
        .route("/.well-known/agent-card.json",axum::routing::get(move ||{let card=card.clone();async move{axum::Json(card)}}))
        .with_state(state)
}
