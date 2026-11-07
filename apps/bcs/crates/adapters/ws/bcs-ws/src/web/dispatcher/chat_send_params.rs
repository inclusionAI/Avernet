use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
pub(super) struct ChatSendParams {
    #[serde(alias = "sessionKey")]
    pub(super) session_key: Option<String>,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    pub(super) session_id: Option<String>,
    pub(super) message: String,
    pub(super) group_id: String,
    pub(super) bot_uuid: Option<String>,
    pub(super) bot_id: Option<String>,
    pub(super) bot_name: Option<String>,
    #[serde(default)]
    pub(super) mentions: Vec<String>,
    pub(super) thinking: Option<String>,
    #[serde(alias = "idempotencyKey")]
    pub(super) idempotency_key: Option<String>,
    /// Stable client-side message correlation ID. It is used as the canonical
    /// message client key when present; legacy callers continue using
    /// `idempotency_key`.
    #[serde(default, alias = "clientMessageId")]
    pub(super) client_message_id: Option<String>,
    /// Speaking-lease coordinates for caller groups (spec §6.3): the id
    /// and epoch an acquire/preempt returned. Optional — plain groups
    /// never carry it and DM traffic never fences on it.
    #[serde(default, alias = "leaseId")]
    pub(super) lease_id: Option<String>,
    #[serde(default, alias = "leaseEpoch")]
    pub(super) lease_epoch: Option<u64>,
    pub(super) attachments: Option<Vec<bcs_protocol::Attachment>>,
}

#[derive(Debug, Serialize)]
pub(super) struct ChatSendResponse {
    #[serde(rename = "runId")]
    pub(super) run_id: String,
    pub(super) status: String,
}


#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn accepts_snake_case_and_camel_case_correlation_without_changing_legacy_requests() {
        for key in ["client_message_id", "clientMessageId"] {
            let mut value = serde_json::json!({"message":"same", "group_id":"g", "idempotencyKey":"legacy"});
            value[key] = serde_json::json!("client-1");
            let params: ChatSendParams = serde_json::from_value(value).unwrap();
            assert_eq!(params.client_message_id.or(params.idempotency_key).as_deref(), Some("client-1"));
        }
        let params: ChatSendParams = serde_json::from_value(serde_json::json!({"message":"same", "group_id":"g", "idempotencyKey":"legacy"})).unwrap();
        assert_eq!(params.client_message_id.or(params.idempotency_key).as_deref(), Some("legacy"));
    }
}
