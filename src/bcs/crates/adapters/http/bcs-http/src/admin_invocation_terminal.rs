use std::{net::IpAddr, sync::Arc};

use bcs_protocol::BCN_PROVIDER_ID_HEADER;
use bcs_route_security::{OutboundUrlError, OutboundUrlGuard};
use bcs_service_api::{BotTerminalEvent, BotTerminalObserverPort, BotTerminalState};
use serde_json::json;
use tracing::{info, warn};

use crate::state::{AdminInvocationRun, AdminInvocationStore};

#[derive(Clone)]
pub struct AdminInvocationTerminalObserver {
    runs: Arc<AdminInvocationStore>,
    outbound_url_guard: OutboundUrlGuard,
}

impl AdminInvocationTerminalObserver {
    pub fn new(runs: Arc<AdminInvocationStore>, outbound_url_guard: OutboundUrlGuard) -> Self {
        Self {
            runs,
            outbound_url_guard,
        }
    }

    pub fn notify(&self, run_id: &str, state: BotTerminalState, text: &str) {
        let Some(run) = self.runs.claim_callback(run_id) else {
            return;
        };
        self.dispatch(run_id, run, state, text);
    }

    fn dispatch(&self, run_id: &str, run: AdminInvocationRun, state: BotTerminalState, text: &str) {
        let Some(callback) = run.callback else {
            return;
        };
        let body = if state == BotTerminalState::Final {
            json!({ "run_id": run_id, "provider_id": run.provider_id, "status": "completed", "message": { "role": "assistant", "content": [{ "type": "text", "text": text }] } })
        } else {
            json!({ "run_id": run_id, "provider_id": run.provider_id, "status": "failed", "error": { "code": "ADMIN_INVOCATION_TARGET_FAILED", "message": text } })
        };
        let provider_id = run.provider_id;
        let run_id = run_id.to_string();
        let outbound_url_guard = self.outbound_url_guard.clone();
        tokio::spawn(async move {
            let callback_url = callback_url_for_log(&callback.url);
            let guarded_url = match outbound_url_guard
                .resolve_request_http_url(&callback.url)
                .await
            {
                Ok(url) => url,
                Err(error) => {
                    warn!(
                        run_id = %run_id,
                        provider_id = %provider_id,
                        callback_url = %callback_url,
                        resolved_ip = ?callback_blocked_ip(&error),
                        reason = %error,
                        "organization admin terminal callback blocked by outbound URL policy"
                    );
                    return;
                }
            };
            let mut client_builder = reqwest::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none());
            if let Some((host, addresses)) = guarded_url.dns_override() {
                client_builder = client_builder.resolve_to_addrs(host, addresses);
            }
            let client = match client_builder.build() {
                Ok(client) => client,
                Err(error) => {
                    warn!(
                        run_id = %run_id,
                        provider_id = %provider_id,
                        callback_url = %callback_url,
                        error = %error,
                        "organization admin terminal callback client creation failed"
                    );
                    return;
                }
            };
            let response = client
                .post(guarded_url.as_str())
                .header("content-type", "application/json")
                .header(BCN_PROVIDER_ID_HEADER, provider_id)
                .bearer_auth(callback.bearer_token)
                .json(&body)
                .send()
                .await;
            match response {
                Ok(response) if response.status().is_success() => {
                    info!(run_id = %run_id, "organization admin terminal callback acknowledged")
                }
                Ok(response) => {
                    warn!(run_id = %run_id, status = %response.status(), "organization admin terminal callback was not acknowledged")
                }
                Err(error) => {
                    warn!(run_id = %run_id, error = %error, "organization admin terminal callback failed")
                }
            }
        });
    }
}

#[async_trait::async_trait]
impl BotTerminalObserverPort for AdminInvocationTerminalObserver {
    async fn observe(&self, event: BotTerminalEvent) {
        let Some(run) = self
            .runs
            .claim_callback_for_bot(&event.run_id, &event.bot_uuid)
        else {
            return;
        };
        self.dispatch(&event.run_id, run, event.state, &event.text);
    }
}

pub(crate) fn callback_blocked_ip(error: &OutboundUrlError) -> Option<IpAddr> {
    match error {
        OutboundUrlError::UnsafeAddress(address) => Some(*address),
        _ => None,
    }
}

