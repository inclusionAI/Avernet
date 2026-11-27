//! Inbound A2A 1.0 delivery adapter of the distributed Gateway: JSON-RPC over
//! HTTP, with Server-Sent Events for the streaming methods.
//!
//! The adapter owns the A2A wire types and calls only the application service
//! `DistGatewayService`. It does not authenticate: the layer in front of the
//! router puts the resolved [`GatewayCaller`] into the request extensions.
//!
//! | A2A method | Use case calls |
//! | --- | --- |
//! | `SendMessage` | `submit`, then `get_task` (return immediately) or `wait_task` |
//! | `SendStreamingMessage` | `submit`, then `subscribe(Snapshot)` |
//! | `GetTask` | `get_task` |
//! | `SubscribeToTask` | `subscribe(Resume)` with a usable `Last-Event-ID`, else `subscribe(Snapshot)` |
//! | `CancelTask` | `cancel`, then `wait_task(Final)` |
//! | `ListTasks` | `list_tasks` |
//!
//! Push notifications are not supported (agent card and `-32003`).

mod card;
mod content;
pub mod cursor;
mod error;
mod mapping;
mod request;
mod rpc;
mod stream;

use axum::Router;
use axum::extract::DefaultBodyLimit;
use axum::routing::{get, post};
use bcs_service_api::application::dist_gateway::DistGatewayService;
use std::sync::Arc;
use std::time::Duration;

pub use bcs_service_api::application::dist_gateway::GatewayCaller;

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
    /// Largest accepted request body.
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
    pub direct: bool,
    pub config: Arc<GatewayHttpConfig>,
}

/// Routes of the Gateway:
///
/// - `POST /a2a/{group}`: A2A JSON-RPC (`A2A-Version: 1.0`);
/// - `GET /a2a/{group}/.well-known/agent-card.json`: the group's agent card.
///
/// Direct agents expose the same methods and card under `/a2a/agents/{target}`.
///
/// The caller is read from a [`GatewayCaller`] request extension, set by the
/// authentication layer in front of this router; a JSON-RPC request without
/// one is refused with HTTP 401.
pub fn router(gateway: Arc<dyn DistGatewayService>, config: GatewayHttpConfig) -> Router {
    let limit = config.max_message_bytes;
    let state = AppState {
        gateway,
        config: Arc::new(config),
        direct: false,
    };
    let direct = Router::new()
        .route("/a2a/agents/{target}", post(rpc::handle))
        .route("/a2a/agents/{target}/.well-known/agent-card.json", get(card::handle))
        .with_state(AppState { direct: true, ..state.clone() });
    Router::new()
        .route("/a2a/{group}", post(rpc::handle))
        .route("/a2a/{group}/.well-known/agent-card.json", get(card::handle))
        .with_state(state)
        .merge(direct)
        .layer(DefaultBodyLimit::max(limit))
}
