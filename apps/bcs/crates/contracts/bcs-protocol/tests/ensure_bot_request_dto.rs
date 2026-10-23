//! `EnsureBotRequest` DTO tests — the ensure/admin contract carries the
//! optional human-facing `user_visibility` alongside the bot-network
//! `visibility`, and both payload shapes (field present / absent) must keep
//! round-tripping without inventing a default.

use bcs_protocol::EnsureBotRequest;

#[test]
fn ensure_bot_request_carries_user_visibility_when_present() {
    let decoded: EnsureBotRequest = serde_json::from_value(serde_json::json!({
        "name": "TC sync bot",
        "staff_no": "85020",
        "visibility": "protected",
        "user_visibility": "public",
    }))
    .unwrap();

    assert_eq!(decoded.staff_no, "85020");
    assert_eq!(decoded.visibility, "protected");
    assert_eq!(decoded.user_visibility.as_deref(), Some("public"));
}

#[test]
fn ensure_bot_request_defaults_user_visibility_to_absent() {
    // Absent field means "preserve the existing attribute", never a silent
    // default application — the route must treat payload absence as a no-op.
    let decoded: EnsureBotRequest = serde_json::from_value(serde_json::json!({
        "staff_no": "85020",
        "visibility": "protected",
    }))
    .unwrap();

    assert_eq!(decoded.user_visibility, None);
}

#[test]
fn ensure_bot_request_roundtrips_user_visibility() {
    let request = EnsureBotRequest {
        name: Some("TC sync bot".to_string()),
        summary: Some("edge-permission fixture".to_string()),
        staff_no: "85020".to_string(),
        nick_name: Some("TC".to_string()),
        visibility: "protected".to_string(),
        user_visibility: Some("protected".to_string()),
    };

    let encoded = serde_json::to_value(&request).unwrap();
    assert_eq!(encoded["user_visibility"], serde_json::json!("protected"));
    // The bot-network attribute stays its own field, never conflated.
    assert_eq!(encoded["visibility"], serde_json::json!("protected"));

    let decoded: EnsureBotRequest = serde_json::from_value(encoded).unwrap();
    assert_eq!(decoded.name, request.name);
    assert_eq!(decoded.staff_no, request.staff_no);
    assert_eq!(decoded.visibility, request.visibility);
    assert_eq!(decoded.user_visibility, request.user_visibility);
}
