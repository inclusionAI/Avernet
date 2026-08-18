//! Wire types for `GET /bots/{id}/admission`.
use serde::Deserialize;
use bcs_domain::edge_permission::AdmissionResult;

/// Query params for `GET /bots/{bot_id}/admission`.
#[derive(Debug, Clone, Deserialize)]
pub struct AdmissionQuery {
    pub actor: String,
    #[serde(default)]
    pub originator: Option<String>,
    #[serde(default)]
    pub env: Option<String>,
}

/// `AdmissionResult` serializes directly as the response body (domain type,
/// snake_case via `AdmissionReason`'s serde derive). The `BotNotFound` reason
/// string is 待定 (spec §10) — currently serializes as `"bot_not_found"`.
pub type AdmissionResponse = AdmissionResult;

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::edge_permission::AdmissionReason;
    #[test]
    fn admission_response_serializes() {
        let r = AdmissionResult {
            allowed: true,
            grants: vec![],
            reason_code: AdmissionReason::Ok,
            public_default: false,
        };
        let s = serde_json::to_string(&r).unwrap();
        assert!(s.contains("\"allowed\":true"));
        assert!(s.contains("\"reason_code\":\"ok\""));
    }
}
