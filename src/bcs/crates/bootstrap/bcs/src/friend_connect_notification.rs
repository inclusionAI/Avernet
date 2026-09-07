//! Friend-connect notification adapter that forwards BCS friend requests to
//! the backend work-order API.

use async_trait::async_trait;
use bcs_service_api::port::{
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort,
};
use bcs_service_api::{ServiceError, ServiceResult};
use serde::Serialize;
use tracing::{debug, info, warn};

/// Backend internal (non-openapi) endpoint; authenticates BCS via the
/// forwarded gateway principal (`x-avernet-principal`) and derives the
/// acting user from it, so no `user_id` query param is sent.
const WORK_ORDER_PATH: &str = "/api/v1/work-orders/events";

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

    fn request_parts(
        &self,
        command: &FriendConnectNotificationCommand,
    ) -> Result<(reqwest::Url, FriendWorkOrderEventRequest), ServiceError> {
        let url = self.work_order_url()?;
        let payload = FriendWorkOrderEventRequest::from_command(command);
        Ok((url, payload))
    }

    fn build_request_with_payload(
        &self,
        command: &FriendConnectNotificationCommand,
        url: reqwest::Url,
        payload: &FriendWorkOrderEventRequest,
    ) -> reqwest::RequestBuilder {
        let mut request = self.client.post(url);
        // The backend internal endpoint authenticates BCS via the forwarded
        // gateway principal (`x-avernet-principal`) and derives the acting
        // user from it. Propagate request-id/trace-id for diagnostics.
        // Openapi Authorization/Cookie identity is intentionally NOT sent.
        if let Some(auth) = command.request_auth.as_ref() {
            for (name, value) in &auth.forwarded_headers {
                let lower = name.to_ascii_lowercase();
                if lower == "x-avernet-principal" || lower == "x-request-id" || lower == "x-trace-id" {
                    if let Ok(header_name) = reqwest::header::HeaderName::try_from(name.as_str()) {
                        request = request.header(header_name, value.as_str());
                    }
                }
            }
        }
        request.json(payload)
    }

    #[cfg(test)]
    fn build_request(
        &self,
        command: &FriendConnectNotificationCommand,
        payload: &FriendWorkOrderEventRequest,
    ) -> Result<reqwest::RequestBuilder, ServiceError> {
        let url = self.work_order_url()?;
        Ok(self.build_request_with_payload(command, url, payload))
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
    content: Option<serde_json::Value>,
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

fn notification_kind_label(kind: FriendConnectNotificationKind) -> &'static str {
    match kind {
        FriendConnectNotificationKind::ApprovalRequested => "approval_requested",
        FriendConnectNotificationKind::AutoApproved => "auto_approved",
        FriendConnectNotificationKind::Reviewed => "reviewed",
    }
}

fn title_for(kind: FriendConnectNotificationKind) -> &'static str {
    match kind {
        FriendConnectNotificationKind::ApprovalRequested => "好友申请待审批",
        FriendConnectNotificationKind::AutoApproved => "好友申请已自动通过",
        FriendConnectNotificationKind::Reviewed => "好友申请已处理",
    }
}

fn content_for(command: &FriendConnectNotificationCommand) -> String {
    // Prefer the resolved display names (a human nick name / a bot name); fall
    // back to the raw actor ids when a name could not be resolved.
    let applicant = command
        .applicant_name
        .as_deref()
        .unwrap_or(&command.applicant_actor_id);
    let target = command
        .target_bot_name
        .as_deref()
        .unwrap_or(&command.target_bot_id);
    match command.kind {
        FriendConnectNotificationKind::ApprovalRequested => format!(
            "{}申请添加你的 Bot「{}」为好友，请及时处理。",
            applicant, target
        ),
        FriendConnectNotificationKind::AutoApproved => format!(
            "{}与 Bot「{}」的好友申请已自动通过。",
            applicant, target
        ),
        FriendConnectNotificationKind::Reviewed => format!(
            "{}与 Bot「{}」的好友申请已处理。",
            applicant, target
        ),
    }
}

