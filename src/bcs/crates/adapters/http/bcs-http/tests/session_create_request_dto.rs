//! Contract tests for the `group_context_delivery` field of
//! `CreateSessionRequest` (`POST /groups/{id}/sessions`).
//!
//! The field chooses whether the driver bot's `[GROUP CONTEXT]` message is
//! actively sent (`"send"`, default, asks the driver to respond) or silently
//! injected (`"inject"`). Other participants always receive `chat.inject`.

use bcs_domain::DeliveryType;
use bcs_http::routes::sessions::CreateSessionRequest;

#[test]
fn group_context_delivery_defaults_to_none() {
    let req: CreateSessionRequest = serde_json::from_str(r#"{"input":"task"}"#).unwrap();
    assert_eq!(req.group_context_delivery, None);
}

#[test]
fn group_context_delivery_accepts_send_and_inject() {
    let req: CreateSessionRequest =
        serde_json::from_str(r#"{"group_context_delivery":"send"}"#).unwrap();
    assert_eq!(req.group_context_delivery, Some(DeliveryType::Send));

    let req: CreateSessionRequest =
        serde_json::from_str(r#"{"group_context_delivery":"inject"}"#).unwrap();
    assert_eq!(req.group_context_delivery, Some(DeliveryType::Inject));
}

#[test]
fn group_context_delivery_rejects_unknown_value() {
    assert!(serde_json::from_str::<CreateSessionRequest>(
        r#"{"group_context_delivery":"bogus"}"#
    )
    .is_err());
}
