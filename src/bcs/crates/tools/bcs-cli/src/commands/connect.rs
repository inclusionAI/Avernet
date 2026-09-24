//! Connect command execution.
use super::*;

pub(super) async fn execute_connect(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Connect { token, bot_id } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, structured_mode, .. } = command_context;

            let mut client = BcsClient::new(&bcs_url);
            if let Some(ref cookie) = bcs_cookie {
                client.set_cookie(cookie);
            }
            if let Some(ref headers) = oauth_headers {
                client.set_oauth_headers(headers.clone());
            }

            // Auto-discover existing session token to avoid duplicate registration.
            // Priority: explicit --token > BCN_BOT_TOKEN env > session.json
            let resolved_token =
                token
                    .or_else(|| {
                        std::env::var("BCN_BOT_TOKEN")
                            .ok()
                            .filter(|t| !t.is_empty())
                    })
                    .or_else(|| {
                        get_optional_session_file_path()
                        .and_then(|p| load_session_info_from_path(&p).ok().flatten())
                        .and_then(|s| if s.token.is_empty() { None } else {
                            info!("Found existing session token from session file, will reconnect");
                            Some(s.token)
                        })
                    });

            // If we found a session token and no bot_id override, warn the user
            // that this bot is already registered (will reconnect instead of creating new).
            let already_registered = resolved_token.is_some() && bot_id.is_none();

            let params = BotConnectParams {
                token: resolved_token,
                bot_id,
                protocol_version: Some(BCS_PROTOCOL_VERSION),
                client_kind: None,
            };

            debug_request!(
                debug,
                "POST",
                "/bots/connect",
                json!({
                    "token": params.token.as_ref().map(|_| "***"),
                    "bot_id": &params.bot_id
                })
            );

            let result = client.connect(params).await?;

            // Save session.json for subsequent commands
            {
                let session_bcs_url =
                    normalize_bcs_ws_url(&bcs_url).unwrap_or_else(|| bcs_url.clone());
                let session = SessionInfo {
                    bot_uuid: Some(result.bot_uuid.clone()),
                    token: result.token.clone(),
                    bcs_url: Some(session_bcs_url),
                    api_base_url: Some(bcs_url.clone()),
                };
                let session_path = get_optional_session_file_path();
                if let Some(path) = session_path {
                    if let Some(parent) = path.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    if let Err(e) = std::fs::write(
                        &path,
                        serde_json::to_string_pretty(&session).unwrap_or_default(),
                    ) {
                        eprintln!("Warning: failed to save session to {:?}: {}", path, e);
                    } else {
                        debug!("Session saved to {:?}", path);
                    }
                }
            }

            debug_response!(
                debug,
                "200",
                json!({
                    "is_new": result.is_new,
                    "bot_uuid": &result.bot_uuid
                })
            );

            if structured_mode {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                if already_registered && !result.is_new {
                    println!("ℹ Bot already registered, reconnecting:");
                } else if result.is_new {
                    println!("✓ New bot connected to BCS network:");
                } else {
                    println!("✓ Bot reconnected to BCS network:");
                }
                println!("  Bot UUID: {}", result.bot_uuid);
                println!("  Token: {}...", &result.token[..8.min(result.token.len())]);
                if result.is_new {
                    println!("\n  Save this token for reconnection!");
                }
            }

    Ok(())
}
