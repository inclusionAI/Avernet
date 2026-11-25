use super::{StructuredResult, classify_auth_error_message};

#[test]
fn test_structured_result_ok_serialization() {
    let result = StructuredResult {
        status: "ok".to_string(),
        message: None,
        network_env: None,
        auth_url: None,
        timeout_secs: None,
        log_file: Some("/tmp/bcs.log".to_string()),
    };
    let json = serde_json::to_string(&result).unwrap();
    assert!(json.contains("\"status\":\"ok\""));
    assert!(json.contains("\"log_file\":"));
}

#[test]
fn test_structured_result_serialization() {
    let result = StructuredResult {
        status: "auth_timeout".to_string(),
        message: Some("timeout".to_string()),
        network_env: Some("office".to_string()),
        auth_url: None,
        timeout_secs: Some(120),
        log_file: Some("/tmp/bcs.log".to_string()),
    };
    let json = serde_json::to_string(&result).unwrap();
    assert!(json.contains("\"status\":\"auth_timeout\""));
    assert!(json.contains("\"network_env\":\"office\""));
    assert!(json.contains("\"timeout_secs\":120"));
}

#[test]
fn test_classify_auth_error_message_timeout() {
    assert_eq!(
        classify_auth_error_message("OAuth2 authorization timed out locally after 2 minutes"),
        "auth_timeout"
    );
}

#[test]
fn test_classify_auth_error_message_expired() {
    assert_eq!(
        classify_auth_error_message(
            "OAuth2 authorization expired on server. Please rerun the command."
        ),
        "auth_expired"
    );
}

#[test]
fn test_classify_auth_error_message_request_failed() {
    assert_eq!(
        classify_auth_error_message("Invalid bots list response"),
        "request_failed"
    );
}
