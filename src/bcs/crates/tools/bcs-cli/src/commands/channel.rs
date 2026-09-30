//! Channel command execution.
use super::*;

pub(super) async fn execute_channel(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Channel { token, command } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, cli_json, .. } = command_context;

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

                    if cli_json {
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

                    if cli_json {
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

                    if cli_json {
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

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Channel binding deleted: {}", id);
                    }
                }
            }

    Ok(())
}
