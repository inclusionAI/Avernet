//! The agent card of a target served by this Gateway.

use crate::AppState;
use a2a::{AgentCapabilities, AgentCard, AgentInterface};
use axum::Json;
use axum::extract::{Path, State};

pub(crate) async fn handle(State(state): State<AppState>, Path(target): Path<String>) -> Json<AgentCard> {
    let base = state.config.public_url.trim_end_matches('/');
    let (url, description) = if state.direct {
        (format!("{base}/a2a/agents/{target}"), format!("Direct agent {target}"))
    } else {
        (format!("{base}/a2a/{target}"), format!("Agent group {target}"))
    };
    Json(AgentCard {
        name: target,
        description,
        version: "1".to_owned(),
        supported_interfaces: vec![AgentInterface::new(url, "JSONRPC")],
        capabilities: AgentCapabilities {
            streaming: Some(true),
            // Not in the MVP: push configuration calls return -32003.
            push_notifications: Some(false),
            extensions: None,
            extended_agent_card: Some(false),
        },
        default_input_modes: vec!["text/plain".to_owned()],
        default_output_modes: vec!["text/plain".to_owned()],
        skills: Vec::new(),
        provider: None,
        documentation_url: None,
        icon_url: None,
        security_schemes: None,
        security_requirements: None,
        signatures: None,
    })
}
