use axum::{
    Json,
    extract::{Path, State},
    http::HeaderMap,
    response::{Html, IntoResponse, Response},
};
use bcs_protocol as wire;
use bcs_service_api::{
    GroupChatProposal, GroupProposalConfirmCommand, GroupProposalConfirmResult,
    GroupProposalCreateCommand, GroupProposalCreateResult, GroupProposalPreviewCommand,
    GroupUseCaseError,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::HttpAdapterError;
use crate::mapping::principal::to_app_proposal_context;
use crate::state::HttpAppState;

use super::authenticated_bot_from_headers;

#[derive(Debug, Deserialize, Serialize)]
pub struct EvaluateProposalRequest {
    pub topic: String,
    #[serde(default)]
    pub suggested_participants: Vec<String>,
    #[serde(default)]
    pub suggested_driver: Option<String>,
    #[serde(default)]
    pub context: Option<wire::ProposalContext>,
}

pub async fn group_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    Json(req): Json<EvaluateProposalRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let bot_uuid = authenticated_bot_from_headers(&state, &headers).await?;

    let result = state
        .services
        .group_proposals
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some(bot_uuid.clone()),
            driver_bot_id: bot_uuid,
            suggested_driver_bot_id: req.suggested_driver,
            suggested_participants: req.suggested_participants,
            topic: req.topic,
            context: to_app_proposal_context(req.context),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(group_proposal_create_to_json(result)))
}

pub async fn confirm_group_page(
    State(state): State<HttpAppState>,
    Path(token): Path<String>,
) -> Result<Html<String>, HttpAdapterError> {
    match state
        .services
        .group_proposals
        .preview_proposal(GroupProposalPreviewCommand {
            token: token.clone(),
        })
        .await
    {
        Ok(result) => Ok(Html(confirm_page(&result.token, &result.proposal))),
        Err(GroupUseCaseError::ProposalExpired(_)) => Ok(Html(expired_page())),
        Err(error) => Err(group_use_case_error_to_http(error)),
    }
}

pub async fn confirm_group(
    State(state): State<HttpAppState>,
    Path(token): Path<String>,
) -> Response {
    match confirm_group_inner(state, token).await {
        Ok(value) => Json(value).into_response(),
        Err(error) => error.into_response(),
    }
}

async fn confirm_group_inner(
    state: HttpAppState,
    token: String,
) -> Result<Value, HttpAdapterError> {
    let result = state
        .services
        .group_proposals
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: None,
            token,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(group_proposal_confirm_to_json(result))
}

fn group_proposal_create_to_json(result: GroupProposalCreateResult) -> Value {
    serde_json::json!({
        "proposal_created": result.proposal_created,
        "driver_bot": result.driver_bot_id,
        "participants": result.participant_bot_ids,
        "member_intros": result.member_intros,
        "confirm_url": result.confirm_url,
        "expires_in_seconds": result.expires_in_seconds,
        "message": result.message,
    })
}

fn group_proposal_confirm_to_json(result: GroupProposalConfirmResult) -> Value {
    serde_json::json!({
        "created": result.created,
        "group_id": result.group_id,
        "driver_bot": result.driver_bot_id,
        "participants": result.participant_bot_ids,
        "chat_url": result.chat_url,
        "context_injected": result.context_injected,
    })
}

fn group_use_case_error_to_http(error: GroupUseCaseError) -> HttpAdapterError {
    match error {
        GroupUseCaseError::Unauthorized(message) => HttpAdapterError::Unauthorized(message),
        GroupUseCaseError::Forbidden(message) => HttpAdapterError::Forbidden(message),
        GroupUseCaseError::InvalidGroupId(message)
        | GroupUseCaseError::InvalidGroupStatus(message)
        | GroupUseCaseError::InvalidProposal(message) => HttpAdapterError::BadRequest(message),
        GroupUseCaseError::InvalidHistoryLimit(limit) => {
            HttpAdapterError::BadRequest(format!("Invalid history limit: {}", limit))
        }
        GroupUseCaseError::ActorNotFound(actor_id) => {
            HttpAdapterError::NotFound(format!("Actor '{}' not found", actor_id))
        }
        GroupUseCaseError::ProposalNotFound(proposal_id)
        | GroupUseCaseError::ProposalExpired(proposal_id) => {
            HttpAdapterError::NotFound(format!("Proposal '{}' not found or expired", proposal_id))
        }
        GroupUseCaseError::InvalidParticipantMode { mode, actor_kind } => {
            HttpAdapterError::BadRequest(format!(
                "mode '{:?}' is not valid for actor_kind '{:?}'",
                mode, actor_kind
            ))
        }
        GroupUseCaseError::Conflict(message) => HttpAdapterError::Conflict(message),
        GroupUseCaseError::Service(error) => HttpAdapterError::Service(error),
    }
}

fn confirm_page(token: &str, proposal: &GroupChatProposal) -> String {
    format!(
        r#"<!DOCTYPE html>
<html>
<head><title>确认群聊</title><meta charset="utf-8"></head>
<body style="font-family: sans-serif; padding: 40px; max-width: 600px; margin: 0 auto;">
    <h1>📋 确认创建群聊</h1>
    <p><strong>Driver：</strong>{}</p>
    <p><strong>原因：</strong>{}</p>
    <h3>参与者：</h3>
    <pre style="background: #f5f5f5; padding: 15px; border-radius: 5px; white-space: pre-wrap;">{}</pre>
    <form action="/groups/{}/confirm" method="post" style="margin-top: 20px;">
        <button type="submit" style="background: #4CAF50; color: white; padding: 15px 30px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px;">
            ✅ 确认创建群聊
        </button>
    </form>
</body>
</html>"#,
        proposal.driver_bot, proposal.reason, proposal.member_intros, token
    )
}

fn expired_page() -> String {
    r#"<!DOCTYPE html>
<html>
<head><title>提案已过期</title></head>
<body style="font-family: sans-serif; padding: 40px; text-align: center;">
    <h1>⏰ 提案已过期</h1>
    <p>此群聊提案已超过10分钟有效期，请重新发起。</p>
</body>
</html>"#
        .to_string()
}
