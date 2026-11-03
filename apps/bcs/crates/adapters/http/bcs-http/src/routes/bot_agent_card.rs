//! Reads the registered source Card using the same application authorization as
//! Bot details. This is a registry read, not a public A2A Server AgentCard.
use axum::{Json, extract::{Path, State}, http::{HeaderMap, Uri}};
use bcs_service_api::BotDetailCommand;
use serde_json::Value;
use crate::{error::HttpAdapterError, state::HttpAppState};

pub async fn get(
    State(state): State<HttpAppState>, Path(bot_id): Path<String>, headers: HeaderMap, uri: Uri,
) -> Result<Json<Value>, HttpAdapterError> {
    let caller = super::require_caller_actor_id_from_headers(&state, &headers, &uri).await?;
    let detail = state.services.bot_query.get_bot(BotDetailCommand {
        caller_actor_id: Some(caller), bot_id,
    }).await.map_err(super::bots::bot_use_case_error_to_http)?;
    detail.agent_card.map(Json).ok_or_else(||HttpAdapterError::NotFound("AgentCard is not configured".into()))
}
