use axum::{extract::State, Json};

use crate::state::HttpAppState;

pub async fn health(State(state): State<HttpAppState>) -> Json<serde_json::Value> {
    // Local binding kept for readability/debuggability of the health probe value.
    let health_state = state.health.health().await;
    Json(health_state)
}
