use axum::{extract::State, Json};

use crate::state::HttpAppState;

pub async fn health(State(state): State<HttpAppState>) -> Json<serde_json::Value> {
    Json(state.health.health().await)
}
