pub mod common;
pub mod gateway_principal;
pub mod group_session_connection;
pub mod internal;
pub mod openapi;

use axum::Router;
use axum::middleware;

use common::{ApiState, verify_principal};

pub use group_session_connection::group_session_connection_router;

/// Build the v1 router with an injected Principal verification boundary.
///
/// The Internal API deliberately has no business routes in the first batch.
pub fn router(state: ApiState) -> Router {
    Router::new()
        .merge(openapi::router())
        .merge(internal::router())
        .layer(middleware::from_fn_with_state(
            state.clone(),
            verify_principal::<ApiState>,
        ))
        .with_state(state)
}
