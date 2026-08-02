mod dto;
mod routes;

use axum::Router;

use super::common::ApiState;

pub fn router() -> Router<ApiState> {
    routes::bot::router()
        .merge(routes::group::router())
        .merge(routes::session::router())
        .merge(routes::invitation::router())
        .merge(routes::friendship::router())
}
