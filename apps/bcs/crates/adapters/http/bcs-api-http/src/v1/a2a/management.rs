use super::{
    service,
    transport::{bearer, body, protocol_error, reply},
};
use crate::v1::common::{ApiState, RequestId};
use axum::{
    extract::{Extension, Path, Request, State},
    http::StatusCode,
    response::Response,
};
use bcs_service_api::application::v1::{
    ApplicationError,
    a2a::{
        A2aCardInput, A2aRegistrationVisibility, RegisterA2aAgent, RegisterNamedA2aAgent,
        UpdateA2aAgent,
    },
};
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
pub struct Target {
    bot_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Registration {
    provider_bot_ref: String,
    #[serde(default)]
    visibility: A2aRegistrationVisibility,
    owners: Option<Vec<String>>,
    summary: Option<String>,
    agent_card_url: Option<String>,
    agent_card: Option<Value>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Update {
    visibility: Option<A2aRegistrationVisibility>,
    agent_card_url: Option<String>,
    agent_card: Option<Value>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceQuery {
    provider_bot_ref: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Refresh {}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NamedRegistration {
    provider_bot_ref: String,
    owners: Vec<String>,
    summary: Option<String>,
    agent_card_url: Option<String>,
    agent_card: Option<Value>,
}

async fn execute(
    state: &ApiState,
    request: Request,
    bot_id: Option<String>,
) -> Result<(StatusCode, Value), ApplicationError> {
    let (parts, request_body) = request.into_parts();
    let token = bearer(&parts.headers)?;
    let service = service(state)?;
    let bytes = axum::body::to_bytes(request_body, 2 * 1024 * 1024)
        .await
        .map_err(|_| {
            ApplicationError::payload_too_large("request_too_large", "Request body exceeds limit")
        })?;
    if parts.method == axum::http::Method::GET {
        if !bytes.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "GET body is not allowed",
            ));
        }
        if let Some(bot_id) = bot_id.as_deref() {
            if parts.uri.query().is_some() {
                return Err(ApplicationError::invalid(
                    "invalid_request",
                    "Query is not allowed",
                ));
            }
            return Ok((StatusCode::OK, json!(service.get(&token, bot_id).await?)));
        }
        let axum::extract::Query(query) =
            axum::extract::Query::<SourceQuery>::try_from_uri(&parts.uri).map_err(|_| {
                ApplicationError::invalid("invalid_request", "provider_bot_ref is required")
            })?;
        return Ok((
            StatusCode::OK,
            json!({"items":service.find(&token, &query.provider_bot_ref).await?}),
        ));
    }
    if parts.uri.query().is_some() {
        return Err(ApplicationError::invalid(
            "invalid_request",
            "Query is not allowed",
        ));
    }
    if let Some(bot_id) = bot_id {
        if parts.method == axum::http::Method::PUT {
            let input: Update = body(&parts.headers, &bytes)?;
            let result = service
                .update(UpdateA2aAgent {
                    token,
                    bot_id,
                    visibility: input.visibility,
                    source: A2aCardInput {
                        agent_card_url: input.agent_card_url,
                        agent_card: input.agent_card,
                    },
                })
                .await?;
            return Ok((StatusCode::OK, json!(result)));
        }
        if !bytes.is_empty() {
            let _: Refresh = body(&parts.headers, &bytes)?;
        }
        return Ok((
            StatusCode::OK,
            json!(service.refresh(&token, &bot_id).await?),
        ));
    }
    let input: Registration = body(&parts.headers, &bytes)?;
    let result = service
        .register(RegisterA2aAgent {
            token,
            provider_bot_ref: input.provider_bot_ref,
            owners: input.owners,
            summary: input.summary,
            visibility: input.visibility,
            source: A2aCardInput {
                agent_card_url: input.agent_card_url,
                agent_card: input.agent_card,
            },
        })
        .await?;
    Ok((StatusCode::CREATED, json!(result)))
}

pub async fn handle(
    State(state): State<ApiState>,
    Extension(id): Extension<RequestId>,
    target: Result<Path<Target>, axum::extract::rejection::PathRejection>,
    request: Request,
) -> Response {
    let path = request.uri().path().to_owned();
    let collection = path == "/api/v1/collaboration/a2a/agents";
    let target = if collection {
        Ok(None)
    } else {
        target.map(|Path(v)| Some(v.bot_id))
    };
    let allowed = if collection {
        "GET, POST"
    } else if path.ends_with("/agent-card:refresh") {
        "POST"
    } else {
        "GET, PUT"
    };
    if !allowed.split(", ").any(|v| v == request.method().as_str()) {
        let mut response =
            protocol_error(&id, StatusCode::METHOD_NOT_ALLOWED, "method_not_allowed");
        response
            .headers_mut()
            .insert("allow", axum::http::HeaderValue::from_static(allowed));
        return response;
    }
    let target = match target {
        Ok(target) => target,
        Err(_) => {
            return reply(
                &id,
                StatusCode::OK,
                Err(ApplicationError::invalid(
                    "invalid_request",
                    "Invalid Bot path",
                )),
            );
        }
    };
    match execute(&state, request, target).await {
        Ok((status, value)) => {
            let location = (status == StatusCode::CREATED)
                .then(|| {
                    value
                        .get("bot_id")
                        .and_then(Value::as_str)
                        .map(|id| format!("{path}/{}/agent-card", urlencoding::encode(id)))
                })
                .flatten();
            let mut response = reply(&id, status, Ok(value));
            if let Some(location) = location {
                if let Ok(value) = location.parse() {
                    response.headers_mut().insert("location", value);
                }
            }
            response
        }
        Err(error) => reply(&id, StatusCode::OK, Err(error)),
    }
}

pub async fn register_named(
    State(state): State<ApiState>,
    Extension(id): Extension<RequestId>,
    Path(provider_name): Path<String>,
    request: Request,
) -> Response {
    let result = async {
        let (parts, request_body) = request.into_parts();
        let token = bearer(&parts.headers)?;
        let bytes = axum::body::to_bytes(request_body, 2 * 1024 * 1024).await
            .map_err(|_|ApplicationError::payload_too_large("request_too_large", "Request body exceeds limit"))?;
        if parts.uri.query().is_some() { return Err(ApplicationError::invalid("invalid_request", "Query is not allowed")); }
        let input: NamedRegistration = body(&parts.headers, &bytes)?;
        let result = service(&state)?.register_named(RegisterNamedA2aAgent { token, provider_name,
            provider_bot_ref: input.provider_bot_ref, owners: input.owners, summary: input.summary,
            source: A2aCardInput { agent_card_url: input.agent_card_url, agent_card: input.agent_card } }).await?;
        let card = result.card;
        let mut value = json!({"provider_bot_ref":card.provider_bot_ref,"bot_id":card.bot_id,"provider_name":card.provider_name,
            "bot_name":card.bot_name,"agent_card_url":card.agent_card_url,"agent_card":card.agent_card});
        if let Some(token) = result.bot_runtime_token { value["bot_runtime_token"]=json!(token); }
        Ok(value)
    }.await;
    reply(&id, StatusCode::CREATED, result)
}
