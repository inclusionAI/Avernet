//! BaaS message callback sender.
//!
//! Uses TeamClaw/BaaS Open API `POST /openapi/v1/messages`. The callback is
//! considered successful once BaaS accepts the message (`code == 0`); BCS does
//! not poll the asynchronous message result.

use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::CallbackChannelConfig;
use tracing::info;

#[derive(Debug, Clone, PartialEq, Eq)]
struct BaasAcceptedMessage {
    trace_id: String,
    message_id: String,
    session_id: String,
}

/// Dispatch one BaaS callback. Returns Ok when BaaS accepts the message.
pub async fn send(
    channel: &CallbackChannelConfig,
    content: &str,
    extra: Option<&serde_json::Value>,
) -> Result<(), String> {
    send_with_url_guard(channel, content, extra, &OutboundUrlGuard::strict()).await
}

pub async fn send_with_url_guard(
    channel: &CallbackChannelConfig,
    content: &str,
    extra: Option<&serde_json::Value>,
    url_guard: &OutboundUrlGuard,
) -> Result<(), String> {
    let CallbackChannelConfig::Baas {
        base_url,
        api_key,
        bot_id,
        metadata,
    } = channel else {
        return Err("baas sender received non-baas callback channel".to_string());
    };

    send_baas_message(
        base_url,
        api_key,
        bot_id,
        content,
        metadata.as_ref(),
        extra,
        url_guard,
    )
    .await
}

async fn send_baas_message(
    base_url: &str,
    api_key: &str,
    bot_id: &str,
    content: &str,
    metadata: Option<&serde_json::Value>,
    extra: Option<&serde_json::Value>,
    url_guard: &OutboundUrlGuard,
) -> Result<(), String> {
    let url = messages_url(base_url);
    let body = build_message_body(bot_id, content, metadata, extra);
    let guarded_url = url_guard
        .resolve_request_http_url(&url)
        .await
        .map_err(|e| format!("baas callback URL is not allowed: {e}"))?;

    let mut client_builder = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none());
    if let Some((host, addrs)) = guarded_url.dns_override() {
        client_builder = client_builder.resolve_to_addrs(host, addrs);
    }
    let client = client_builder
        .build()
        .map_err(|e| format!("baas client build failed: {e}"))?;
    let resp = client
        .post(guarded_url.as_str())
        .bearer_auth(api_key)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("baas HTTP request failed: {e}"))?;

    let status = resp.status();
    let resp_body = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(format!(
            "baas callback failed: HTTP {status}, body: {resp_body}"
        ));
    }

    let accepted = parse_accept_response(&resp_body)?;
    info!(
        target: "baas",
        event = "baas.message.accepted",
        bot_id = %bot_id,
        trace_id = %accepted.trace_id,
        message_id = %accepted.message_id,
        session_id = %accepted.session_id,
    );
    Ok(())
}

fn messages_url(base_url: &str) -> String {
    format!("{}/openapi/v1/messages", base_url.trim_end_matches('/'))
}

fn build_message_body(
    bot_id: &str,
    content: &str,
    metadata: Option<&serde_json::Value>,
    extra: Option<&serde_json::Value>,
) -> serde_json::Value {
    let mut body = serde_json::json!({
        "bot_id": bot_id,
        "message": content,
    });
    if let Some(metadata) = build_callback_metadata(metadata, extra) {
        body["metadata"] = metadata;
    }
    body
}

fn build_callback_metadata(
    metadata: Option<&serde_json::Value>,
    extra: Option<&serde_json::Value>,
) -> Option<serde_json::Value> {
    let baas_session_id = extra
        .and_then(|value| value.pointer("/callback_target/baas_session_id"))
        .and_then(|value| value.as_str());
    let Some(baas_session_id) = baas_session_id else {
        return metadata.cloned();
    };

    let mut metadata_object = metadata
        .and_then(|value| value.as_object())
        .cloned()
        .unwrap_or_default();
    metadata_object.insert(
        "session_id".to_string(),
        serde_json::Value::String(baas_session_id.to_string()),
    );
    Some(serde_json::Value::Object(metadata_object))
}

