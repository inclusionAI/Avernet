use std::collections::HashSet;
use std::time::Duration;

use anyhow::{Context, Result};
use futures::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, info, warn};

use crate::{config::GroupLoggerConfig, dedup::DedupStore, handler};

const GATEWAY_URL: &str = "https://api.dingtalk.com/v1.0/gateway/connections/open";
const RECONNECT_INITIAL: Duration = Duration::from_secs(1);
const RECONNECT_MAX: Duration = Duration::from_secs(60);

pub async fn run_listener(config: GroupLoggerConfig, group_ids: HashSet<String>, dedup: DedupStore) {
    let mut attempt: u32 = 0;
    loop {
        match run_connection(&config, &group_ids, &dedup).await {
            Ok(()) => {
                attempt = 0;
            }
            Err(e) => {
                warn!(attempt, "DingTalk connection error: {e:#}");
            }
        }

        let delay = reconnect_delay(attempt);
        warn!(
            delay_secs = delay.as_secs(),
            attempt,
            "DingTalk WebSocket closed, reconnecting"
        );
        tokio::time::sleep(delay).await;
        attempt = attempt.saturating_add(1);
    }
}

fn reconnect_delay(attempt: u32) -> Duration {
    let secs = RECONNECT_INITIAL.as_secs()
        * 2u64.saturating_pow(attempt.min(6));
    Duration::from_secs(secs.min(RECONNECT_MAX.as_secs()))
}

async fn run_connection(config: &GroupLoggerConfig, group_ids: &HashSet<String>, dedup: &DedupStore) -> Result<()> {
    let (endpoint, ticket) = register_connection(&config.client_id, &config.client_secret).await?;

    let ws_url = format!("{}?ticket={}", endpoint, ticket);
    debug!(%ws_url, "Connecting to DingTalk WebSocket");

    let (ws_stream, _) = connect_async(&ws_url)
        .await
        .context("failed to connect to WebSocket")?;

    let (mut write, mut read) = ws_stream.split();
    info!("DingTalk group logger connected");

    while let Some(msg) = read.next().await {
        let msg = match msg {
            Ok(m) => m,
            Err(e) => {
                warn!(error = %e, "WebSocket error");
                break;
            }
        };

        if let Message::Text(text) = msg {
            let frame: Value = match serde_json::from_str(&text) {
                Ok(v) => v,
                Err(e) => {
                    warn!(error = %e, "Failed to parse frame");
                    continue;
                }
            };

            let frame_type = frame.get("type").and_then(|v| v.as_str()).unwrap_or("");

            match frame_type {
                "SYSTEM" => {
                    let topic = frame
                        .pointer("/headers/topic")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if topic == "disconnect" {
                        info!("Received disconnect frame, reconnecting");
                        break;
                    }
                    debug!(topic, "SYSTEM ping received, sending pong");
                    let pong = json!({
                        "code": 200,
                        "headers": frame.get("headers").cloned().unwrap_or(json!({})),
                        "message": "OK",
                        "data": "OK"
                    });
                    if let Err(e) = write.send(Message::Text(pong.to_string().into())).await {
                        warn!(error = %e, "Failed to send pong");
                        break;
                    }
                }
                "EVENT" | "CALLBACK" => {
                    if let Err(e) = handler::handle_frame(&frame, group_ids, dedup) {
                        warn!(error = %e, "Failed to handle frame");
                    }
                    send_ack(&frame, &mut write).await?;
                }
                _ => {
                    debug!(frame_type, "Unknown frame type");
                }
            }
        }
    }

    Ok(())
}

async fn register_connection(client_id: &str, client_secret: &str) -> Result<(String, String)> {
    let client = reqwest::Client::new();
    let body = json!({
        "clientId": client_id,
        "clientSecret": client_secret,
        "subscriptions": [
            {"type": "EVENT", "topic": "*"},
            {"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}
        ]
    });

    let resp = client
        .post(GATEWAY_URL)
        .json(&body)
        .send()
        .await
        .context("failed to register gateway connection")?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        anyhow::bail!("gateway registration failed: {} - {}", status, text);
    }

    let data: Value = resp.json().await.context("failed to parse gateway response")?;

    let endpoint = data
        .get("endpoint")
        .and_then(|v| v.as_str())
        .context("missing endpoint")?
        .to_string();
    let ticket = data
        .get("ticket")
        .and_then(|v| v.as_str())
        .context("missing ticket")?
        .to_string();

    Ok((endpoint, ticket))
}

async fn send_ack(
    frame: &Value,
    write: &mut futures::stream::SplitSink<
        tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>,
        Message,
    >,
) -> Result<()> {
    let message_id = frame
        .pointer("/headers/messageId")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    let ack = json!({
        "code": 200,
        "messageId": message_id,
        "headers": frame.get("headers").cloned().unwrap_or(json!({})),
        "message": "OK",
        "data": {"messageId": message_id}
    });

    write
        .send(Message::Text(ack.to_string().into()))
        .await
        .context("failed to send ACK")
}
