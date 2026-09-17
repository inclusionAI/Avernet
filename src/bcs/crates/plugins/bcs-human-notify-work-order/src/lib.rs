//! Work-order human mention notification backend for BCS.
//!
//! Delivers each @-human mention as a backend work-order NOTICE event
//! (`HUMAN_GROUP_MENTIONED`) so it lands in the user's work-order inbox.
//! BCS mints a short-lived user principal (shared HS256 gateway key) for
//! every mentioned human and posts it as the `x-avernet-principal` header;
//! the internal endpoint verifies the signature and derives the acting user.

pub const WORK_ORDER_BACKEND_NAME: &str = "work_order";

use async_trait::async_trait;
use bcs_config_api::HumanNotifyProviderConfig;
use bcs_human_notify_api::{
    HumanMentionNotifier, HumanMentionNotifierFactory, HumanNotifyError, HumanNotifyResult,
    MentionNotification,
};
use futures::future::BoxFuture;
use secrecy::{ExposeSecret, Secret};
use serde::Serialize;
use std::collections::BTreeMap;
use std::sync::Arc;

const DEFAULT_EVENT_PATH: &str = "/api/v1/work-orders/events";
const PRINCIPAL_TTL_SECS: u64 = 60;
const PRINCIPAL_ISSUER: &str = "bcs";
const PRINCIPAL_AUDIENCE: &str = "backend";
const PRINCIPAL_KEY_ID: &str = "bare";
const MAX_CONTENT_CHARS: usize = 200;
/// Per-request budget so one slow backend response cannot consume the shared
/// 10s adapter timeout before the remaining recipients are attempted.
const PER_REQUEST_TIMEOUT_SECS: u64 = 5;

/// Parsed options of the `[[human_notify.providers]]` entry with
/// `name = "work_order"`.
#[derive(Debug, Clone)]
struct WorkOrderNotifierOptions {
    backend_base_url: reqwest::Url,
    event_path: String,
    signing_key: Secret<String>,
}

impl WorkOrderNotifierOptions {
    fn event_url(&self) -> reqwest::Url {
        self.backend_base_url
            .join(&self.event_path)
            .expect("valid base url joins any path")
    }
}

fn required_option(
    options: &BTreeMap<String, serde_json::Value>,
    key: &str,
) -> Result<String, HumanNotifyError> {
    let literal = options
        .get(key)
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if let Some(value) = literal {
        return Ok(value.to_string());
    }
    // 与 dingtalk 后端一致的提示：`<key>_secret` 是 Mist 引用，必须在
    // 启动期 resolve_config_secrets 解析成 `<key>` 明文后才能构建后端。
    let reference = format!("{key}_secret");
    if options
        .get(&reference)
        .and_then(|value| value.as_str())
        .is_some_and(|value| !value.trim().is_empty())
    {
        return Err(HumanNotifyError::Config(format!(
            "{reference} is a Mist key reference and must be resolved before building the work_order notifier"
        )));
    }
    Err(HumanNotifyError::Config(format!(
        "work_order notifier option '{key}' must be a non-empty string"
    )))
}

fn parse_options(options: &BTreeMap<String, serde_json::Value>) -> Result<WorkOrderNotifierOptions, HumanNotifyError> {
    let backend_base_url = required_option(options, "backend_base_url")?;
    let backend_base_url = reqwest::Url::parse(&backend_base_url)
        .map_err(|error| {
            HumanNotifyError::Config(format!("invalid backend_base_url: {error}"))
        })?;
    if !matches!(backend_base_url.scheme(), "http" | "https") {
        return Err(HumanNotifyError::Config(format!(
            "backend_base_url must use http or https: {backend_base_url}"
        )));
    }
    let event_path = options
        .get("event_path")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| DEFAULT_EVENT_PATH.to_string());
    let signing_key = required_option(options, "signing_key")?;
    Ok(WorkOrderNotifierOptions {
        backend_base_url,
        event_path,
        signing_key: Secret::new(signing_key),
    })
}

/// Gateway-principal claim set for one mentioned human. Kept as a separate
/// struct so the JWT shape matches the contract fixture
/// `api-contracts/v1/gateway-principal/principal-set.json`.
#[derive(Debug, Serialize)]
struct PrincipalClaims {
    iss: &'static str,
    aud: &'static str,
    iat: u64,
    exp: u64,
    principals: [serde_json::Value; 1],
}

