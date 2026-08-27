//! Friend-connect notification adapter that forwards BCS friend requests to
//! the backend work-order API.

use async_trait::async_trait;
use bcs_service_api::port::{
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort,
};
use bcs_service_api::{ServiceError, ServiceResult};
use serde::Serialize;

const WORK_ORDER_PATH: &str = "/openapi/v1/bots/work-orders/events";

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

    fn work_order_url(&self, user_id: &str) -> Result<reqwest::Url, ServiceError> {
        let mut url = self.base_url.join(WORK_ORDER_PATH).map_err(|error| {
            ServiceError::InternalError(format!(
                "failed to build friend work-order url from '{}': {error}",
                self.base_url
            ))
        })?;
        url.query_pairs_mut().append_pair("user_id", user_id);
        Ok(url)
    }

    fn build_request(
        &self,
        command: &FriendConnectNotificationCommand,
    ) -> Result<reqwest::RequestBuilder, ServiceError> {
        let user_id = event_actor_user_id(command);
        let url = self.work_order_url(&user_id)?;
        let payload = FriendWorkOrderEventRequest::from_command(command);
        let mut request = self.client.post(url);
        if let Some(auth) = command.request_auth.as_ref() {
            if let Some(cookie) = auth.cookie.as_deref() {
                request = request.header(reqwest::header::COOKIE, cookie);
            }
            if let Some(authorization) = auth.authorization.as_deref() {
                request = request.header(reqwest::header::AUTHORIZATION, authorization);
            }
            // Forward ingress auth-context headers (gateway principal,
            // request-id, trace-id, ...) captured by the route so the
            // backend's user-scoped auth accepts the notification. Skip
            // authorization/cookie since they are applied above.
            for (name, value) in &auth.forwarded_headers {
                let lower = name.to_ascii_lowercase();
                if lower == "authorization" || lower == "cookie" {
                    continue;
                }
                if let Ok(header_name) = reqwest::header::HeaderName::try_from(name.as_str()) {
                    request = request.header(header_name, value.as_str());
                }
            }
        }
        Ok(request.json(&payload))
    }
}

#[derive(Debug, Serialize)]
struct FriendWorkOrderEventRequest {
    event_category: String,
    biz_type: String,
    biz_id: String,
    event_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    applicant_user_id: Option<String>,
    #[serde(default)]
    approver_user_ids: Vec<String>,
    #[serde(default)]
    recipient_user_ids: Vec<String>,
    title: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    apply_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    biz_data: Option<serde_json::Value>,
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

fn applicant_user_id(actor_id: &str) -> Option<String> {
    actor_id
        .strip_prefix("human_")
        .filter(|user_id| !user_id.is_empty())
        .map(ToOwned::to_owned)
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

fn event_category_for(kind: FriendConnectNotificationKind) -> &'static str {
    match kind {
        FriendConnectNotificationKind::ApprovalRequested => "APPROVAL",
        FriendConnectNotificationKind::AutoApproved
        | FriendConnectNotificationKind::Reviewed => "NOTICE",
    }
}

fn event_actor_user_id(command: &FriendConnectNotificationCommand) -> String {
    match command.kind {
        FriendConnectNotificationKind::ApprovalRequested => {
            applicant_user_id(&command.applicant_actor_id)
                .unwrap_or_else(|| command.applicant_actor_id.clone())
        }
        FriendConnectNotificationKind::AutoApproved
        | FriendConnectNotificationKind::Reviewed => command
            .recipient_user_ids
            .first()
            .cloned()
            .or_else(|| applicant_user_id(&command.applicant_actor_id))
            .unwrap_or_else(|| command.applicant_actor_id.clone()),
    }
}

fn notification_kind_label(kind: FriendConnectNotificationKind) -> &'static str {
    match kind {
        FriendConnectNotificationKind::ApprovalRequested => "approval_requested",
        FriendConnectNotificationKind::AutoApproved => "auto_approved",
        FriendConnectNotificationKind::Reviewed => "reviewed",
    }
}

