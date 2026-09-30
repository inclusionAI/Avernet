//! Friend command execution.
use super::*;

pub(super) async fn execute_friend(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Friend { token, command } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, cli_json, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                FriendCommands::Request { bot_uuid } => {
                    debug_request!(
                        debug,
                        "POST",
                        "/friends/request",
                        json!({ "to_bot": &bot_uuid })
                    );

                    let result = client.send_friend_request(None, &bot_uuid).await?;

                    if cli_json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({
                                "success": result.success,
                                "data": result.data,
                                "message": result.message,
                            }))?
                        );
                    } else if result.success {
                        if let Some(ref msg) = result.message {
                            println!("✓ {}", msg);
                        } else {
                            println!("✓ Friend request sent to {}", bot_uuid);
                            if let Some(ref data) = result.data {
                                if let Some(id) = data.get("id").and_then(|v| v.as_str()) {
                                    println!("  Request ID: {}", id);
                                }
                            }
                        }
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::Accept { request_id } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/friends/requests/{}/accept", &request_id),
                        json!({})
                    );

                    let result = client.accept_friend_request(&request_id).await?;

                    if cli_json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({ "success": result.success }))?
                        );
                    } else if result.success {
                        println!("✓ Friend request accepted");
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::Reject { request_id } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/friends/requests/{}/reject", &request_id),
                        json!({})
                    );

                    let result = client.reject_friend_request(&request_id).await?;

                    if cli_json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({ "success": result.success }))?
                        );
                    } else if result.success {
                        println!("✓ Friend request rejected");
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::List { bot_uuid } => {
                    let my_bot_uuid = match bot_uuid {
                        Some(id) => id,
                        None => resolve_my_bot_uuid()?,
                    };

                    debug_request!(
                        debug,
                        "GET",
                        &format!("/bots/{}/friends", &my_bot_uuid),
                        json!({})
                    );

                    let result = client.list_friends(&my_bot_uuid).await?;

                    if cli_json {
                        println!("{}", serde_json::to_string(&result.data)?);
                    } else if let Some(data) = result.data {
                        if let Some(friends) = data.as_array() {
                            println!("Friends ({}):", friends.len());
                            for friend in friends {
                                let uuid = friend
                                    .get("bot_uuid")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("?");
                                let name = friend
                                    .get("name")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("unnamed");
                                let online = friend
                                    .get("is_online")
                                    .and_then(|v| v.as_bool())
                                    .unwrap_or(false);
                                let status_icon = if online { "🟢" } else { "⚪" };
                                println!("  {} {} ({})", status_icon, name, uuid);
                            }
                        } else {
                            println!("No friends found.");
                        }
                    }
                }

                FriendCommands::Requests { direction, status } => {
                    debug_request!(
                        debug,
                        "GET",
                        "/friends/requests",
                        json!({
                            "direction": &direction,
                            "status": &status
                        })
                    );

                    let result = client
                        .list_friend_requests(None, Some(&direction), status.as_deref())
                        .await?;

                    if cli_json {
                        println!("{}", serde_json::to_string(&result.data)?);
                    } else if let Some(data) = result.data {
                        if let Some(requests) = data.as_array() {
                            println!("Friend requests ({}):", requests.len());
                            for req in requests {
                                let id = req.get("id").and_then(|v| v.as_str()).unwrap_or("?");
                                let from =
                                    req.get("from_bot").and_then(|v| v.as_str()).unwrap_or("?");
                                let to = req.get("to_bot").and_then(|v| v.as_str()).unwrap_or("?");
                                let req_status =
                                    req.get("status").and_then(|v| v.as_str()).unwrap_or("?");
                                println!("  {} → {} [{}] (id: {})", from, to, req_status, id);
                            }
                        } else {
                            println!("No friend requests found.");
                        }
                    }
                }
            }

    Ok(())
}
