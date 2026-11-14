//! Opt-in A2A 1.0 HTTP+JSON / JSON-RPC server adapter for Bridge engines.
//! SDK types stay here; gateway/plugin execution and engine contracts are unchanged.
pub mod config;
mod card;
mod service;
mod task;
mod wire;

use std::{collections::HashMap, sync::Arc};
use axum::{extract::{DefaultBodyLimit, Path, State}, http::StatusCode, response::{IntoResponse, Response}, routing::{get, post}, Json, Router};
use crate::AppState;
use service::Service;

type Services = Arc<HashMap<String, Arc<Service>>>;

/// Public cards carry only configured public metadata. Operations require the
/// target Provider's existing bearer credential, never a token from another group.
pub fn router(states: Vec<Arc<AppState>>) -> Router {
    let services: HashMap<_, _> = states.into_iter().filter(|state| state.config.a2a.is_some())
        .map(|state| (state.config.provider_id.clone(), Arc::new(Service::new(state)))).collect();
    if services.is_empty() { return Router::new(); }
    Router::new()
        .route("/.well-known/agent-card.json", get(single_card))
        .route("/a2a/{provider}/{bot}/.well-known/agent-card.json", get(agent_card))
        .route("/a2a/{provider}/{bot}", post(wire::rpc))
        .route("/a2a/{provider}/{bot}/message:send", post(wire::send))
        .route("/a2a/{provider}/{bot}/message:stream", post(wire::send_stream))
        .route("/a2a/{provider}/{bot}/tasks", get(wire::list))
        .route("/a2a/{provider}/{bot}/tasks/{task}", get(wire::get_task).post(wire::task_action))
        .layer(DefaultBodyLimit::max(task::MAX_TEXT_BYTES))
        .with_state(Arc::new(services))
}

fn card_response(service: &Service, bot: &str) -> Response {
    if service.state.config.bot(bot).is_none() { return StatusCode::NOT_FOUND.into_response(); }
    let config = service.state.config.a2a.as_ref().expect("A2A-enabled service");
    // Host / Forwarded headers are deliberately not used for advertised endpoints.
    ([("cache-control", "public, max-age=60")], Json(card::card(config, &service.state.config.provider_id, bot))).into_response()
}

async fn agent_card(State(services): State<Services>, Path((provider, bot)): Path<(String, String)>) -> Response {
    match services.get(&provider) {
        Some(service) => card_response(service, &bot),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn single_card(State(services): State<Services>) -> Response {
    let mut bots = services.values().flat_map(|service| service.state.config.bots.iter().map(move |bot| (service, bot)));
    let Some((service, bot)) = bots.next() else { return StatusCode::NOT_FOUND.into_response(); };
    if bots.next().is_some() { return StatusCode::NOT_FOUND.into_response(); }
    card_response(service, &bot.provider_bot_ref)
}
