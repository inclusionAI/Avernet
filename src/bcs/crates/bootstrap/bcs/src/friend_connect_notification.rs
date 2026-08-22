//! Friend-connect notification adapter that forwards BCS friend requests to
//! the backend work-order API.

use async_trait::async_trait;
use bcs_service_api::port::{
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort,
};
use bcs_service_api::{ServiceError, ServiceResult};
use serde::Serialize;

const WORK_ORDER_PATH: &str = "/openapi/v1/bots/work-orders";

#[derive(Debug, Clone)]
pub struct HttpFriendConnectNotificationPort {
    client: reqwest::Client,
    base_url: reqwest::Url,
}

impl HttpFriendConnectNotificationPort {
    pub fn new(base_url: &str) -> Result<Self, ServiceError> {
        let base_url = base_url.trim();
        if base_url.is_empty() {
            return Err(ServiceError::InternalError(
                "friend work-order base url must not be blank".to_string(),
            ));
        }
        let base_url = reqwest::Url::parse(base_url).map_err(|error| {
            ServiceError::InternalError(format!(
                "invalid friend work-order base url '{base_url}': {error}"
            ))
        })?;
        if !matches!(base_url.scheme(), "http" | "https") {
            return Err(ServiceError::InternalError(format!(
                "friend work-order base url must use http or https: {base_url}"
            )));
        }
        Ok(Self {
            client: reqwest::Client::new(),
            base_url,
        })
    }

    fn work_order_url(&self) -> Result<reqwest::Url, ServiceError> {
        self.base_url.join(WORK_ORDER_PATH).map_err(|error| {
            ServiceError::InternalError(format!(
                "failed to build friend work-order url from '{}': {error}",
                self.base_url
            ))
        })
    }
}

#[derive(Debug, Serialize)]
struct FriendWorkOrderCreateRequest {
    event_type: String,
    biz_type: String,
    biz_id: String,
    applicant_user_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    apply_reason: Option<String>,
    #[serde(default)]
    biz_data: serde_json::Value,
    #[serde(default)]
    approver_user_ids: Vec<String>,
    #[serde(default)]
    notification_recipient_user_ids: Vec<String>,
}

#[derive(Debug, Serialize)]
struct FriendWorkOrderBizData {
    request_ids: Vec<String>,
    applicant_actor_id: String,
    target_bot_id: String,
    notification_kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

fn is_human_actor(actor_id: &str) -> bool {
    actor_id.starts_with("human_")
}

fn event_type_for(kind: FriendConnectNotificationKind, applicant_actor_id: &str) -> &'static str {
    match (kind, is_human_actor(applicant_actor_id)) {
        (FriendConnectNotificationKind::ApprovalRequested, true) => {
            "HUMAN2BOT_FRIEND_APPLIED"
        }
        (FriendConnectNotificationKind::ApprovalRequested, false) => {
            "BOT2BOT_FRIEND_APPLIED"
        }
        (FriendConnectNotificationKind::AutoApproved, true)
        | (FriendConnectNotificationKind::Reviewed, true) => "HUMAN2BOT_FRIEND_REVIEWED",
        (FriendConnectNotificationKind::AutoApproved, false)
        | (FriendConnectNotificationKind::Reviewed, false) => "BOT2BOT_FRIEND_REVIEWED",
    }
}

impl FriendWorkOrderCreateRequest {
    fn from_command(command: &FriendConnectNotificationCommand) -> Self {
        let event_type = event_type_for(command.kind, &command.applicant_actor_id);
        let biz_data = FriendWorkOrderBizData {
            request_ids: command.request_ids.clone(),
            applicant_actor_id: command.applicant_actor_id.clone(),
            target_bot_id: command.target_bot_id.clone(),
            notification_kind: match command.kind {
                FriendConnectNotificationKind::ApprovalRequested => "approval_requested".to_string(),
                FriendConnectNotificationKind::AutoApproved => "auto_approved".to_string(),
                FriendConnectNotificationKind::Reviewed => "reviewed".to_string(),
            },
            message: command.message.clone(),
        };
        let biz_data = serde_json::to_value(biz_data).expect("friend work-order biz_data json");
        let (approver_user_ids, notification_recipient_user_ids) = match command.kind {
            FriendConnectNotificationKind::ApprovalRequested => {
                (command.recipient_user_ids.clone(), Vec::new())
            }
            FriendConnectNotificationKind::AutoApproved
            | FriendConnectNotificationKind::Reviewed => {
                (Vec::new(), command.recipient_user_ids.clone())
            }
        };
        Self {
            event_type: event_type.to_string(),
            biz_type: "BOT_FRIEND".to_string(),
            biz_id: command.request_ids.first().cloned().unwrap_or_default(),
            applicant_user_id: command.applicant_actor_id.clone(),
            apply_reason: command.message.clone(),
            biz_data,
            approver_user_ids,
            notification_recipient_user_ids,
        }
    }
}

#[async_trait]
impl FriendConnectNotificationPort for HttpFriendConnectNotificationPort {
    async fn notify(&self, command: FriendConnectNotificationCommand) -> ServiceResult<()> {
        if command.recipient_user_ids.is_empty() {
            return Ok(());
        }
        let url = self.work_order_url()?;
        let payload = FriendWorkOrderCreateRequest::from_command(&command);
        let response = self
            .client
            .post(url)
            .json(&payload)
            .send()
            .await
            .map_err(|error| ServiceError::InternalError(format!(
                "friend work-order create request failed: {error}"
            )))?;
        if response.status().is_success() {
            return Ok(());
        }
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        Err(ServiceError::InternalError(format!(
            "friend work-order create request returned {status}: {body}"
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_pending_friend_request_payload() {
        let payload = FriendWorkOrderCreateRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["req_1".to_string()],
            applicant_actor_id: "human_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: Some("please add me".to_string()),
        });

        assert_eq!(payload.event_type, "HUMAN2BOT_FRIEND_APPLIED");
        assert_eq!(payload.biz_type, "BOT_FRIEND");
        assert_eq!(payload.biz_id, "req_1");
        assert_eq!(payload.applicant_user_id, "human_1001");
        assert_eq!(payload.apply_reason.as_deref(), Some("please add me"));
        assert_eq!(payload.approver_user_ids, vec!["user_2001".to_string()]);
        assert!(payload.notification_recipient_user_ids.is_empty());
        assert_eq!(
            payload.biz_data,
            serde_json::json!({
                "request_ids": ["req_1"],
                "applicant_actor_id": "human_1001",
                "target_bot_id": "bot_2001",
                "notification_kind": "approval_requested",
                "message": "please add me"
            })
        );
    }
}
