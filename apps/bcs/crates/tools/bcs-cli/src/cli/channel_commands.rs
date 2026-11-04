use std::collections::HashMap;

use anyhow::{Result, anyhow};
use serde_json::json;

use super::resource_args::ChannelCommands;
use super::Commands;
use super::authentication::create_client;
use super::environment::get_token;

pub(super) fn build_channel_bind_payload(command: &ChannelCommands) -> Result<serde_json::Value> {
    let ChannelCommands::Bind {
        account,
        target_kind,
        target_id,
        group_chat_scope,
        visibility,
        env,
        robot_code,
        client_id,
        client_secret,
        send_mode,
        card_template_id,
        message_type,
    } = command
    else {
        return Err(anyhow!("channel bind payload requires bind command"));
    };

    let target = match target_kind.as_str() {
        "group" => json!({ "group": { "group_id": target_id } }),
        "bot" => json!({ "bot": { "bot_id": target_id } }),
        other => {
            return Err(anyhow!(
                "unsupported target-kind {}; expected group or bot",
                other
            ));
        }
    };

    let send_mode = match send_mode.as_str() {
        "normal" => json!({
            "mode": "normal",
            "message_type": message_type,
        }),
        "streaming_card" => {
            let Some(card_template_id) = card_template_id.as_deref() else {
                return Err(anyhow!(
                    "--card-template-id is required when --send-mode=streaming_card"
                ));
            };
            json!({
                "mode": "streaming_card",
                "card_template_id": card_template_id,
                "fallback_message_type": message_type,
            })
        }
        other => {
            return Err(anyhow!(
                "unsupported send-mode {}; expected normal or streaming_card",
                other
            ));
        }
    };

    // channel_type MUST be the registry spelling "dingtalk": bcs-http routes,
    // the bcs-channel inbound join (find_active_by_account), and bcs-cli's own
    // ConversationId by-session queries all key on that exact string.
    // ChannelType is a plain String with no aliasing, so any other spelling
    // stores a binding that inbound messages can never hit (BindingNotFound)
    // and that our own channel conversation listing cannot see.
    let mut payload = json!({
        "channel_type": "dingtalk",
        "account_ref": account,
        "target": target,
        "outbound_visibility": visibility,
        "env": env,
        "config": {
            "channel_type": "dingtalk",
            "robot_code": robot_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "send_mode": send_mode,
        }
    });

    if let Some(group_chat_scope) = group_chat_scope {
        payload["group_chat_scope"] = json!(group_chat_scope);
    }

    Ok(payload)
}

pub(super) fn redact_channel_bind_debug_payload(payload: &serde_json::Value) -> serde_json::Value {
    let mut redacted = payload.clone();
    if let Some(config) = redacted
        .get_mut("config")
        .and_then(|value| value.as_object_mut())
    {
        if config.contains_key("client_secret") {
            config.insert("client_secret".to_string(), json!("<redacted>"));
        }
    }
    redacted
}

/// Execute a channel binding command.
pub(super) async fn execute(
    command: Commands,
    bcs_url: String,
    bcs_cookie: Option<String>,
    oauth_headers: Option<HashMap<String, String>>,
    json: bool,
    debug: bool,
) -> Result<()> {
    match command {
        Commands::Channel { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match &command {
                ChannelCommands::Bind { .. } => {
                    let payload = build_channel_bind_payload(&command)?;
                    debug_request!(
                        debug,
                        "POST",
                        "/channels/bindings",
                        redact_channel_bind_debug_payload(&payload)
                    );

                    let result = client.create_channel_binding(&payload).await?;

                    debug_response!(debug, "200", &result);

                    if json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Channel binding created");
                        if let Some(id) = result.get("id").and_then(|value| value.as_str()) {
                            println!("  ID: {}", id);
                        }
                        if let Some(account) =
                            result.get("account_ref").and_then(|value| value.as_str())
                        {
                            println!("  Account: {}", account);
                        }
                    }
                }

                ChannelCommands::List => {
                    debug_request!(debug, "GET", "/channels/bindings", json!({}));

                    let result = client.list_channel_bindings().await?;

                    debug_response!(debug, "200", &result);

                    if json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else if let Some(items) =
                        result.get("items").and_then(|value| value.as_array())
                    {
                        println!("Channel bindings ({}):", items.len());
                        for item in items {
                            let id = item
                                .get("id")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            let account = item
                                .get("account_ref")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            let status = item
                                .get("status")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            println!("  {} {} [{}]", id, account, status);
                        }
                    } else {
                        println!("{}", serde_json::to_string_pretty(&result)?);
                    }
                }

                ChannelCommands::ConversationId { session } => {
                    debug_request!(
                        debug,
                        "GET",
                        "/channels/conversations/by-session",
                        json!({
                            "bcs_session_id": session,
                            "channel_type": "dingtalk"
                        })
                    );

                    let result = client
                        .list_channel_conversations_by_session(session, "dingtalk")
                        .await?;

                    debug_response!(debug, "200", &result);

                    if json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else if let Some(items) =
                        result.get("items").and_then(|value| value.as_array())
                    {
                        println!(
                            "DingTalk conversations for session {} ({}):",
                            session,
                            items.len()
                        );
                        for item in items {
                            let conversation_id = item
                                .get("conversation_id")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            let binding_id = item
                                .get("binding_id")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            let session_scope = item
                                .get("session_scope")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            println!(
                                "  {} (binding: {}, scope: {})",
                                conversation_id, binding_id, session_scope
                            );
                        }
                    } else {
                        println!("{}", serde_json::to_string_pretty(&result)?);
                    }
                }

                ChannelCommands::Unbind { id } => {
                    debug_request!(
                        debug,
                        "DELETE",
                        &format!("/channels/bindings/{}", id),
                        json!({})
                    );

                    let result = client.delete_channel_binding(id).await?;

                    debug_response!(debug, "200", &result);

                    if json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Channel binding deleted: {}", id);
                    }
                }
            }
        }

        _ => unreachable!("wrong command family"),
    }

    Ok(())
}