fn mint_user_principal_claims(staff_no: &str, username: &str, now_unix: u64) -> PrincipalClaims {
    PrincipalClaims {
        iss: PRINCIPAL_ISSUER,
        aud: PRINCIPAL_AUDIENCE,
        iat: now_unix,
        exp: now_unix + PRINCIPAL_TTL_SECS,
        principals: [serde_json::json!({
            "type": "user",
            "subject": {
                "id": staff_no,
                "username": username,
            }
        })],
    }
}

fn mint_user_principal_jwt(
    signing_key: &Secret<String>,
    staff_no: &str,
    username: &str,
    now_unix: u64,
) -> Result<String, HumanNotifyError> {
    // 独立于 claims 结构的纯函数 JWT 签发（见 mint_user_principal_claims）
    let mut header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256);
    header.kid = Some(PRINCIPAL_KEY_ID.to_string());
    let claims = mint_user_principal_claims(staff_no, username, now_unix);
    jsonwebtoken::encode(
        &header,
        &claims,
        &jsonwebtoken::EncodingKey::from_secret(signing_key.expose_secret().as_bytes()),
    )
    .map_err(|error| {
        HumanNotifyError::Config(format!("failed to mint user principal: {error}"))
    })
}

#[derive(Debug, Serialize)]
struct WorkOrderNoticeEventRequest {
    event_category: &'static str,
    biz_type: &'static str,
    biz_id: String,
    event_type: &'static str,
    recipient_user_ids: Vec<String>,
    title: String,
    content: serde_json::Value,
    biz_data: serde_json::Value,
}

/// UTF-8 安全截断（char_indices，遵守仓库编码规范）。
fn truncate_chars(value: &str, max_chars: usize) -> String {
    match value.char_indices().nth(max_chars) {
        Some((idx, _)) => format!("{}…", &value[..idx]),
        None => value.to_string(),
    }
}

fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

fn build_notice_event(
    notification: &MentionNotification,
    staff_no: &str,
) -> WorkOrderNoticeEventRequest {
    let text = truncate_chars(
        &format!("{}: {}", notification.sender_label, notification.message_text),
        MAX_CONTENT_CHARS,
    );
    let biz_id = if notification.session_id.is_empty() {
        notification.group_id.clone()
    } else {
        notification.session_id.clone()
    };
    WorkOrderNoticeEventRequest {
        event_category: "NOTICE",
        biz_type: "GROUP_MENTION",
        biz_id,
        event_type: "HUMAN_GROUP_MENTIONED",
        recipient_user_ids: vec![staff_no.to_string()],
        title: "你被 @ 了".to_string(),
        content: serde_json::json!({ "text": text }),
        biz_data: serde_json::json!({
            "group_id": notification.group_id,
            "session_id": notification.session_id,
            "sender_actor_id": notification.sender_actor_id,
        }),
    }
}

/// Work-order backend: one NOTICE event per mentioned human.
pub struct WorkOrderMentionNotifier {
    client: reqwest::Client,
    options: WorkOrderNotifierOptions,
}

impl WorkOrderMentionNotifier {
    async fn deliver_one(
        &self,
        notification: &MentionNotification,
        human: &bcs_human_notify_api::MentionedHuman,
    ) -> Result<(), String> {
        let Some(staff_no) = human
            .actor_id
            .strip_prefix("human_")
            .filter(|staff_no| !staff_no.is_empty())
        else {
            // 非 human actor id：跳过，不计失败（插件契约）。
            return Ok(());
        };
        let username = if human.display_name.trim().is_empty() {
            human.actor_id.as_str()
        } else {
            human.display_name.as_str()
        };
        let url = self.options.event_url();
        let url = url.as_str();
        let principal = mint_user_principal_jwt(
            &self.options.signing_key,
            staff_no,
            username,
            now_unix(),
        )
        .map_err(|error| error.to_string())?;
        let payload = build_notice_event(notification, staff_no);
        let response = self
            .client
            .post(url)
            .header("x-avernet-principal", principal)
            .json(&payload)
            .send()
            .await;
        match response {
            Ok(response) if response.status().is_success() => {
                tracing::info!(
                    backend = WORK_ORDER_BACKEND_NAME,
                    staff_no,
                    group_id = %notification.group_id,
                    session_id = %notification.session_id,
                    "work-order mention notice delivered"
                );
                Ok(())
            }
            Ok(response) => {
                let status = response.status();
                let body = response.text().await.unwrap_or_default();
                tracing::warn!(
                    backend = WORK_ORDER_BACKEND_NAME,
                    staff_no,
                    group_id = %notification.group_id,
                    status = %status,
                    response_body = %body,
                    "work-order mention notice rejected"
                );
                Err(format!("work-order event for {staff_no} returned {status}: {body}"))
            }
            Err(error) => {
                tracing::warn!(
                    backend = WORK_ORDER_BACKEND_NAME,
                    staff_no,
                    group_id = %notification.group_id,
                    error = %error,
                    "work-order notice request failed"
                );
                Err(format!("work-order event for {staff_no} failed: {error}"))
            }
        }
    }
}

