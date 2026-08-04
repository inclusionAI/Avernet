pub mod auth;
pub mod connection_registry;
pub mod dispatcher;
pub mod frontend_delivery;
pub mod group_session;
pub mod handler;

pub const FRONTEND_WS_ENDPOINT: &str = "/ws";

pub use auth::WorkbenchConnectionAuth;
pub use connection_registry::WorkbenchConnectionRegistry;
pub use dispatcher::{
    WebClientConnectionState, WebConnectionPhase, WebDispatchOutcome, WebDispatchState,
    WebWsDispatchError, dispatch_client_frame,
};
pub use frontend_delivery::WorkbenchFrontendDelivery;
pub use group_session::{GROUP_SESSION_WS_ENDPOINT, group_session_websocket_router};
pub use handler::handle_client_connection;