fn friend_work_order_payload_for_log(payload: &FriendWorkOrderEventRequest) -> String {
    serde_json::to_string(payload).unwrap_or_else(|error| {
        format!("<failed to serialize friend work-order request payload: {error}>")
    })
}

fn friend_work_order_non_success_error(
    status: reqwest::StatusCode,
    body: &str,
    url: &reqwest::Url,
    payload: &FriendWorkOrderEventRequest,
    command: &FriendConnectNotificationCommand,
) -> ServiceError {
    warn!(
        kind = %notification_kind_label(command.kind),
        request_ids = ?command.request_ids,
        target_bot_id = %command.target_bot_id,
        status = %status,
        url = %url,
        response_body = %body,
        request_payload = %friend_work_order_payload_for_log(payload),
        "friend-connect notification failed"
    );
    ServiceError::InternalError(format!(
        "friend work-order create request returned {status}: {body}"
    ))
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
                command
                    .applicant_user_id
                    .clone()
                    .or_else(|| applicant_user_id(&command.applicant_actor_id))
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
            content: Some(serde_json::json!({ "text": content_for(command) })),
            apply_reason: command.message.clone(),
            biz_data: Some(biz_data),
        }
    }
}

#[async_trait]
impl FriendConnectNotificationPort for HttpFriendConnectNotificationPort {
    async fn notify(&self, command: FriendConnectNotificationCommand) -> ServiceResult<()> {
        bcs_observability::observe_result("callback.friend_work_order", async {
        if command.recipient_user_ids.is_empty() {
            debug!(
                kind = %notification_kind_label(command.kind),
                request_ids = ?command.request_ids,
                target_bot_id = %command.target_bot_id,
                "friend-connect notification skipped: no recipients"
            );
            return Ok(());
        }
        info!(
            kind = %notification_kind_label(command.kind),
            request_ids = ?command.request_ids,
            target_bot_id = %command.target_bot_id,
            recipient_count = command.recipient_user_ids.len(),
            "sending friend-connect notification"
        );
        let (url, payload) = self.request_parts(&command)?;
        let request_payload = friend_work_order_payload_for_log(&payload);
        let response = self
            .build_request_with_payload(&command, url.clone(), &payload)
            .send()
            .await
            .map_err(|error| {
                warn!(
                    kind = %notification_kind_label(command.kind),
                    request_ids = ?command.request_ids,
                    target_bot_id = %command.target_bot_id,
                    url = %url,
                    request_payload = %request_payload,
                    error = %error,
                    "friend-connect notification request failed"
                );
                ServiceError::InternalError(format!(
                    "friend work-order create request failed: {error}"
                ))
            })?;
        if response.status().is_success() {
            info!(
                kind = %notification_kind_label(command.kind),
                request_ids = ?command.request_ids,
                target_bot_id = %command.target_bot_id,
                status = %response.status(),
                "friend-connect notification sent successfully"
            );
            return Ok(());
        }
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        Err(friend_work_order_non_success_error(
            status,
            &body,
            &url,
            &payload,
            &command,
        ))
            }).await
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
                applicant_name: None,
                target_bot_name: None,
                applicant_user_id: None,
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
        let command = FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "human_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: Some("please add me".to_string()),
            request_auth: Some(request_auth.clone()),
            applicant_name: None,
            target_bot_name: None,
            applicant_user_id: None,
        };
        let payload = FriendWorkOrderEventRequest::from_command(&command);
        let request = adapter
            .build_request(&command, &payload)
            .expect("build request")
            .build()
            .expect("materialize request");
        assert_eq!(request.url().as_str(), "https://backend.example.com/api/v1/work-orders/events");
        assert!(request.headers().get(reqwest::header::AUTHORIZATION).is_none(), "openapi Authorization is not forwarded to the internal endpoint");
        assert!(request.headers().get(reqwest::header::COOKIE).is_none(), "openapi Cookie is not forwarded to the internal endpoint");
        assert_eq!(request.headers().get("x-avernet-principal").and_then(|value| value.to_str().ok()), Some("jwt-payload"));
        assert_eq!(request.headers().get("x-request-id").and_then(|value| value.to_str().ok()), Some("rid-1"));
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
                applicant_name: None,
                target_bot_name: None,
                applicant_user_id: None,
            })
            .await;
        assert!(matches!(result, Err(ServiceError::InternalError(message)) if message.contains("friend work-order create request failed")));
    }

    #[test]
    fn non_success_error_logs_request_payload_with_json_content_and_approvers() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "human_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: Some("please add me".to_string()),
            request_auth: None,
            applicant_name: None,
            target_bot_name: None,
            applicant_user_id: None,
        });
        let url = reqwest::Url::parse(
            "https://backend.example.com/api/v1/work-orders/events",
        )
        .expect("url");

        let command = FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "human_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: Some("please add me".to_string()),
            request_auth: None,
            applicant_name: None,
            target_bot_name: None,
            applicant_user_id: None,
        };
        let error = friend_work_order_non_success_error(
            reqwest::StatusCode::UNPROCESSABLE_ENTITY,
            "Invalid request",
            &url,
            &payload,
            &command,
        );
        assert!(matches!(error, ServiceError::InternalError(message) if message.contains("422 Unprocessable Entity")));

        let logged_payload: serde_json::Value = serde_json::from_str(
            &friend_work_order_payload_for_log(&payload),
        )
        .expect("request payload log is json");
        assert_eq!(
            logged_payload["content"],
            serde_json::json!({
                "text": "human_1001申请添加你的 Bot「bot_2001」为好友，请及时处理。"
            })
        );
        assert_eq!(logged_payload["approver_user_ids"], serde_json::json!(["user_2001"]));
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
            applicant_name: None,
            target_bot_name: None,
            applicant_user_id: None,
        });

        assert_eq!(payload.event_category, "APPROVAL");
        assert_eq!(payload.event_type, "HUMAN2BOT_FRIEND_APPLIED");
        assert_eq!(payload.biz_type, "BOT_FRIEND");
        assert_eq!(payload.biz_id, "1");
        assert_eq!(payload.applicant_user_id.as_deref(), Some("1001"));
        assert_eq!(payload.apply_reason.as_deref(), Some("please add me"));
        assert_eq!(payload.approver_user_ids, vec!["user_2001".to_string()]);
        assert!(payload.recipient_user_ids.is_empty());
        assert_eq!(payload.title, "好友申请待审批");
        assert_eq!(
            payload.content,
            Some(serde_json::json!({
                "text": "human_1001申请添加你的 Bot「bot_2001」为好友，请及时处理。"
            }))
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
    fn serializes_content_as_json_object_and_keeps_approvers() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "human_1001".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["user_2001".to_string()],
            message: Some("please add me".to_string()),
            request_auth: None,
            applicant_name: None,
            target_bot_name: None,
            applicant_user_id: None,
        });

        let serialized = serde_json::to_value(&payload).expect("serialize payload");
        assert!(serialized["content"].is_object(), "content must be a JSON object for backend schema");
        assert_eq!(
            serialized["content"],
            serde_json::json!({
                "text": "human_1001申请添加你的 Bot「bot_2001」为好友，请及时处理。"
            })
        );
        assert_eq!(
            serialized["approver_user_ids"],
            serde_json::json!(["user_2001"]),
            "approval notifications must pass target owner ids as approvers"
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
            applicant_name: None,
            target_bot_name: None,
            applicant_user_id: None,
        });

        assert_eq!(payload.event_category, "NOTICE");
        assert_eq!(payload.event_type, "BOT2BOT_FRIEND_REVIEWED");
        assert_eq!(payload.applicant_user_id, None);
        assert!(payload.approver_user_ids.is_empty());
        assert_eq!(payload.recipient_user_ids, vec!["user_2001".to_string()]);
        assert_eq!(payload.title, "好友申请已自动通过");
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
            applicant_name: None,
            target_bot_name: None,
            applicant_user_id: Some("152819".to_string()),
        });

        assert_eq!(payload.event_category, "APPROVAL");
        assert_eq!(payload.event_type, "BOT2BOT_FRIEND_APPLIED");
        // bot→bot: applicant_user_id is the initiating bot's OWNER (user id),
        // not the bot id (the backend rejects a bot id as not matching the user).
        assert_eq!(payload.applicant_user_id.as_deref(), Some("152819"));
        assert_eq!(payload.approver_user_ids, vec!["user_2001".to_string()]);
        assert!(payload.recipient_user_ids.is_empty());
        assert_eq!(payload.title, "好友申请待审批");
        assert_eq!(payload.apply_reason.as_deref(), Some("bot-to-bot"));
        assert_eq!(
            payload.content,
            Some(serde_json::json!({
                "text": "bot_1001申请添加你的 Bot「bot_2001」为好友，请及时处理。"
            }))
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
            applicant_name: None,
            target_bot_name: None,
            applicant_user_id: None,
        });

        assert_eq!(payload.event_category, "NOTICE");
        assert_eq!(payload.event_type, "HUMAN2BOT_FRIEND_REVIEWED");
        assert_eq!(payload.applicant_user_id, None);
        assert!(payload.approver_user_ids.is_empty());
        assert_eq!(payload.recipient_user_ids, vec!["user_2001".to_string()]);
        assert_eq!(payload.title, "好友申请已处理");
        assert_eq!(payload.apply_reason.as_deref(), Some("handled"));
        assert_eq!(
            payload.content,
            Some(serde_json::json!({
                "text": "human_1001与 Bot「bot_2001」的好友申请已处理。"
            }))
        );
    }

    #[test]
    fn builds_internal_work_order_event_url() {
        let adapter = HttpFriendConnectNotificationPort::new("https://backend.example.com/api/")
            .expect("valid url");
        let url = adapter.work_order_url().expect("work order url");
        assert_eq!(
            url.as_str(),
            "https://backend.example.com/api/v1/work-orders/events"
        );
    }

    #[test]
    fn applicant_user_id_handles_non_human_actor() {
        assert_eq!(applicant_user_id("bot_1001"), None);
        assert_eq!(applicant_user_id("human_"), None);
    }

    #[test]
    fn approval_requested_content_uses_resolved_names() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "human_447147".to_string(),
            target_bot_id: "20260828_eeua0r54:330429".to_string(),
            recipient_user_ids: vec!["447147".to_string()],
            message: None,
            request_auth: None,
            applicant_name: Some("李四".to_string()),
            target_bot_name: Some("本地代码专家".to_string()),
            applicant_user_id: None,
        });
        assert_eq!(payload.title, "好友申请待审批");
        assert_eq!(
            payload.content,
            Some(serde_json::json!({
                "text": "李四申请添加你的 Bot「本地代码专家」为好友，请及时处理。"
            }))
        );
    }

    #[test]
    fn approval_requested_content_falls_back_per_field_when_partially_resolved() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::ApprovalRequested,
            env: "dev".to_string(),
            request_ids: vec!["1".to_string()],
            applicant_actor_id: "human_447147".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["447147".to_string()],
            message: None,
            request_auth: None,
            applicant_name: Some("李四".to_string()),
            target_bot_name: None,
            applicant_user_id: None,
        });
        assert_eq!(
            payload.content,
            Some(serde_json::json!({
                "text": "李四申请添加你的 Bot「bot_2001」为好友，请及时处理。"
            }))
        );
    }

    #[test]
    fn auto_approved_content_uses_resolved_names() {
        let payload = FriendWorkOrderEventRequest::from_command(&FriendConnectNotificationCommand {
            kind: FriendConnectNotificationKind::AutoApproved,
            env: "dev".to_string(),
            request_ids: vec!["2".to_string()],
            applicant_actor_id: "human_447147".to_string(),
            target_bot_id: "bot_2001".to_string(),
            recipient_user_ids: vec!["447147".to_string()],
            message: None,
            request_auth: None,
            applicant_name: Some("李四".to_string()),
            target_bot_name: Some("本地代码专家".to_string()),
            applicant_user_id: None,
        });
        assert_eq!(payload.title, "好友申请已自动通过");
        assert_eq!(
            payload.content,
            Some(serde_json::json!({
                "text": "李四与 Bot「本地代码专家」的好友申请已自动通过。"
            }))
        );
    }
}
