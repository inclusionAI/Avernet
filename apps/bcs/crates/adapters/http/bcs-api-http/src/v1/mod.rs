pub mod bot_authority;
pub mod common;
pub mod gateway_principal;
pub mod group_session_connection;
pub mod internal;
pub mod openapi;
pub mod collaboration_drafts;
pub mod human;

use axum::Router;
use axum::middleware;

use common::{ApiState, enforce_invite_code_gate, verify_principal};

pub use group_session_connection::group_session_connection_router;

/// Build the v1 router with an injected Principal verification boundary.
///
/// Public and internal collaboration routes that need caller verification share
/// the same protected boundary.
pub fn router(state: ApiState) -> Router {
    let drafts = collaboration_drafts::router(state.collaboration_drafts.clone());
    let protected = Router::new()
        .merge(openapi::protected_router())
        .merge(internal::protected_router())
        .route("/openapi/v1/collaboration/me", axum::routing::get(human::current_user))
        .layer(middleware::from_fn_with_state(
            state.clone(),
            enforce_invite_code_gate::<ApiState>,
        ))
        .layer(middleware::from_fn_with_state(
            state.clone(),
            verify_principal::<ApiState>,
        ));
    // The unified authority surface (spec §5.1/§6.1) shares
    // ONLY the signature verification boundary — it is never
    // anonymous and never URL-whitelisted. It does NOT take
    // the always-Human invite gate: Human admission (with the
    // invite-code check) runs in the application per call
    // (spec §6.1), while the ordinary routes above keep the
    // gate unchanged.
    let authority_protected = Router::new()
        .merge(bot_authority::protected_router())
        .layer(middleware::from_fn_with_state(
            state.clone(),
            verify_principal::<ApiState>,
        ));
    Router::new()
        .merge(protected)
        .merge(authority_protected)
        .merge(openapi::public_router())
        .merge(internal::public_router())
        .with_state(state)
        .merge(drafts)
}