fn parse_accept_response(resp_body: &str) -> Result<BaasAcceptedMessage, String> {
    let resp_json: serde_json::Value = serde_json::from_str(resp_body)
        .map_err(|e| format!("baas callback response is not JSON: {e}, body: {resp_body}"))?;
    let code = resp_json
        .get("code")
        .and_then(|value| value.as_i64())
        .ok_or_else(|| format!("baas callback response missing numeric code: {resp_body}"))?;
    let trace_id = resp_json
        .get("trace_id")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    if code != 0 {
        let message = resp_json
            .get("message")
            .and_then(|value| value.as_str())
            .unwrap_or("unknown error");
        return Err(format!(
            "baas callback business error: code {code}, message: {message}, trace_id: {trace_id}"
        ));
    }

    let message_id = resp_json
        .pointer("/data/message_id")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    let session_id = resp_json
        .pointer("/data/session_id")
        .and_then(|value| value.as_str())
        .unwrap_or("");

    Ok(BaasAcceptedMessage {
        trace_id: trace_id.to_string(),
        message_id: message_id.to_string(),
        session_id: session_id.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn messages_url_appends_messages_path_once() {
        assert_eq!(
            messages_url("https://baas.example.com/"),
            "https://baas.example.com/openapi/v1/messages"
        );
    }

    #[test]
    fn build_message_body_includes_metadata_when_configured() {
        let metadata = serde_json::json!({
            "title": "BCS service callback",
            "bot_options": {
                "lifecycle_stage": "online"
            },
            "sender_options": {
                "from": "owner"
            }
        });

        let body = build_message_body("default:151614", "callback done", Some(&metadata), None);

        assert_eq!(body["bot_id"], "default:151614");
        assert_eq!(body["message"], "callback done");
        assert_eq!(body["metadata"]["title"], "BCS service callback");
        assert_eq!(body["metadata"]["bot_options"]["lifecycle_stage"], "online");
        assert_eq!(body["metadata"]["sender_options"]["from"], "owner");
    }

    #[test]
    fn build_message_body_uses_dynamic_baas_session_id_from_extra() {
        let metadata = serde_json::json!({
            "title": "BCS service callback",
            "session_id": "agent:main:static-session"
        });
        let extra = serde_json::json!({
            "callback_target": {
                "baas_session_id": "agent:main:dynamic-session"
            }
        });

        let body = build_message_body(
            "default:151614",
            "callback done",
            Some(&metadata),
            Some(&extra),
        );

        assert_eq!(body["metadata"]["title"], "BCS service callback");
        assert_eq!(body["metadata"]["session_id"], "agent:main:dynamic-session");
    }

    #[tokio::test]
    async fn send_rejects_private_base_url_before_request() {
        let channel = CallbackChannelConfig::Baas {
            base_url: "http://127.0.0.1:1".to_string(),
            api_key: "sk-test".to_string(),
            bot_id: "default:callback-test".to_string(),
            metadata: None,
        };

        let err = send(&channel, "hello", None)
            .await
            .expect_err("private BaaS callback base_url should be rejected");

        assert!(err.contains("baas callback URL is not allowed"));
    }

    #[test]
    fn parse_accept_response_accepts_code_zero() {
        let accepted = parse_accept_response(
            r#"{"code":0,"message":"success","data":{"message_id":"message-1","session_id":"agent:main:s1"},"trace_id":"trace-1"}"#,
        )
        .expect("code zero should be accepted");

        assert_eq!(
            accepted,
            BaasAcceptedMessage {
                trace_id: "trace-1".to_string(),
                message_id: "message-1".to_string(),
                session_id: "agent:main:s1".to_string(),
            }
        );
    }

    #[test]
    fn parse_accept_response_rejects_business_error() {
        let err = parse_accept_response(
            r#"{"code":60001,"message":"bot is unavailable","trace_id":"trace-failed"}"#,
        )
        .expect_err("business error should fail callback");

        assert!(err.contains("code 60001"), "{err}");
        assert!(err.contains("bot is unavailable"), "{err}");
        assert!(err.contains("trace-failed"), "{err}");
    }
}