#[async_trait]
impl HumanMentionNotifier for WorkOrderMentionNotifier {
    fn backend_name(&self) -> &'static str {
        WORK_ORDER_BACKEND_NAME
    }

    async fn notify(&self, notification: &MentionNotification) -> HumanNotifyResult<()> {
        let mut attempted = 0usize;
        let mut failures: Vec<String> = Vec::new();
        for human in &notification.mentioned {
            if !human
                .actor_id
                .strip_prefix("human_")
                .is_some_and(|staff_no| !staff_no.is_empty())
            {
                continue;
            }
            attempted += 1;
            if let Err(failure) = self.deliver_one(notification, human).await {
                failures.push(failure);
            }
        }
        // 插件契约：无收件人/全部跳过/至少一人成功 -> Ok；全部失败 -> Err。
        if attempted == 0 || failures.len() < attempted {
            Ok(())
        } else {
            Err(HumanNotifyError::Delivery(format!(
                "work-order mention delivery failed for all {attempted} recipients: {}",
                failures.join("; ")
            )))
        }
    }
}

pub fn build_work_order_notifier(
    config: HumanNotifyProviderConfig,
) -> BoxFuture<'static, HumanNotifyResult<Arc<dyn HumanMentionNotifier>>> {
    Box::pin(async move {
        let options = parse_options(&config.options)?;
        // Delivery is serial per recipient inside one `notify()` call, which is
        // wrapped by the adapter's 10s total timeout; cap each request so one
        // slow backend response cannot starve the remaining recipients.
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(PER_REQUEST_TIMEOUT_SECS))
            .build()
            .map_err(|error| {
                HumanNotifyError::Config(format!("failed to build work-order http client: {error}"))
            })?;
        Ok(Arc::new(WorkOrderMentionNotifier {
            client,
            options,
        }) as Arc<dyn HumanMentionNotifier>)
    })
}

