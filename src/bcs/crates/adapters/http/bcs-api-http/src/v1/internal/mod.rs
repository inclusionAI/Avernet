#[path = "routes/mod.rs"]
mod routes;

use axum::Router;

use super::common::ApiState;

pub fn protected_router() -> Router<ApiState> {
    Router::new().nest(
        "/api/v1/collaboration",
        routes::bot::router().merge(routes::session_file::protected_router()),
    )
}

pub fn public_router() -> Router<ApiState> {
    Router::new().nest(
        "/api/v1/collaboration",
        routes::session_file::public_router(),
    )
}

pub fn router() -> Router<ApiState> {
    protected_router().merge(public_router())
}
