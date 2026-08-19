mod auth;
#[path = "routes.rs"]
mod bot_attributes_routes;
mod dto;
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

pub use auth::{InternalProviderAuthError, InternalProviderAuthenticator};

pub fn bot_attributes_router(
    service: std::sync::Arc<dyn bcs_service_api::application::v1::InternalBotAttributesService>,
    authenticator: std::sync::Arc<dyn InternalProviderAuthenticator>,
) -> Router {
    bot_attributes_routes::router(service, authenticator)
}
