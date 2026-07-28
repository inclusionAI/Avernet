use axum::Router;

use super::common::ApiState;

/// No Internal API business routes are included in the first batch.
pub fn router() -> Router<ApiState> {
    Router::new()
}
