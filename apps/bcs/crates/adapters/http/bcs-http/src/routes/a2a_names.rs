//! Provider-name registration and named chat; Card management uses stable Bot IDs.
use axum::{Json,extract::{Path,Query,State},http::{HeaderMap,StatusCode},response::{IntoResponse,Response}};
use serde::Deserialize;
use serde_json::{json,Value};
use bcs_service_api::{ServiceError,application::a2a_bot::{NamedA2aCard,RegisterNamedA2aBotCommand},types::a2a_bot::A2aCardSource};
use crate::state::HttpAppState;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Registration {
    #[serde(default,deserialize_with="bcs_service_api::types::catalog_labels::present_input")]
    pub catalog_labels:Option<bcs_service_api::types::catalog_labels::CatalogLabelsInput>,
    pub provider_bot_ref:String,
    pub owners:Vec<String>,pub summary:Option<String>,pub agent_card_url:Option<String>,pub agent_card:Option<Value>,
}
fn error(error:ServiceError)->Response {
    let (status,message)=match error {
        ServiceError::A2aManagement(error)=>(super::a2a_management::status(error.code),error.reason),
        ServiceError::Unauthorized(_)=>(StatusCode::UNAUTHORIZED,"Provider authentication required"),
        ServiceError::Forbidden(_)=>(StatusCode::FORBIDDEN,"Provider access denied"),
        ServiceError::ProviderNotFound(_)|ServiceError::BotNotFound(_)|ServiceError::BotNotRegistered(_)=>(StatusCode::NOT_FOUND,"Provider or Bot not found"),
        ServiceError::Conflict(_)=>(StatusCode::CONFLICT,"Bot name in use or configuration changed"),
        ServiceError::InvalidOperation {..}=>(StatusCode::BAD_REQUEST,"Invalid A2A registration or configuration"),
        _=>(StatusCode::INTERNAL_SERVER_ERROR,"A2A management failed"),
    };
    (status,Json(json!({"error":message}))).into_response()
}
fn token(headers:&HeaderMap)->Result<String,Response> {
    crate::headers::extract_bearer_token(headers).ok_or_else(||error(ServiceError::Unauthorized(String::new())))
}
fn source(url:Option<String>,card:Option<Value>)->Result<A2aCardSource,Response> {
    A2aCardSource::from_parts(url,card).map_err(error)?.ok_or_else(||error(ServiceError::InvalidOperation {message:"Card source required".into(),request_id:None}))
}
fn view(card:NamedA2aCard)->Value {
    json!({"provider_bot_ref":card.provider_bot_ref,"bot_id":card.bot_id,"provider_name":card.provider_name,"bot_name":card.bot_name,"catalog_labels":card.catalog_labels,"agent_card_url":card.agent_card_url,"agent_card":card.agent_card})
}
fn now_ms()->i64 {std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|value|value.as_millis().min(i64::MAX as u128) as i64).unwrap_or(0)}
fn unavailable()->Response {(StatusCode::SERVICE_UNAVAILABLE,Json(json!({"error":"A2A management unavailable"}))).into_response()}

pub async fn register(State(state):State<HttpAppState>,Path(provider_name):Path<String>,headers:HeaderMap,Json(body):Json<Registration>)->Response {
    let token=match token(&headers) {Ok(token)=>token,Err(error)=>return error};
    let Some(service)=&state.services.a2a_bot_management else {return unavailable()};
    let source=match source(body.agent_card_url,body.agent_card) {Ok(source)=>source,Err(error)=>return error};
    match service.register_named(RegisterNamedA2aBotCommand {catalog_labels:body.catalog_labels.unwrap_or_default(),provider_bot_ref:body.provider_bot_ref,provider_name,provider_admin_token:token,owners:body.owners,summary:body.summary,source,now_ms:now_ms()}).await {
        Ok(result)=>{let mut value=view(result.card);if let Some(token)=result.bot_runtime_token {value["bot_runtime_token"]=json!(token);}(StatusCode::CREATED,Json(value)).into_response()},
        Err(failure)=>error(failure),
    }
}
#[derive(Deserialize)]
pub struct NamedChatRequest {
    test_user_ref: Option<String>,
    #[serde(flatten)]
    chat: super::bot_chat::ChatRequest,
}

fn chat_error(status: StatusCode, message: &'static str) -> Response {
    (status, Json(json!({"error": message}))).into_response()
}

