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
/// Public and internal collaboration routes that need caller verification share
/// the same protected boundary.
pub fn router(state: ApiState) -> Router {
    let protected = Router::new()
        .merge(openapi::protected_router())
        .merge(internal::protected_router())
        .layer(middleware::from_fn_with_state(
            state.clone(),
            verify_principal::<ApiState>,
        ));
    Router::new()
        .merge(protected)
        .merge(openapi::public_router())
        .merge(internal::public_router())
        .with_state(state)
}