pub(crate) fn callback_url_for_log(callback_url: &str) -> String {
    let Ok(mut url) = reqwest::Url::parse(callback_url) else {
        return "<invalid callback URL>".to_string();
    };
    let _ = url.set_username("");
    let _ = url.set_password(None);
    url.set_query(None);
    url.set_fragment(None);
    url.to_string()
}

#[cfg(test)]
mod tests {
    use std::time::{SystemTime, UNIX_EPOCH};

    use bcs_service_api::BotTerminalObserverPort;
    use bcs_test_support::contract::port::bot_terminal_observer_port_contract_tests;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::time::{Duration, timeout};

    use super::*;
    use crate::state::{AdminInvocationCallback, AdminInvocationRun};

    fn admin_run(callback_url: String) -> AdminInvocationRun {
        AdminInvocationRun {
            provider_id: "admin-provider".to_string(),
            organization_code: "org-1".to_string(),
            target_bot_uuid: "target-bot".to_string(),
            session_id: "session-1".to_string(),
            detach: false,
            expires_at_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_millis() as u64
                + 60_000,
            delivery_error: None,
            callback: Some(AdminInvocationCallback {
                url: callback_url,
                bearer_token: "callback-token".to_string(),
            }),
            callback_claimed: false,
        }
    }

    async fn read_http_request(socket: &mut tokio::net::TcpStream) -> Vec<u8> {
        let mut request = Vec::new();
        loop {
            let mut chunk = [0; 1024];
            let count = socket.read(&mut chunk).await.unwrap();
            assert!(count > 0, "callback connection closed before request completed");
            request.extend_from_slice(&chunk[..count]);
            let Some(headers_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
            else {
                continue;
            };
            let headers_end = headers_end + 4;
            let headers = String::from_utf8_lossy(&request[..headers_end]);
            let content_length = headers
                .lines()
                .find_map(|line| {
                    line.split_once(':').and_then(|(name, value)| {
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().unwrap())
                    })
                })
                .unwrap_or(0);
            if request.len() >= headers_end + content_length {
                return request;
            }
        }
    }

    #[tokio::test]
    async fn matching_bot_terminal_dispatches_callback_once() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let callback_url = format!("http://{}/callback", listener.local_addr().unwrap());
        let runs = Arc::new(AdminInvocationStore::default());
        runs.insert("run-1".to_string(), admin_run(callback_url));
        let observer = AdminInvocationTerminalObserver::new(
            runs,
            OutboundUrlGuard::allowing_private_networks_for_tests(),
        );
        let event = BotTerminalEvent {
            run_id: "run-1".to_string(),
            bot_uuid: "target-bot".to_string(),
            state: BotTerminalState::Final,
            text: "websocket result".to_string(),
        };

        bot_terminal_observer_port_contract_tests(&observer, event.clone(), async {
            let (mut socket, _) = timeout(Duration::from_secs(2), listener.accept())
                .await
                .unwrap()
                .unwrap();
            let request = timeout(Duration::from_secs(2), read_http_request(&mut socket))
                .await
                .unwrap();
            socket
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
                .await
                .unwrap();
            let request = String::from_utf8_lossy(&request);
            request.contains("x-bcn-provider-id: admin-provider")
                && request.contains("authorization: Bearer callback-token")
                && request.contains("websocket result")
        })
        .await;

        observer.observe(event).await;
        assert!(
            timeout(Duration::from_millis(100), listener.accept())
                .await
                .is_err()
        );
    }

    #[tokio::test]
    async fn mismatched_bot_does_not_claim_callback() {
        let runs = Arc::new(AdminInvocationStore::default());
        runs.insert(
            "run-1".to_string(),
            admin_run("http://127.0.0.1:1/callback".to_string()),
        );
        let observer = AdminInvocationTerminalObserver::new(
            runs.clone(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
        );

        observer
            .observe(BotTerminalEvent {
                run_id: "run-1".to_string(),
                bot_uuid: "different-bot".to_string(),
                state: BotTerminalState::Final,
                text: "spoofed".to_string(),
            })
            .await;

        assert!(runs.claim_callback_for_bot("run-1", "target-bot").is_some());
    }
}