/// Keep the established caller authentication and chat admission checks intact.
pub async fn chat(State(state):State<HttpAppState>,Path((provider_name,provider_bot_ref)):Path<(String,String)>,
    headers:HeaderMap,uri:axum::http::Uri,Json(body):Json<NamedChatRequest>)->Response {
    let Some(service)=&state.services.a2a_bot_management else {return unavailable()};
    let caller = match super::bot_chat::resolve_bot_caller(&state, &headers).await {
        Ok(caller) => caller,
        Err(failure) => return chat_error(failure.into_response().status(), "Caller authentication required"),
    };
    let admission = if let Some(alias) = body.test_user_ref.as_deref() {
        let Some(admission) = &state.a2a_test_user_admission else {
            return chat_error(StatusCode::FORBIDDEN, "A2A test user selection unavailable");
        };
        if admission.selection.caller_bot_id != caller {
            return chat_error(StatusCode::FORBIDDEN, "A2A test user selection denied");
        }
        if !bcs_config_api::a2a_test_user::valid_ref(alias) || alias != admission.selection.alias
            || body.chat.session_id.is_some()
        {
            return chat_error(StatusCode::BAD_REQUEST, "Invalid A2A test user selection");
        }
        Some(admission)
    } else {
        None
    };
    let target=match service.resolve_provider_ref_target(&provider_name,&provider_bot_ref).await {
        Ok(target)=>target,Err(failure)=>return error(failure),
    };
    if admission.is_some_and(|admission| admission.target_bot_id != target) {
        return chat_error(StatusCode::FORBIDDEN, "A2A test user selection denied");
    }
    let selection = admission.map(|admission| admission.selection.clone());
    match super::bot_chat::submit_async_chat(state, target, headers, uri, body.chat, selection).await {
        Ok((status,Json(result)))=>(status,Json(json!({"provider_name":provider_name,"provider_bot_ref":provider_bot_ref,
            "run_id":result["run_id"],"session_id":result["session_id"],"status":result["status"],
            "expires_at_ms":result["expires_at_ms"]}))).into_response(),
        Err(failure)=>{
            let status=failure.into_response().status();
            (status,Json(json!({"error":"Chat admission failed","status":status.as_u16()}))).into_response()
        }
    }
}

fn run_view(value:Value)->Value {
    let mut result=serde_json::Map::new();
    for key in ["run_id","session_id","state","response","created_at_ms","updated_at_ms",
        "completed_at_ms","expires_at_ms","version","content_truncated","is_terminal","cancelled"] {
        if let Some(value)=value.get(key) {result.insert(key.into(),value.clone());}
    }
    if value.get("error_message").is_some_and(|value|!value.is_null()) {
        result.insert("error_message".into(),json!("Run failed; provide run_id to the platform operator"));
    }
    Value::Object(result)
}

pub async fn get_run(State(state):State<HttpAppState>,Path(run_id):Path<String>,
    Query(query):Query<super::messages::GetChatRunQuery>,headers:HeaderMap)->Response {
    match super::messages::get_chat_run(State(state),Path(run_id),Query(query),headers).await {
        Ok(Json(value))=>Json(run_view(value)).into_response(),
        Err(failure)=>(failure.into_response().status(),Json(json!({"error":"Run query failed"}))).into_response(),
    }
}

pub async fn cancel_run(State(state):State<HttpAppState>,Path(run_id):Path<String>,headers:HeaderMap)->Response {
    match super::messages::cancel_chat_run(State(state),Path(run_id),headers).await {
        Ok(Json(value))=>Json(run_view(value)).into_response(),
        Err(failure)=>(failure.into_response().status(),Json(json!({"error":"Run cancellation failed"}))).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn run_projection_excludes_internal_identities_and_diagnostics() {
        let source=json!({"run_id":"run-1","state":"failed","response":{"content":"answer"},
            "bot_uuid":"private-bot","from_bot_id":"private-caller","provider_id":"private-provider",
            "delivery":{"target":"private-bot"},"error_message":"private-provider failed", "version":3});
        let projected=run_view(source);
        assert_eq!(projected["run_id"],"run-1");assert_eq!(projected["version"],3);
        assert_eq!(projected["response"]["content"],"answer");
        assert!(!projected.to_string().contains("private-"));
    }
}
