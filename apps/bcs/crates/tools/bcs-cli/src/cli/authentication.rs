use std::collections::HashMap;
use std::ops::ControlFlow;
use std::path::Path;

use anyhow::{Result, anyhow};
use bcs_cli::{BcsClient, oauth};
use tracing::{info, warn};

use super::agentpass;
use super::environment::{NetworkEnv, detect_network_env, load_optional_session_info};
use super::output::{StructuredResult, classify_auth_error_message, emit_structured_result};

use super::{AUTH_VIA_AGENT_PASS, CLI_LOG_TARGET};

/// Resolve OAuth2 client credentials: env var override > compiled-in defaults.
pub(super) fn resolve_oauth_credentials() -> (String, String) {
    let client_id = std::env::var("BCS_OAUTH_CLIENT_ID")
        .unwrap_or_else(|_| oauth::default_oauth_client_id().to_string());
    let client_secret = std::env::var("BCS_OAUTH_CLIENT_SECRET")
        .unwrap_or_else(|_| oauth::default_oauth_client_secret().to_string());
    (client_id, client_secret)
}

/// Create a BCS client with optional cookie and OAuth headers.
/// An empty `token` means no bot token is available; the client is built
/// without Bearer auth (cookie / OAuth headers, if any, still apply).
pub(super) fn create_client(
    bcs_url: &str,
    token: &str,
    cookie: Option<&str>,
    oauth_headers: Option<&HashMap<String, String>>,
) -> BcsClient {
    let mut client = if let Some(oauth_headers) = oauth_headers {
        let mut client = if token.is_empty() {
            let mut c = BcsClient::new(bcs_url);
            c.set_oauth_headers(oauth_headers.clone());
            c
        } else {
            BcsClient::with_token_and_oauth(bcs_url, token, oauth_headers.clone())
        };
        if let Some(cookie) = cookie {
            client.set_cookie(cookie);
        }
        client
    } else if token.is_empty() {
        let mut client = BcsClient::new(bcs_url);
        if let Some(cookie) = cookie {
            client.set_cookie(cookie);
        }
        client
    } else if let Some(cookie) = cookie {
        BcsClient::with_token_and_cookie(bcs_url, token, cookie)
    } else {
        BcsClient::with_token(bcs_url, token)
    };
    client.set_actor_id(bcs_cli::CallerContext::from_env_with_token(Some(token)).expect("validated caller context").actor_id()).expect("validated Actor");
    client.set_client_identity(format!("bcs-cli/{}", env!("CARGO_PKG_VERSION")));
    if let Ok(traceparent) = std::env::var("TRACEPARENT") {
        if let Err(error) = client.set_traceparent(traceparent.trim()) {
            warn!(target: CLI_LOG_TARGET, error = %error, "Ignoring invalid TRACEPARENT from tool environment");
        }
    }
    client
}

/// Resolve the network environment and OAuth headers for the CLI run.
///
/// Returns `Continue((network_env, oauth_headers))` after resolving network
/// environment and OAuth headers. A handled structured-mode authentication
/// failure is reported to stdout and returns `Break(())` so the caller can
/// finish the run without continuing to command dispatch.
pub(super) async fn prepare_authentication(
    bcs_url: &str,
    debug: bool,
    structured_mode: bool,
    log_file: &Path,
) -> Result<ControlFlow<(), (NetworkEnv, Option<HashMap<String, String>>)>> {
    let prepared = if bcs_url
        .is_empty()
    {
        (NetworkEnv::Prod, None)
    } else {
        let env = match std::env::var("tc_sdb_nenv").ok().as_deref() {
            Some("production") => NetworkEnv::Prod,
            Some(_) => NetworkEnv::Office,
            None => detect_network_env(&bcs_url, structured_mode).await,
        };
        if debug {
            eprintln!("\x1b[2m[→BCS] Network: {:?}\x1b[0m", env);
        }

        let headers = if env == NetworkEnv::Office {
            if AUTH_VIA_AGENT_PASS {
                let err_msg = if let Ok(bot_data_dir) = std::env::var("BOT_DATA_DIR") {
                    let bot_data_path = Path::new(&bot_data_dir);

                    if let Ok(Some(session)) = load_optional_session_info() {
                        if let Some(ref bot_uuid) = session.bot_uuid {
                            if !session.token.is_empty() && !bot_uuid.is_empty() {
                                let summary = agentpass::load_bot_summary(bot_data_path);

                                info!(target: CLI_LOG_TARGET, "尝试 AgentPass 注册，bot_uuid: {}", bot_uuid);

                                match agentpass::try_register_and_auth(
                                    &session.token,
                                    bot_uuid,
                                    &summary,
                                    structured_mode,
                                )
                                .await
                                {
                                    Ok(()) => {
                                        info!(target: CLI_LOG_TARGET, "AgentPass 注册流程完成");
                                        None
                                    }
                                    Err(e) => Some(format!("AgentPass 注册失败: {}", e)),
                                }
                            } else {
                                Some(
                                    "AgentPass auth failed: token or bot_uuid is empty".to_string(),
                                )
                            }
                        } else {
                            Some("AgentPass auth failed: session has no bot_uuid".to_string())
                        }
                    } else {
                        Some("AgentPass auth failed: no session info found (check BOT_DATA_DIR/.bcs/session.json)".to_string())
                    }
                } else {
                    Some("AgentPass auth failed: BOT_DATA_DIR not set".to_string())
                };

                if let Some(msg) = err_msg {
                    if structured_mode {
                        emit_structured_result(&StructuredResult {
                            status: "agentpass_auth_failed".to_string(),
                            message: Some(msg),
                            network_env: Some("office".to_string()),
                            auth_url: None,
                            timeout_secs: None,
                            log_file: Some(log_file.display().to_string()),
                        });
                        return Ok(ControlFlow::Break(()));
                    } else {
                        return Err(anyhow!(msg));
                    }
                }

                info!(target: CLI_LOG_TARGET, "auth_via_agent_pass enabled, skipping OAuth");
                None
            } else {
                info!(target: CLI_LOG_TARGET, "Office network detected, obtaining OAuth2 authentication...");
                let (client_id, client_secret) = resolve_oauth_credentials();
                let log_file_str = log_file.display().to_string();
                let on_auth_required: Option<oauth::AuthRequiredCallback> = if structured_mode {
                    Some(Box::new(move |auth_url: &str| {
                        emit_structured_result(&StructuredResult {
                            status: "auth_required".to_string(),
                            message: Some("OAuth2 authorization required. Waiting for browser authorization...".to_string()),
                            network_env: Some("office".to_string()),
                            auth_url: Some(auth_url.to_string()),
                            timeout_secs: Some(120),
                            log_file: Some(log_file_str),
                        });
                        use std::io::Write;
                        let _ = std::io::stdout().flush();
                    }))
                } else {
                    None
                };
                match oauth::try_get_oauth_headers(client_id, client_secret, on_auth_required).await
                {
                    Ok(headers) => Some(headers),
                    Err(e) => {
                        if structured_mode {
                            emit_structured_result(&StructuredResult {
                                status: classify_auth_error_message(&e.message).to_string(),
                                message: Some(e.message),
                                network_env: Some("office".to_string()),
                                auth_url: e.auth_url,
                                timeout_secs: Some(120),
                                log_file: Some(log_file.display().to_string()),
                            });
                            return Ok(ControlFlow::Break(()));
                        }
                        return Err(e.into());
                    }
                }
            }
        } else {
            None
        };
        (env, headers)
    };

    Ok(ControlFlow::Continue(prepared))
}
