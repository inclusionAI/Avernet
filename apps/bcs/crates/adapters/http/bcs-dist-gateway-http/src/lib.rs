//! Inbound A2A delivery adapter of the distributed Gateway: JSON-RPC over
//! HTTP for the legacy binding, REST+JSON and Server-Sent Events for the
//! published FSM-group surface (plan T8).
//!
//! The adapter owns the A2A wire types of both bindings and calls only the
//! application services `DistGatewayService` and `GroupDiscoveryService`. It
//! does not authenticate: the layer in front of the router puts the resolved
//! [`GatewayCaller`] into the request extensions.
//!
//! | Legacy JSON-RPC method | Use case calls |
//! | --- | --- |
//! | `SendMessage` | `submit`, then `get_task` (return immediately) or `wait_task` |
//! | `SendStreamingMessage` | `submit`, then `subscribe(Snapshot)` |
//! | `GetTask` | `get_task` |
//! | `SubscribeToTask` | `subscribe(Resume)` with a usable `Last-Event-ID`, else `subscribe(Snapshot)` |
//! | `CancelTask` | `cancel`, then `wait_task(Final)` |
//! | `ListTasks` | `list_tasks` |
//!
//! | Group REST action | Use case calls |
//! | --- | --- |
//! | `message:send` / `message:stream` | `group_send`, `get_task` / `subscribe` |
//! | `GET tasks`, `GET tasks/{id}` | `group_list_tasks`, `get_task` |
//! | `tasks/{id}:subscribe` | `subscribe` |
//! | `tasks/{id}:cancel` | `cancel`, then `group_wait_task(Final)` |
//!
//! Push notifications are not supported on either binding (agent cards and
//! `-32003`).

mod card;
mod content;
pub mod cursor;
mod error;
mod group_rest;
mod mapping;
mod rpc;
mod stream;

use axum::Router;
use axum::extract::DefaultBodyLimit;
use axum::routing::{get, post};
use bcs_dist_model::PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES;
use bcs_service_api::application::dist_gateway::{DistGatewayService, GroupDiscoveryService};
use std::sync::Arc;
use std::time::Duration;

pub use bcs_service_api::application::dist_gateway::GatewayCaller;
pub use group_rest::GROUPS_PREFIX;

#[derive(Debug, Clone)]
pub struct GatewayHttpConfig {
    /// Public base URL of this Gateway, advertised in agent cards.
    pub public_url: String,
    /// Longest a blocking `SendMessage` waits before returning the current task.
    pub blocking_send_wait: Duration,
    /// Longest `CancelTask` waits for a final state.
    pub cancel_wait: Duration,
    /// Interval of SSE keep-alive comments while a stream is idle.
    pub keep_alive: Duration,
    /// Largest accepted request body of the legacy JSON-RPC binding.
    pub max_message_bytes: usize,
    /// Page size of `ListTasks` when the request names none.
    pub default_page_size: u32,
}

impl GatewayHttpConfig {
    pub fn new(public_url: impl Into<String>) -> Self {
        Self {
            public_url: public_url.into(),
            blocking_send_wait: Duration::from_secs(60),
            cancel_wait: Duration::from_secs(4),
            keep_alive: Duration::from_secs(15),
            max_message_bytes: 256 * 1024,
            default_page_size: 20,
        }
    }
}

#[derive(Clone)]
pub(crate) struct AppState {
    pub gateway: Arc<dyn DistGatewayService>,
    pub discovery: Arc<dyn GroupDiscoveryService>,
    pub config: Arc<GatewayHttpConfig>,
}

/// Routes of the Gateway:
///
/// - `POST /a2a/{group}`: the legacy A2A JSON-RPC binding (`A2A-Version: 1.0`);
/// - `GET /a2a/{group}/.well-known/agent-card.json`: the JSON-RPC binding's card;
/// - `/api/v1/collaboration/a2a/groups/{group_id}`: the REST/SSE surface of
///   published FSM groups — `message:send`, `message:stream`, `GET tasks`,
///   `tasks/{id}` / `tasks/{id}:subscribe` / `tasks/{id}:cancel` and its
///   `agent-card.json`, with the public frame budget as its body limit.
///
/// The caller is read from a [`GatewayCaller`] request extension, set by the
/// authentication layer in front of this router; a request without one is
/// refused with HTTP 401.
pub fn router(
    gateway: Arc<dyn DistGatewayService>,
    discovery: Arc<dyn GroupDiscoveryService>,
    config: GatewayHttpConfig,
) -> Router {
    let limit = config.max_message_bytes;
    let state = AppState {
        gateway,
        discovery,
        config: Arc::new(config),
    };
    Router::new()
        .route("/a2a/{group}", post(rpc::handle))
        .route("/a2a/{group}/.well-known/agent-card.json", get(card::handle))
        .route(
            &format!("{}/{{group}}/agent-card.json", GROUPS_PREFIX),
            get(group_rest::card_get),
        )
        .route(
            &format!("{}/{{group}}/{{action}}", GROUPS_PREFIX),
            post(group_rest::message_post).layer(DefaultBodyLimit::max(
                // The public frame budget is the body budget of the group
                // surface, independent of the legacy binding's limit.
                PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES,
            )),
        )
        .route(
            &format!("{}/{{group}}/tasks", GROUPS_PREFIX),
            get(group_rest::tasks_list),
        )
        .route(
            &format!("{}/{{group}}/tasks/{{action}}", GROUPS_PREFIX),
            get(group_rest::task_action).post(group_rest::task_action),
        )
        .layer(DefaultBodyLimit::max(limit))
        .with_state(state)
}
