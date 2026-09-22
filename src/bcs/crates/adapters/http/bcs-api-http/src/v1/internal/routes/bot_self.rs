use axum::{Json, Router, extract::State, http::{HeaderMap, HeaderValue}, response::{IntoResponse, Response}, routing::get};
use bcs_service_api::application::v1::{ApplicationError, BotRegistrationStatus, BotSelfView};
use serde::Serialize;

use crate::v1::common::{ApiState, Envelope, RequestId, application_error_response};

pub fn router() -> Router<ApiState> {
    Router::new().route("/bots/me", get(get_me))
}

#[derive(Serialize)]
struct AgentIdentityDto {
    agent_code: String,
}

#[derive(Serialize)]
struct RegisteredBotDto {
    bot_id: String,
    name: Option<String>,
    summary: Option<String>,
    provider_id: Option<String>,
    provider_bot_ref: Option<String>,
}

#[derive(Serialize)]
struct BotSelfDto {
    registration_status: &'static str,
    identity: AgentIdentityDto,
    bot: Option<RegisteredBotDto>,
}

impl From<BotSelfView> for BotSelfDto {
    fn from(view: BotSelfView) -> Self {
        Self {
            registration_status: match view.registration_status {
                BotRegistrationStatus::Registered => "registered",
                BotRegistrationStatus::Unregistered => "unregistered",
            },
            identity: AgentIdentityDto { agent_code: view.agent_code },
            bot: view.bot.map(|bot| RegisteredBotDto {
                bot_id: bot.bot_id, name: bot.name, summary: bot.summary,
                provider_id: bot.provider_id, provider_bot_ref: bot.provider_bot_ref,
            }),
        }
    }
}

fn bearer(headers: &HeaderMap) -> Result<&str, ApplicationError> {
    let mut values = headers.get_all(axum::http::header::AUTHORIZATION).iter();
    let value = values.next().and_then(|v| v.to_str().ok())
        .ok_or(ApplicationError::Unauthenticated)?;
    if values.next().is_some() {
        return Err(ApplicationError::Unauthenticated);
    }
    let (scheme, token) = value.split_once(' ').ok_or(ApplicationError::Unauthenticated)?;
    if !scheme.eq_ignore_ascii_case("Bearer") || token.is_empty()
        || token.bytes().any(|b| b.is_ascii_whitespace() || b == b',')
    {
        return Err(ApplicationError::Unauthenticated);
    }
    Ok(token)
}

async fn get_me(State(state): State<ApiState>, headers: HeaderMap) -> Response {
    let request_id = RequestId::from_headers(&headers);
    let result = async {
        let token = bearer(&headers)?;
        let service = state.bot_self_service.as_ref()
            .ok_or_else(|| ApplicationError::internal("Bot self service is not configured"))?;
        service.get_me(token).await
    }.await;
    let mut response = match result {
        Ok(view) => Json(Envelope::success(20_000, "OK", BotSelfDto::from(view), request_id.0)).into_response(),
        Err(error) => application_error_response(&request_id, error).into_response(),
    };
    response.headers_mut().insert(axum::http::header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}
