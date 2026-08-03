pub mod auth;
pub mod connection_registry;
pub mod dispatcher;
pub mod frontend_delivery;
pub mod handler;

pub const FRONTEND_WS_ENDPOINT: &str = "/ws";

pub use auth::WorkbenchConnectionAuth;
pub use connection_registry::WorkbenchConnectionRegistry;
pub use dispatcher::{
    WebClientConnectionState, WebConnectionPhase, WebDispatchOutcome, WebDispatchState,
    WebWsDispatchError, dispatch_client_frame,
};
pub use frontend_delivery::WorkbenchFrontendDelivery;
pub use handler::handle_client_connection;
