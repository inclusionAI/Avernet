pub(crate) mod dto;
mod routes;
mod session_file_url;

use axum::Router;

use super::common::ApiState;

pub use session_file_url::SessionFileUrlProjector;
pub use dto::session_file::{
    ListSessionFilesQuery, PrepareSessionFileRequest, ProtectedFileContentQuery,
    ShareSessionFileRequest, SharedFileContentQuery, UploadSessionFileQuery,
};

pub fn protected_router() -> Router<ApiState> {
    Router::new().nest(
        "/openapi/v1/collaboration",
        routes::bot::router()
            .merge(routes::event_subscription::router())
            .merge(routes::group::router())
            .merge(routes::session::router())
            .merge(routes::invitation::router())
            .merge(routes::friendship::router())
            .merge(routes::channel::router()),
    )
}

pub fn public_router() -> Router<ApiState> {
    Router::new()
}