fn title_for(kind: FriendConnectNotificationKind) -> &'static str {
    match kind {
        FriendConnectNotificationKind::ApprovalRequested => "好友添加申请待审批",
        FriendConnectNotificationKind::AutoApproved => "好友添加申请已自动通过",
        FriendConnectNotificationKind::Reviewed => "好友添加申请已处理",
    }
}

fn content_for(command: &FriendConnectNotificationCommand) -> String {
    match command.kind {
        FriendConnectNotificationKind::ApprovalRequested => format!(
            "{} 申请添加 Bot {} 为好友，请在好友申请列表中处理。",
            command.applicant_actor_id, command.target_bot_id
        ),
        FriendConnectNotificationKind::AutoApproved => format!(
            "{} 与 Bot {} 的好友申请已自动通过。",
            command.applicant_actor_id, command.target_bot_id
        ),
        FriendConnectNotificationKind::Reviewed => format!(
            "{} 与 Bot {} 的好友申请已处理。",
            command.applicant_actor_id, command.target_bot_id
        ),
    }
}

impl FriendWorkOrderEventRequest {
    fn from_command(command: &FriendConnectNotificationCommand) -> Self {
        let event_type = event_type_for(command.kind, &command.applicant_actor_id);
        let biz_data = FriendWorkOrderBizData {
            request_ids: command.request_ids.clone(),
            applicant_actor_id: command.applicant_actor_id.clone(),
            target_bot_id: command.target_bot_id.clone(),
            notification_kind: notification_kind_label(command.kind).to_string(),
            message: command.message.clone(),
        };
        let biz_data = serde_json::to_value(biz_data).expect("friend work-order biz_data json");
        let (applicant_user_id, approver_user_ids, recipient_user_ids) = match command.kind {
            FriendConnectNotificationKind::ApprovalRequested => (
                applicant_user_id(&command.applicant_actor_id)
                    .or_else(|| Some(command.applicant_actor_id.clone())),
                command.recipient_user_ids.clone(),
                Vec::new(),
            ),
            FriendConnectNotificationKind::AutoApproved
            | FriendConnectNotificationKind::Reviewed => (
                None,
                Vec::new(),
                command.recipient_user_ids.clone(),
            ),
        };
        Self {
            event_category: event_category_for(command.kind).to_string(),
            event_type: event_type.to_string(),
            biz_type: "BOT_FRIEND".to_string(),
            biz_id: command
                .request_ids
                .first()
                .map(|id| id.to_string())
                .unwrap_or_default(),
            applicant_user_id,
            approver_user_ids,
            recipient_user_ids,
            title: title_for(command.kind).to_string(),
            content: Some(content_for(command)),
            apply_reason: command.message.clone(),
            biz_data: Some(biz_data),
        }
    }
}

