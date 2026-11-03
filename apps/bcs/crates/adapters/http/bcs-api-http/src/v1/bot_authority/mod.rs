//! The unified authority route module (spec §5.1/§6.1).

mod dto;
mod routes;

use axum::Router;

use crate::v1::common::ApiState;

/// The protected authority router, mounted at
/// `/api/v1/collaboration` from [`crate::v1::router`].
///
/// It deliberately receives ONLY the signature-verification
/// layer — not the always-Human invite gate. Human admission
/// (including invite-code checks) runs in the application so
/// a trusted App is never treated as an un-invite-coded
/// Human, and the ordinary existing routes keep their full
/// middleware unchanged.
pub(crate) fn protected_router() -> Router<ApiState> {
    Router::new().nest("/api/v1/collaboration", routes::router())
}