inventory::submit! {
    HumanMentionNotifierFactory {
        name: WORK_ORDER_BACKEND_NAME,
        build: build_work_order_notifier,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    fn options(entries: &[(&str, &str)]) -> BTreeMap<String, serde_json::Value> {
        entries
            .iter()
            .map(|(key, value)| ((*key).to_string(), serde_json::Value::String((*value).to_string())))
            .collect()
    }

    fn sample_notification() -> MentionNotification {
        MentionNotification {
            session_id: "group-1:s1".to_string(),
            group_id: "group-1".to_string(),
            sender_actor_id: "bot-driver".to_string(),
            sender_label: "张三".to_string(),
            mentioned: vec![
                bcs_human_notify_api::MentionedHuman {
                    actor_id: "human_447147".to_string(),
                    display_name: "李四".to_string(),
                },
            ],
            message_text: "你好".to_string(),
            timestamp_ms: 1_700_000_000_000,
        }
    }

    #[test]
    fn parse_options_requires_backend_base_url_and_signing_key() {
        let error = parse_options(&options(&[])).expect_err("empty options must fail");
        assert!(error.to_string().contains("backend_base_url"));

        // 只有 base url、signing_key 完全缺失：走兜底 Config 错。
        let only_base = options(&[("backend_base_url", "http://127.0.0.1:8080")]);
        let error = parse_options(&only_base).expect_err("missing signing key must fail");
        assert!(error.to_string().contains("signing_key"));

        // 有未解析的 `signing_key_secret` 引用而无明文：必须报 Mist 引用错
        //（与 dingtalk 后端同模式）。注意：只有引用键存在时才会走这个分支，
        // 缺失时走上面的兜底分支。
        let unresolved = options(&[
            ("backend_base_url", "http://127.0.0.1:8080"),
            (
                "signing_key_secret",
                "other_manual_teamclawgw_principal_signing_key",
            ),
        ]);
        let error =
            parse_options(&unresolved).expect_err("unresolved reference must fail");
        assert!(error.to_string().contains("signing_key_secret"));
        assert!(error.to_string().contains("Mist key reference"));
    }

    #[test]
    fn parse_options_rejects_bad_urls_and_defaults_event_path() {
        let entries = vec![
            ("backend_base_url", "ftp://backend"),
            ("signing_key", "k"),
        ];
        assert!(parse_options(&options(&entries)).is_err());

        let parsed = parse_options(&options(&[
            ("backend_base_url", "http://127.0.0.1:8080/api/"),
            ("signing_key", "k"),
        ]))
        .expect("valid options parse");
        assert_eq!(
            parsed.event_url().as_str(),
            "http://127.0.0.1:8080/api/v1/work-orders/events"
        );

        let parsed = parse_options(&options(&[
            ("backend_base_url", "http://127.0.0.1:8080"),
            ("signing_key", "k"),
            ("event_path", "/custom/events"),
        ]))
        .expect("custom path parses");
        assert_eq!(parsed.event_url().as_str(), "http://127.0.0.1:8080/custom/events");
    }

    #[derive(serde::Deserialize)]
    struct TestClaims {
        principals: serde_json::Value,
    }

    #[test]
    fn minted_principal_matches_gateway_claim_contract() {
        let claims = mint_user_principal_claims("447147", "李四", 1_700_000_000);
        assert_eq!(claims.iss, "bcs");
        assert_eq!(claims.aud, "backend");
        assert_eq!(claims.exp, 1_700_000_000 + 60);

        let signing_key = Secret::new("test-key".to_string());
        let token = mint_user_principal_jwt(&signing_key, "447147", "李四", 1_700_000_000)
            .expect("token signs");
        let mut validation = jsonwebtoken::Validation::new(jsonwebtoken::Algorithm::HS256);
        validation.validate_exp = false;
        validation.validate_aud = false;
        validation.required_spec_claims.clear();
        let decoded = jsonwebtoken::decode::<TestClaims>(
            &token,
            &jsonwebtoken::DecodingKey::from_secret(b"test-key"),
            &validation,
        )
        .expect("token verifies with the shared key");
        assert_eq!(decoded.header.kid.as_deref(), Some("bare"));
        assert!(decoded
            .claims
            .principals
            .get(0)
            .and_then(|p| p.get("type"))
            .and_then(|v| v.as_str())
            .is_some_and(|v| v == "user"));
        assert_eq!(
            decoded
                .claims
                .principals
                .get(0)
                .and_then(|p| p.get("subject"))
                .and_then(|s| s.get("id"))
                .and_then(|v| v.as_str()),
            Some("447147")
        );
        assert_eq!(
            decoded
                .claims
                .principals
                .get(0)
                .and_then(|p| p.get("subject"))
                .and_then(|s| s.get("username"))
                .and_then(|v| v.as_str()),
            Some("李四")
        );
    }

    #[test]
    fn notice_event_payload_uses_contract_constants() {
        let payload = build_notice_event(&sample_notification(), "447147");
        assert_eq!(payload.event_category, "NOTICE");
        assert_eq!(payload.biz_type, "GROUP_MENTION");
        assert_eq!(payload.biz_id, "group-1:s1");
        assert_eq!(payload.event_type, "HUMAN_GROUP_MENTIONED");
        assert_eq!(payload.recipient_user_ids, vec!["447147".to_string()]);
        assert_eq!(payload.title, "你被 @ 了");
        assert_eq!(
            payload.content,
            serde_json::json!({ "text": "张三: 你好" })
        );
        assert_eq!(
            payload.biz_data,
            serde_json::json!({
                "group_id": "group-1",
                "session_id": "group-1:s1",
                "sender_actor_id": "bot-driver",
            })
        );
    }

    #[test]
    fn group_only_message_uses_group_id_as_biz_id() {
        let mut notification = sample_notification();
        notification.session_id = String::new();
        let payload = build_notice_event(&notification, "447147");
        assert_eq!(payload.biz_id, "group-1");
    }

    #[test]
    fn content_truncates_on_char_boundary() {
        let mut notification = sample_notification();
        notification.message_text = " boundary测试边界安全截断boundary测试边界安全截断".repeat(20);
        let payload = build_notice_event(&notification, "447147");
        let text = payload
            .content
            .get("text")
            .and_then(|v| v.as_str())
            .expect("text content");
        // 截断函数在边界字符数上附加省略号，所以上限是 MAX_CONTENT_CHARS + 1。
        assert!(text.chars().count() <= MAX_CONTENT_CHARS + 1);
        // 中间字节截断会造成 UTF-8 panic；断言能安全取 chars 即证明边界安全。
        let _ = text.chars().last();
    }

    async fn read_http_request(stream: &mut tokio::net::TcpStream) -> String {
        let mut buf: Vec<u8> = Vec::new();
        let mut chunk = [0u8; 4096];
        let header_end = loop {
            let n = stream.read(&mut chunk).await.expect("read");
            assert!(n > 0, "client closed before sending the request");
            buf.extend_from_slice(&chunk[..n]);
            if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                break pos;
            }
        };
        let headers = String::from_utf8_lossy(&buf[..header_end]).to_string();
        let content_length: usize = headers
            .lines()
            .filter_map(|line| line.split_once(':'))
            .find(|(name, _)| name.trim().eq_ignore_ascii_case("content-length"))
            .and_then(|(_, value)| value.trim().parse().ok())
            .unwrap_or(0);
        let body_start = header_end + 4;
        while buf.len() < body_start + content_length {
            let n = stream.read(&mut chunk).await.expect("read body");
            buf.extend_from_slice(&chunk[..n]);
        }
        stream
            .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nok")
            .await
            .expect("write response");
        String::from_utf8_lossy(&buf).into_owned()
    }

    fn notifier(base_url: String) -> WorkOrderMentionNotifier {
        WorkOrderMentionNotifier {
            client: reqwest::Client::builder()
                .no_proxy()
                .timeout(std::time::Duration::from_millis(200))
                .build()
                .expect("test client"),
            options: parse_options(&options(&[
                ("backend_base_url", &base_url),
                ("signing_key", "test-key"),
            ]))
            .expect("options parse"),
        }
    }

    #[tokio::test]
    async fn notify_skips_non_human_actor_ids_without_http() {
        let mut notification = sample_notification();
        notification.mentioned = vec![bcs_human_notify_api::MentionedHuman {
            actor_id: "bot-driver".to_string(),
            display_name: "张三".to_string(),
        }];
        // 127.0.0.1:9 无监听：若发起 HTTP 必失败，Got Ok 证明被跳过。
        let port = notifier("http://127.0.0.1:9".to_string());
        port.notify(&notification).await.expect("skipped means ok");
    }

    #[tokio::test]
    async fn notify_all_recipients_failed_is_delivery_error() {
        let mut notification = sample_notification();
        notification.mentioned = vec![
            bcs_human_notify_api::MentionedHuman {
                actor_id: "human_1".to_string(),
                display_name: "一".to_string(),
            },
            bcs_human_notify_api::MentionedHuman {
                actor_id: "human_2".to_string(),
                display_name: "二".to_string(),
            },
        ];
        let port = notifier("http://127.0.0.1:9".to_string());
        let error = port.notify(&notification).await.expect_err("all failed");
        assert!(matches!(error, HumanNotifyError::Delivery(_)));
    }

    #[tokio::test]
    async fn notify_partial_success_is_ok_and_payload_carries_principal() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            read_http_request(&mut stream).await
        });
        let port = notifier(format!("http://{addr}"));
        let mut notification = sample_notification();
        notification.mentioned = vec![
            bcs_human_notify_api::MentionedHuman {
                actor_id: "human_447147".to_string(),
                display_name: "李四".to_string(),
            },
            bcs_human_notify_api::MentionedHuman {
                actor_id: "human_2".to_string(),
                display_name: "二".to_string(),
            },
        ];

        // 第一个收件人由 one-shot server 接住；第二个在 server 关闭后连接
        // 被拒 —— 部分成功仍应返回 Ok。
        port.notify(&notification).await.expect("partial success is ok");

        let request = server.await.unwrap();
        assert!(request.starts_with("POST /api/v1/work-orders/events HTTP/1.1"));
        assert!(request.contains("x-avernet-principal: "));
        assert!(request.contains("HUMAN_GROUP_MENTIONED"));
        assert!(request.contains("GROUP_MENTION"));
        assert!(request.contains("447147"));
        // Authorization / Cookie 头绝不能出现
        assert!(!request.to_ascii_lowercase().contains("authorization:"));
        assert!(!request.to_ascii_lowercase().contains("cookie:"));
    }
}