#[async_trait]
impl FriendConnectNotificationPort for HttpFriendConnectNotificationPort {
    async fn notify(&self, command: FriendConnectNotificationCommand) -> ServiceResult<()> {
        if command.recipient_user_ids.is_empty() {
            return Ok(());
        }
        let response = self
            .build_request(&command)?
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
    fn constructor_rejects_blank_and_non_http_base_urls() {
        assert!(HttpFriendConnectNotificationPort::new("").is_err());
        assert!(HttpFriendConnectNotificationPort::new("ftp://backend.example.com").is_err());
    }

    #[tokio::test]
    async fn notify_returns_ok_when_recipient_list_is_empty() {
        let adapter = HttpFriendConnectNotificationPort::new("http://127.0.0.1:9").expect("valid url");
        adapter
            .notify(FriendConnectNotificationCommand {
                kind: FriendConnectNotificationKind::Reviewed,
                env: "dev".to_string(),
                request_ids: vec!["3".to_string()],
                applicant_actor_id: "bot_1001".to_string(),
                target_bot_id: "bot_2001".to_string(),
                recipient_user_ids: Vec::new(),
                message: Some("ignored".to_string()),
                request_auth: None,
            })
            .await
            .expect("empty recipients should short-circuit");
    }

    #[test]
    fn builds_request_with_forwarded_auth_headers() {
        let adapter = HttpFriendConnectNotificationPort::new("https://backend.example.com/api/")
            .expect("valid url");
        let request_auth = bcs_service_api::RequestAuthHeaders {
            authorization: Some("Bearer user-token".to_string()),
            cookie: Some("session=abc".to_string()),
            forwarded_headers: vec![
                ("authorization".to_string(), "Bearer user-token".to_string()),
                ("cookie".to_string(), "session=abc".to_string()),
                ("x-avernet-principal".to_string(), "jwt-payload".to_string()),
                ("x-request-id".to_string(), "rid-1".to_string()),
            ],
        };
        let request = adapter
            .build_request(&FriendConnectNotificationCommand {
                kind: FriendConnectNotificationKind::ApprovalRequested,
                env: "dev".to_string(),
                request_ids: vec!["1".to_string()],
                applicant_actor_id: "human_1001".to_string(),
                target_bot_id: "bot_2001".to_string(),
                recipient_user_ids: vec!["user_2001".to_string()],
                message: Some("please add me".to_string()),
                request_auth: Some(request_auth.clone()),
            })
            .expect("build request")
            .build()
            .expect("materialize request");
        assert_eq!(request.url().as_str(), "https://backend.example.com/openapi/v1/bots/work-orders/events?user_id=1001");
        assert_eq!(request.headers().get(reqwest::header::AUTHORIZATION).and_then(|value| value.to_str().ok()), Some("Bearer user-token"));
        assert_eq!(request.headers().get(reqwest::header::COOKIE).and_then(|value| value.to_str().ok()), Some("session=abc"));
        assert_eq!(request.headers().get("x-avernet-principal").and_then(|value| value.to_str().ok()), Some("jwt-payload"));
        assert_eq!(request.headers().get("x-request-id").and_then(|value| value.to_str().ok()), Some("rid-1"));
        assert_eq!(
            request.headers().get_all(reqwest::header::AUTHORIZATION).iter().count(),
            1,
            "authorization must not be duplicated by forwarded_headers"
        );
    }

    #[tokio::test]
    async fn notify_returns_internal_error_when_backend_is_unavailable() {
        let mut adapter = HttpFriendConnectNotificationPort::new("http://127.0.0.1:9")
            .expect("valid url");
        adapter.client = reqwest::Client::builder()
            .no_proxy()
            .timeout(std::time::Duration::from_millis(100))
            .build()
            .expect("build test client");
        let result = adapter
            .notify(FriendConnectNotificationCommand {
                kind: FriendConnectNotificationKind::ApprovalRequested,
                env: "dev".to_string(),
                request_ids: vec!["1".to_string()],
                applicant_actor_id: "human_1001".to_string(),
                target_bot_id: "bot_2001".to_string(),
                recipient_user_ids: vec!["user_2001".to_string()],
                message: Some("please add me".to_string()),
                request_auth: None,
            })
            .await;
        assert!(matches!(result, Err(ServiceError::InternalError(message)) if message.contains("friend work-order create request failed")));
    }

    #[test]
    fn builds_pending_friend_request_payload() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "human_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: Some("please add me".to_string()),
            request_auth: None,
        });

        assert_eq!(payload.event_category, "APPROVAL");
        assert_eq!(payload.event_type, "HUMAN2BOT_FRIEND_APPLIED");
        assert_eq!(payload.biz_type, "BOT_FRIEND");
        assert_eq!(payload.biz_id, "1");
        assert_eq!(payload.applicant_user_id.as_deref(), Some("1001"));
        assert_eq!(payload.apply_reason.as_deref(), Some("please add me"));
        assert_eq!(payload.approver_user_ids, vec!["user_2001".to_string()]);
        assert!(payload.recipient_user_ids.is_empty());
        assert_eq!(payload.title, "好友添加申请待审批");
        assert_eq!(
            payload.content.as_deref(),
            Some("human_1001 申请添加 Bot bot_2001 为好友，请在好友申请列表中处理。")
        );
        assert_eq!(
            payload.biz_data,
            Some(serde_json::json!({
                "request_ids": ["1"],
                "applicant_actor_id": "human_1001",
                "target_bot_id": "bot_2001",
                "notification_kind": "approval_requested",
                "message": "please add me"
            }))
        );
    }

    #[test]
    fn builds_auto_approved_notice_payload() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::AutoApproved,
            env: "dev".to_string(),
            request_ids: vec!["2".to_string()],
            applicant_actor_id: "bot_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: None,
            request_auth: None,
        });

        assert_eq!(payload.event_category, "NOTICE");
        assert_eq!(payload.event_type, "BOT2BOT_FRIEND_REVIEWED");
        assert_eq!(payload.applicant_user_id, None);
        assert!(payload.approver_user_ids.is_empty());
        assert_eq!(payload.recipient_user_ids, vec!["user_2001".to_string()]);
        assert_eq!(payload.title, "好友添加申请已自动通过");
    }

    #[test]
    fn builds_bot_to_bot_pending_payload() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["4".to_string()],
            applicant_actor_id: "bot_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: Some("bot-to-bot".to_string()),
            request_auth: None,
        });

        assert_eq!(payload.event_category, "APPROVAL");
        assert_eq!(payload.event_type, "BOT2BOT_FRIEND_APPLIED");
        assert_eq!(payload.applicant_user_id.as_deref(), Some("bot_1001"));
        assert_eq!(payload.approver_user_ids, vec!["user_2001".to_string()]);
        assert!(payload.recipient_user_ids.is_empty());
        assert_eq!(payload.title, "好友添加申请待审批");
        assert_eq!(payload.apply_reason.as_deref(), Some("bot-to-bot"));
        assert_eq!(
            payload.content.as_deref(),
            Some("bot_1001 申请添加 Bot bot_2001 为好友，请在好友申请列表中处理。")
        );
    }

    #[test]
    fn builds_reviewed_notice_payload_for_human_actor() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::Reviewed,
            env: "dev".to_string(),
            request_ids: vec!["5".to_string()],
            applicant_actor_id: "human_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: Some("handled".to_string()),
            request_auth: None,
        });

        assert_eq!(payload.event_category, "NOTICE");
        assert_eq!(payload.event_type, "HUMAN2BOT_FRIEND_REVIEWED");
        assert_eq!(payload.applicant_user_id, None);
        assert!(payload.approver_user_ids.is_empty());
        assert_eq!(payload.recipient_user_ids, vec!["user_2001".to_string()]);
        assert_eq!(payload.title, "好友添加申请已处理");
        assert_eq!(payload.apply_reason.as_deref(), Some("handled"));
        assert_eq!(
            payload.content.as_deref(),
            Some("human_1001 与 Bot bot_2001 的好友申请已处理。")
        );
    }

    #[test]
    fn appends_user_id_to_work_order_event_url() {
        let adapter = HttpFriendConnectNotificationPort::new("https://backend.example.com/api/")
            .expect("valid url");
        let url = adapter.work_order_url("user_1001").expect("work order url");
        assert_eq!(
            url.as_str(),
            "https://backend.example.com/openapi/v1/bots/work-orders/events?user_id=user_1001"
        );
    }

    #[test]
    fn picks_event_actor_user_id_for_backend_user_scope() {
        let pending = FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "human_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: None,
            request_auth: None,
        };
        assert_eq!(event_actor_user_id(&pending), "1001");

        let notice = FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::Reviewed,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "bot_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: None,
            request_auth: None,
        };
        assert_eq!(event_actor_user_id(&notice), "user_2001");
    }

    #[test]
    fn applicant_user_id_handles_non_human_actor() {
        assert_eq!(applicant_user_id("bot_1001"), None);
        assert_eq!(applicant_user_id("human_"), None);
    }
}
