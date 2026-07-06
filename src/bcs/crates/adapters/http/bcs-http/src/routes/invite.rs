use axum::{
    Json,
    extract::{Path, State},
    http::{HeaderMap, Uri},
    response::{IntoResponse, Response},
};
use bcs_service_api::{
    CreateInviteTokenCommand, InviteService, InviteUseCaseError, JoinByInviteCommand,
};
use serde::Deserialize;

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

#[derive(Deserialize, Default)]
pub struct InviteLinkBody {
    #[serde(default)]
    pub ttl_seconds: Option<u64>,
}

fn build_invite_service(
    state: &HttpAppState,
    headers: &HeaderMap,
) -> bcs_group::application::invite::InviteServiceImpl {
    let host = state.invite_base_url.clone().or_else(|| {
        headers
            .get(axum::http::header::HOST)
            .and_then(|v| v.to_str().ok())
            .map(|h| format!("https://{}", h))
    });
    bcs_group::application::invite::InviteServiceImpl {
        registry: state.services.registry.clone(),
        group: state.services.group.clone(),
        session: state.services.session_management.clone(),
        system_message: state.services.system_message.clone(),
        token_secret: state.invite_token_secret.clone(),
        default_ttl_seconds: state.invite_default_ttl_seconds,
        base_url: host,
        group_link_url: state.invite_group_link_url.clone(),
        session_link_url: state.invite_session_link_url.clone(),
    }
}

async fn resolve_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> (Option<String>, Option<String>) {
    let bot_id = super::bot_id_from_headers(state, headers).await;
    if bot_id.is_some() {
        return (bot_id, None);
    }
    let user = state.user_identity.extract(headers, uri).await;
    let staff_no = user.as_ref().and_then(|u| u.staff_no.clone());
    let human_id = staff_no.as_deref().map(|s| format!("human_{}", s));
    (human_id, staff_no)
}

pub async fn create_group_invite_link(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(group_id): Path<String>,
    body: Option<Json<InviteLinkBody>>,
) -> Response {
    let (caller_actor_id, caller_staff_no) = resolve_caller(&state, &headers, &uri).await;
    let body = body.map(|b| b.0).unwrap_or_default();
    let svc = build_invite_service(&state, &headers);
    let cmd = CreateInviteTokenCommand {
        caller_actor_id,
        caller_staff_no,
        target_id: group_id,
        ttl_seconds: body.ttl_seconds,
    };
    match svc.create_group_invite_token(cmd).await {
        Ok(result) => Json(result).into_response(),
        Err(e) => HttpAdapterError::from(e).into_response(),
    }
}

pub async fn create_session_invite_link(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(session_id): Path<String>,
    body: Option<Json<InviteLinkBody>>,
) -> Response {
    let (caller_actor_id, caller_staff_no) = resolve_caller(&state, &headers, &uri).await;
    let body = body.map(|b| b.0).unwrap_or_default();
    let svc = build_invite_service(&state, &headers);
    let cmd = CreateInviteTokenCommand {
        caller_actor_id,
        caller_staff_no,
        target_id: session_id,
        ttl_seconds: body.ttl_seconds,
    };
    match svc.create_session_invite_token(cmd).await {
        Ok(result) => Json(result).into_response(),
        Err(e) => HttpAdapterError::from(e).into_response(),
    }
}

pub async fn join_group_by_invite(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(token): Path<String>,
) -> Response {
    let user = state.user_identity.extract(&headers, &uri).await;
    let staff_no = match user.as_ref().and_then(|u| u.staff_no.as_deref()) {
        Some(s) => s.to_string(),
        None => return HttpAdapterError::Unauthorized("login required".into()).into_response(),
    };
    let nick_name = user
        .as_ref()
        .and_then(|u| u.nick_name.as_deref())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);

    let svc = build_invite_service(&state, &headers);
    let cmd = JoinByInviteCommand { token, staff_no, nick_name };
    match svc.join_group_by_invite(cmd).await {
        Ok(result) => Json(result).into_response(),
        Err(e) => HttpAdapterError::from(e).into_response(),
    }
}

pub async fn join_session_by_invite(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(token): Path<String>,
) -> Response {
    let user = state.user_identity.extract(&headers, &uri).await;
    let staff_no = match user.as_ref().and_then(|u| u.staff_no.as_deref()) {
        Some(s) => s.to_string(),
        None => return HttpAdapterError::Unauthorized("login required".into()).into_response(),
    };
    let nick_name = user
        .as_ref()
        .and_then(|u| u.nick_name.as_deref())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);

    let svc = build_invite_service(&state, &headers);
    let cmd = JoinByInviteCommand { token, staff_no, nick_name };
    match svc.join_session_by_invite(cmd).await {
        Ok(result) => Json(result).into_response(),
        Err(e) => HttpAdapterError::from(e).into_response(),
    }
}

impl From<InviteUseCaseError> for HttpAdapterError {
    fn from(e: InviteUseCaseError) -> Self {
        match e {
            InviteUseCaseError::InvalidToken(msg) => {
                HttpAdapterError::Unauthorized(msg)
            }
            InviteUseCaseError::Expired => {
                HttpAdapterError::Gone("invite link has expired".into())
            }
            InviteUseCaseError::LoginRequired => {
                HttpAdapterError::Unauthorized("login required".into())
            }
            InviteUseCaseError::Forbidden(msg) => {
                HttpAdapterError::Forbidden(msg)
            }
            InviteUseCaseError::NotFound(msg) => {
                HttpAdapterError::NotFound(msg)
            }
            InviteUseCaseError::Conflict(msg) => {
                HttpAdapterError::Conflict(msg)
            }
            InviteUseCaseError::Service(e) => {
                HttpAdapterError::Service(e)
            }
        }
    }
}
